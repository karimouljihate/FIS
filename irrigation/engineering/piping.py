"""AI piping structure generator.

Builds a complete hierarchical irrigation piping network for a project:

    Basin / Well (source)
        -> Main Pipe (MP)  : minimum-spanning-tree over the source + all
                             sector centroids, serving every sector.
        -> Zone Pipes (ZP) : from the MP to every zone centroid (nearest tap).
        -> Laterals / Drip : from the network to every tree row, so that
                             every tree is served without exception.

The generator:

* sizes every PVC segment (diameter) from its flow and a velocity limit,
* optimises total pipe length (MST for the MP, nearest-point taps for the
  ZP and for the drip lines),
* starts the hydraulic calculation at the water source and walks the tree
  downhill/uphill using terrain elevation (gravity aware),
* places master / sector / zone valves and diameter reductions,
* writes a bilingual report to the ``piping_reports`` collection.

Public entry point: :func:`generate_piping_structure`.
"""
import math
from datetime import datetime
from collections import defaultdict

from shapely.geometry import shape, Point, LineString
import pyproj

from irrigation.extensions import mongo
from irrigation.models import get_next_id
from irrigation.engineering.hydraulics import (
    ENGINEERING_DEFAULTS,
    recommended_diameter_mm,
    hazen_williams_head_loss_m,
    pipe_velocity_mps,
    head_m_from_pressure_bar,
    distributed_friction_loss_m,
)
from irrigation.engineering.projection import (
    wgs84,
    get_utm_crs,
    get_project_centroid,
)


STRUCTURAL_TYPES = [
    'main_pipe', 'sub_pipe', 'dripline',
    'master_valve', 'sector_valve', 'zone_valve', 'reduction',
]

_TYPE_EN = {
    'main_pipe': 'Main Pipe',
    'sub_pipe': 'Pipe',
    'dripline': 'Drip Line',
    'master_valve': 'Master Valve',
    'sector_valve': 'Sector Valve',
    'zone_valve': 'Zone Valve',
    'reduction': 'Reduction',
    'check_valve': 'Check Valve',
    'air_release': 'Air Release',
}
_TYPE_AR = {
    'main_pipe': 'أنبوب رئيسي',
    'sub_pipe': 'أنبوب',
    'dripline': 'خط تنقيط',
    'master_valve': 'صمام رئيسي',
    'sector_valve': 'صمام قطاع',
    'zone_valve': 'صمام منطقة',
    'reduction': 'تقليل قطر',
    'check_valve': 'صمام عدم رجوع',
    'air_release': 'تنفيس الهواء',
}


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _polyline_length(coords):
    return sum(_dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _split_polyline(coords, d):
    """Split a polyline at distance ``d``; returns (left, right).

    Either side may be ``None`` when the split point coincides with an end.
    """
    total = _polyline_length(coords)
    if d <= 1e-6:
        return None, list(coords)
    if d >= total - 1e-6:
        return list(coords), None

    acc = 0.0
    left = [coords[0]]
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        seg = _dist(a, b)
        if acc + seg >= d - 1e-9:
            t = 0.0 if seg == 0 else (d - acc) / seg
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            left.append(p)
            right = [p] + coords[i + 1:]
            return left, right
        left.append(b)
        acc += seg
    return list(coords), None


# --------------------------------------------------------------------------
# generator
# --------------------------------------------------------------------------

class _PipeNetwork:
    """Mutable working structure for the generated network (UTM meters)."""

    def __init__(self, transformer, config, standards, options,
                 elevation_lookup, elevation_range):
        self.transformer = transformer
        self.config = config
        self.standards = standards
        self.options = options
        self.elevation_lookup = elevation_lookup
        self.elevation_range = elevation_range or 0.0

        self.nodes = {}        # id -> {x, y, z, kind, sector_id, zone_id}
        self.edges = []        # graph edges (mp, zp, stub)
        self.attachments = []  # row connections (list of edge chains)
        self._next_node = 1
        self._pipe_seq = 0

    # -- nodes ------------------------------------------------------------
    def add_node(self, x, y, kind, sector_id=None, zone_id=None, z=None):
        nid = self._next_node
        self._next_node += 1
        if z is None:
            if self.elevation_lookup is not None:
                z = self.elevation_lookup(Point(x, y)) or 0.0
            else:
                z = 0.0
        self.nodes[nid] = {
            'x': x, 'y': y, 'z': z, 'kind': kind,
            'sector_id': sector_id, 'zone_id': zone_id,
        }
        return nid

    def _pt(self, nid):
        n = self.nodes[nid]
        return (n['x'], n['y'])

    # -- edges ------------------------------------------------------------
    def add_edge(self, a, b, kind, elem_type, vmax, floor_d,
                 coords=None, zone_id=None, row_id=None, sector_id=None,
                 distributed=False, emitter_count=0):
        coords = coords or [self._pt(a), self._pt(b)]
        e = {
            'a': a, 'b': b, 'kind': kind, 'type': elem_type,
            'vmax': vmax, 'floor_d': floor_d, 'coords': coords,
            'zone_id': zone_id, 'row_id': row_id, 'sector_id': sector_id,
            'distributed': distributed, 'emitter_count': emitter_count,
            'flow_lps': 0.0, 'diameter': floor_d,
            'length_m': _polyline_length(coords),
            'origin': self._pipe_seq, 'dist0': 0.0,
        }
        self._pipe_seq += 1
        self.edges.append(e)
        return e

    # -- attach a point to the closest network edge -----------------------
    def _nearest_edge_point(self, x, y, kinds=('mp', 'zp')):
        best = None  # (distance, edge, point, distance_along)
        for e in self.edges:
            if e['kind'] not in kinds:
                continue
            line = LineString(e['coords'])
            p = Point(x, y)
            dist = line.distance(p)
            if best is None or dist < best[0]:
                d_along = line.project(p)
                pt = line.interpolate(d_along)
                best = (dist, e, (pt.x, pt.y), d_along)
        return best

    def tap(self, x, y, kinds=('mp', 'zp')):
        """Attach to the network; returns the node id of the tap point."""
        best = self._nearest_edge_point(x, y, kinds)
        if best is None:
            # nothing to tap onto yet (isolated) -> new floating node
            return self.add_node(x, y, 'tap')
        _, edge, pt, d_along = best
        left, right = _split_polyline(edge['coords'], d_along)
        if left is None:
            return edge['a']
        if right is None:
            return edge['b']

        tap_id = self.add_node(pt[0], pt[1], 'tap')

        # mutate the original edge into its left half (a -> tap)
        orig_b = edge['b']
        orig_dist = edge['dist0']
        edge['b'] = tap_id
        edge['coords'] = left
        edge['length_m'] = _polyline_length(left)

        # create the right half (tap -> b)
        new_edge = dict(edge)
        new_edge['a'] = tap_id
        new_edge['b'] = orig_b
        new_edge['coords'] = right
        new_edge['length_m'] = _polyline_length(right)
        new_edge['dist0'] = orig_dist + d_along
        self.edges.append(new_edge)
        return tap_id

    # -- flows ------------------------------------------------------------
    def compute_graph_flows(self, source_id):
        adj = defaultdict(list)
        for e in self.edges:
            adj[e['a']].append(e)
            adj[e['b']].append(e)

        parent_edge = {}
        order = [source_id]
        seen = {source_id}
        i = 0
        while i < len(order):
            n = order[i]
            i += 1
            for e in adj[n]:
                other = e['b'] if e['a'] == n else e['a']
                if other in seen:
                    continue
                seen.add(other)
                parent_edge[other] = e
                order.append(other)

        # subtree sums of node demands
        subtree = {n: self.nodes[n].get('demand_lps', 0.0) for n in self.nodes}
        for n in reversed(order):
            pe = parent_edge.get(n)
            if pe is None:
                continue
            parent = pe['a'] if pe['b'] == n else pe['b']
            subtree[parent] = subtree.get(parent, 0.0) + subtree.get(n, 0.0)

        # flow on each edge = subtree on the side away from the source
        for e in self.edges:
            da = self._depth(e['a'], parent_edge, source_id)
            db = self._depth(e['b'], parent_edge, source_id)
            if da <= db:
                e['flow_lps'] = subtree.get(e['b'], 0.0)
            else:
                e['flow_lps'] = subtree.get(e['a'], 0.0)

    @staticmethod
    def _depth(n, parent_edge, source_id):
        d = 0
        while n != source_id and n in parent_edge:
            n = parent_edge[n]['a'] if parent_edge[n]['b'] == n else parent_edge[n]['b']
            d += 1
            if d > 10000:
                break
        return d

    # -- sizing / pressure ------------------------------------------------
    def size_edges(self):
        for e in self.edges + [x for chain in self.attachments for x in chain]:
            d = recommended_diameter_mm(e['flow_lps'] / 1000.0,
                                        e['vmax'], self.standards)
            e['diameter'] = max(d, e['floor_d'])

    def _loss(self, e):
        flow = e['flow_lps'] / 1000.0
        hf = hazen_williams_head_loss_m(
            flow, e['length_m'], e['diameter'], self.config['hazen_williams_c'])
        if e.get('distributed'):
            hf = distributed_friction_loss_m(hf, e.get('emitter_count', 0))
        return hf * (1.0 + self.config['minor_loss_factor'])

    def pressures(self, source_id, source_head_m):
        adj = defaultdict(list)
        for e in self.edges:
            adj[e['a']].append(e)
            adj[e['b']].append(e)

        H = {source_id: source_head_m}
        parent_edge = {}
        order = [source_id]
        i = 0
        while i < len(order):
            n = order[i]
            i += 1
            for e in adj[n]:
                other = e['b'] if e['a'] == n else e['a']
                if other in H:
                    continue
                H[other] = H[n] - self._loss(e)
                parent_edge[other] = e
                order.append(other)

        # row attachments hang off tap nodes
        row_states = []  # (chain, tap_node, H_end, path_edges)
        for chain in self.attachments:
            tap = chain[0]['a']
            H_end = H.get(tap)
            path = []
            if H_end is None:
                row_states.append((chain, tap, None, path))
                continue
            for e in chain:
                H_end -= self._loss(e)
                path.append(e)
            row_states.append((chain, tap, H_end, path))

        return H, parent_edge, order, row_states

    def bump_path(self, path_edges):
        """Increase the largest-loss edge on a path by one standard size."""
        worst = None
        worst_loss = -1.0
        for e in path_edges:
            loss = self._loss(e)
            if loss > worst_loss:
                worst_loss = loss
                worst = e
        if worst is None:
            return False
        for d in self.standards:
            if d > worst['diameter'] and d >= worst['floor_d']:
                worst['diameter'] = d
                return True
        return False


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def generate_piping_structure(project_id, project=None, options=None):
    """Generate the full AI piping structure for ``project_id``.

    Returns a bilingual report dict. Raises ``ValueError`` when a
    prerequisite is missing (no source / no sectors).
    """
    options = dict(options or {})
    opts = {
        'use_elevation': options.get('use_elevation', True),
        'replace': options.get('replace', True),
        'mode': options.get('mode', 'direct'),  # direct | lateral
        'source_pressure_bar': options.get('source_pressure_bar'),
        'source_flow_lpm': options.get('source_flow_lpm'),
    }

    if project is None:
        project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        raise ValueError('Project not found / المشروع غير موجود')

    config = ENGINEERING_DEFAULTS.copy()
    spec = project.get('spec') or {}
    for key in ('emitter_flow_lph', 'emitters_per_tree', 'emitter_spacing_m',
                'row_spacing_m', 'hazen_williams_c'):
        val = (spec.get('config') or {}).get(key)
        if val:
            config[key] = float(val)
    config['standard_diameters_mm'] = list(ENGINEERING_DEFAULTS['standard_diameters_mm'])

    # -- water sources ----------------------------------------------------
    primary, secondary = _resolve_sources(project)
    if not primary:
        raise ValueError(
            'No water source with coordinates found / '
            'لا يوجد مصدر مياه بإحداثيات محددة')

    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    if not sectors:
        raise ValueError(
            'No sectors found — generate sectors first / '
            'لا توجد قطاعات — أنشئ القطاعات أولاً')
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    trees = list(mongo.db.trees.find({'project_id': project_id}))

    lng, lat = get_project_centroid(project)
    utm_crs = get_utm_crs(lng, lat)
    transformer = pyproj.Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    inv_transformer = pyproj.Transformer.from_crs(utm_crs, wgs84, always_xy=True)

    def proj(lon, la):
        return transformer.transform(lon, la)

    def unproj(x, y):
        lon, la = inv_transformer.transform(x, y)
        return [lon, la]

    # -- elevation --------------------------------------------------------
    elevation_lookup, elevation_range, elev_warning = _build_elevation_lookup(
        project_id, project, opts['use_elevation'], proj)

    net = _PipeNetwork(transformer, config, config['standard_diameters_mm'],
                       opts, elevation_lookup, elevation_range)

    warnings = []
    if elev_warning:
        warnings.append(elev_warning)

    # -- source node ------------------------------------------------------
    src_x, src_y = proj(primary['lng'], primary['lat'])
    source_id = net.add_node(src_x, src_y, 'source')
    z_src = net.nodes[source_id]['z']

    source_pressure = opts['source_pressure_bar']
    if not source_pressure:
        source_pressure = config['source_pressure_bar']
    source_flow_lpm = opts['source_flow_lpm']
    if not source_flow_lpm:
        source_flow_lpm = primary.get('flow_lpm') or config['source_flow_lpm']

    # -- main pipe: MST over source + sector centroids --------------------
    sector_nodes = []
    for s in sectors:
        try:
            c = shape(s['polygon']).centroid
        except Exception:
            continue
        x, y = proj(c.x, c.y)
        nid = net.add_node(x, y, 'sector', sector_id=s['id'])
        sector_nodes.append((s, nid))

    mst_pairs = _minimum_spanning_tree(
        [(source_id, net.nodes[source_id]['x'], net.nodes[source_id]['y'])] +
        [(nid, net.nodes[nid]['x'], net.nodes[nid]['y']) for _, nid in sector_nodes])
    for a, b in mst_pairs:
        net.add_edge(a, b, 'mp', 'main_pipe',
                     config['main_max_velocity_mps'], 63)

    # -- zone pipes: nearest tap on the MP network ------------------------
    zone_nodes = {}
    for z in zones:
        try:
            c = shape(z['polygon']).centroid
        except Exception:
            continue
        x, y = proj(c.x, c.y)
        tap_id = net.tap(x, y, kinds=('mp',))
        znid = net.add_node(x, y, 'zone', sector_id=z.get('sector_id'), zone_id=z['id'])
        net.add_edge(tap_id, znid, 'zp', 'sub_pipe',
                     config['sub_max_velocity_mps'], 40,
                     zone_id=z['id'], sector_id=z.get('sector_id'))
        zone_nodes[z['id']] = znid

    # -- demand -----------------------------------------------------------
    q_tree_lps = config['emitter_flow_lph'] * config['emitters_per_tree'] / 3600.0
    total_trees = len(trees)
    trees_by_zone = defaultdict(int)
    for t in trees:
        trees_by_zone[t.get('zone_id')] += 1

    rows_by_zone = defaultdict(list)
    for r in rows:
        rows_by_zone[r.get('zone_id')].append(r)

    row_data = []  # (row, row_demand_lps, tree_count, emit_count, row_coords_utm)
    for r in rows:
        geom = r.get('geometry') or {}
        coords = geom.get('coordinates') or []
        if len(coords) < 2:
            continue
        utm = [proj(c[0], c[1]) for c in coords]
        tc = sum(1 for t in trees if t.get('row_id') == r['id'])
        if total_trees == 0:
            spacing = r.get('spacing_m') or config.get('row_spacing_m') or 3.0
            length = _polyline_length(utm)
            tc = max(1, int(length / spacing) + 1) if spacing else 1
        emit = max(2, tc * int(config['emitters_per_tree']))
        row_data.append((r, tc * q_tree_lps, tc, emit, utm, coords))

    # zone centroid demand feeds the MP / ZP flows
    for z in zones:
        znid = zone_nodes.get(z['id'])
        if znid is None:
            continue
        if total_trees > 0:
            demand = trees_by_zone.get(z['id'], 0) * q_tree_lps
        else:
            demand = sum(rd[1] for rd in row_data if rd[0].get('zone_id') == z['id'])
        net.nodes[znid]['demand_lps'] = demand

    # -- row connections (driplines / laterals) ---------------------------
    for r, row_demand, tc, emit, utm, wgs in row_data:
        tap_id = net.tap(utm[0][0], utm[0][1], kinds=('mp', 'zp'))
        if opts['mode'] == 'lateral':
            mid_id = net.add_node(utm[0][0], utm[0][1], 'rowend')
            lat = {
                'a': tap_id, 'b': mid_id, 'kind': 'lat', 'type': 'sub_pipe',
                'coords': [net._pt(tap_id), utm[0]], 'zone_id': None,
                'row_id': r['id'], 'sector_id': None,
                'flow_lps': row_demand, 'diameter': 40,
                'vmax': config['sub_max_velocity_mps'], 'floor_d': 40,
                'distributed': False, 'emitter_count': 0,
                'length_m': _dist(net._pt(tap_id), utm[0]),
            }
            end_id = net.add_node(utm[-1][0], utm[-1][1], 'rowend')
            drip_coords = [utm[0]] + utm[1:]
            drip = {
                'a': mid_id, 'b': end_id, 'kind': 'drip', 'type': 'dripline',
                'coords': drip_coords, 'zone_id': None, 'row_id': r['id'],
                'sector_id': None, 'flow_lps': row_demand,
                'diameter': config['drip_diameter_mm'],
                'vmax': config['drip_max_velocity_mps'],
                'floor_d': config['drip_diameter_mm'],
                'distributed': True, 'emitter_count': emit,
                'length_m': _polyline_length(drip_coords),
            }
            net.attachments.append([lat, drip])
        else:
            end_id = net.add_node(utm[-1][0], utm[-1][1], 'rowend')
            drip_coords = [net._pt(tap_id)] + list(utm)
            drip = {
                'a': tap_id, 'b': end_id, 'kind': 'drip', 'type': 'dripline',
                'coords': drip_coords, 'zone_id': None, 'row_id': r['id'],
                'sector_id': None, 'flow_lps': row_demand,
                'diameter': config['drip_diameter_mm'],
                'vmax': config['drip_max_velocity_mps'],
                'floor_d': config['drip_diameter_mm'],
                'distributed': True, 'emitter_count': emit,
                'length_m': _polyline_length(drip_coords),
            }
            net.attachments.append([drip])

    # -- secondary sources (stub + check valve) ---------------------------
    extra_elements = []  # (type, node_id / coords, properties)
    for s in secondary:
        x, y = proj(s['lng'], s['lat'])
        nearest = _nearest_node(net, x, y)
        sid = net.add_node(x, y, 'src2')
        rated = s.get('flow_lpm') or 0.0
        e = net.add_edge(nearest, sid, 'stub', 'main_pipe',
                         config['main_max_velocity_mps'], 63)
        e['flow_lps'] = rated / 60.0
        extra_elements.append(('check_valve', sid, {'generated': 'ai', 'source': s.get('name')}))

    # -- flows, sizing, pressure ------------------------------------------
    net.compute_graph_flows(source_id)

    net.size_edges()

    source_head = head_m_from_pressure_bar(source_pressure) + z_src
    min_p = config['min_emitter_pressure_bar']

    for _ in range(15):
        H, parent_edge, order, row_states = net.pressures(source_id, source_head)
        any_fail = False
        for chain, tap, H_end, path in row_states:
            if H_end is None:
                continue
            end_node = chain[-1]['b']
            p_end = (H_end - net.nodes[end_node]['z']) / 10.197
            if p_end < min_p:
                any_fail = True
                net.bump_path(_graph_path_edges(parent_edge, tap) + path)
        if not any_fail:
            break

    H, parent_edge, order, row_states = net.pressures(source_id, source_head)

    min_pressure_seen = None
    points_below_min = 0
    for chain, tap, H_end, path in row_states:
        if H_end is None:
            continue
        end_node = chain[-1]['b']
        p_end = (H_end - net.nodes[end_node]['z']) / 10.197
        if p_end < min_p:
            points_below_min += 1
        if min_pressure_seen is None or p_end < min_pressure_seen:
            min_pressure_seen = p_end

    if points_below_min:
        warnings.append(
            '%d emitter point(s) below the required pressure (%.1f bar) even after '
            'upsizing pipes — a booster pump is required / '
            '%d نقطة تنقيط أقل من الضغط المطلوب (%.1f بار) حتى بعد تكبير الأقطار — يلزم مضخة تعزيز'
            % (points_below_min, min_p, points_below_min, min_p))

    max_velocity = max(
        [pipe_velocity_mps(e['flow_lps'] / 1000.0, e['diameter'])
         for e in net.edges + [x for c in net.attachments for x in c]] or [0.0])

    # -- valves -----------------------------------------------------------
    master_valve = ('master_valve', source_id, {'generated': 'ai',
                                                'node': 'source'})
    valve_elements = [master_valve]
    for s, nid in sector_nodes:
        valve_elements.append(('sector_valve', nid, {'generated': 'ai',
                                                     'sector_id': s['id']}))
    for z in zones:
        znid = zone_nodes.get(z['id'])
        if znid is not None:
            valve_elements.append(('zone_valve', znid, {'generated': 'ai',
                                                        'zone_id': z['id']}))

    # -- reductions at diameter changes -----------------------------------
    reduction_specs = _collect_reductions(net, source_id, parent_edge)

    # -- air release at highest point -------------------------------------
    air_element = None
    if opts['use_elevation'] and net.elevation_range >= 3.0 and net.nodes:
        hi = max((n for n in net.nodes.values() if n['kind'] != 'src2'),
                 key=lambda n: n['z'], default=None)
        if hi is not None:
            air_element = (hi['x'], hi['y'])

    # -- persist ----------------------------------------------------------
    if opts['replace']:
        mongo.db.network_elements.delete_many({
            'project_id': project_id,
            '$or': [
                {'properties.generated': 'ai'},
                {'type': {'$in': STRUCTURAL_TYPES}},
            ],
        })

    report = _insert_elements(
        project_id, net, unproj, valve_elements, reduction_specs,
        air_element, extra_elements)

    coverage = _coverage(rows, row_data, zones, trees, total_trees)
    demand_lps = sum(n.get('demand_lps', 0.0) for n in net.nodes.values())
    util = (demand_lps / (source_flow_lpm / 60.0) * 100.0) if source_flow_lpm else 0.0
    if demand_lps > source_flow_lpm / 60.0:
        warnings.append(
            'Water demand %.1f L/min exceeds source capacity %.0f L/min / '
            'الطلب %.1f ل/دقيقة يتجاوز سعة المصدر %.0f ل/دقيقة'
            % (demand_lps * 60, source_flow_lpm, demand_lps * 60, source_flow_lpm))
    if coverage['zones_without_rows']:
        warnings.append(
            '%d zone(s) have no tree rows — draw rows and regenerate to serve '
            'all trees / %d منطقة بلا صفوف أشجار — ارسم الصفوف وأعد الإنشاء'
            % (len(coverage['zones_without_rows']),
               len(coverage['zones_without_rows'])))
    if total_trees == 0:
        warnings.append(
            'No trees generated yet — demand is estimated from row spacing / '
            'لم تُنشأ أشجار بعد — قُدّر الطلب من تباعد الصفوف')

    report.update({
        'project_id': project_id,
        'generated_at': datetime.utcnow(),
        'options': opts,
        'source': {
            'name': primary.get('name') or 'Water Source',
            'lng': primary['lng'], 'lat': primary['lat'],
            'pressure_bar': source_pressure,
            'flow_lpm': source_flow_lpm,
            'elevation_m': round(z_src, 2),
        },
        'elevation': {
            'used': bool(opts['use_elevation'] and net.elevation_lookup is not None),
            'range_m': round(net.elevation_range, 2),
        },
        'demand': {
            'total_lps': round(demand_lps, 3),
            'total_lpm': round(demand_lps * 60, 1),
            'source_flow_lpm': source_flow_lpm,
            'utilization_pct': round(util, 1),
        },
        'pressure': {
            'source_head_m': round(source_head, 2),
            'min_emitter_pressure_bar': (round(min_pressure_seen, 3)
                                         if min_pressure_seen is not None else None),
            'required_min_bar': min_p,
            'max_velocity_mps': round(max_velocity, 3),
        },
        'coverage': coverage,
        'warnings': warnings,
    })

    mongo.db.piping_reports.update_one(
        {'project_id': project_id},
        {'$set': report},
        upsert=True,
    )
    return report


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _resolve_sources(project):
    """Return (primary, secondary) water-source dicts with coordinates."""
    spec = project.get('spec') or {}
    raw = spec.get('water_sources') or []

    candidates = []
    for s in raw:
        if s.get('lat') is None or s.get('lng') is None:
            continue
        candidates.append({
            'name': s.get('name'),
            'lat': float(s['lat']),
            'lng': float(s['lng']),
            'flow_lpm': s.get('flow_lpm'),
            'type': s.get('type'),
            'elevation_m': None,
        })

    ws = project.get('water_source')
    primary = None
    if ws and ws.get('coordinates'):
        lon, la = ws['coordinates'][0], ws['coordinates'][1]
        primary = {'name': 'Water Source', 'lng': lon, 'lat': la,
                   'flow_lpm': None, 'type': None, 'elevation_m': None}
        # enrich from matching spec source
        for c in candidates:
            if abs(c['lng'] - lon) < 1e-6 and abs(c['lat'] - la) < 1e-6:
                primary.update({'name': c['name'] or primary['name'],
                                'flow_lpm': c['flow_lpm'],
                                'type': c['type']})
                candidates.remove(c)
                break

    if primary is None and candidates:
        primary = candidates.pop(0)

    secondary = candidates
    return primary, secondary


def _build_elevation_lookup(project_id, project, use_elevation, proj):
    """Return (lookup_callable, range_m, warning)."""
    if not use_elevation:
        return None, 0.0, None

    try:
        from irrigation.engineering.elevation import (
            get_or_build_elevation_model, get_elevation_at_point)
        model = get_or_build_elevation_model(project_id, project)
    except Exception:
        model = None

    if not model:
        return None, 0.0, (
            'Elevation data unavailable — network sized for flat terrain / '
            'بيانات الارتفاع غير متوفرة — تم التصميم لأرض مستوية')

    stats = model.get('stats') or {}
    elev_range = stats.get('range_m', 0.0)

    def lookup(point):
        return get_elevation_at_point(model, point)

    return lookup, elev_range, None


def _minimum_spanning_tree(points):
    """Prim's MST. ``points`` is a list of (id, x, y). Returns edge pairs."""
    if len(points) <= 1:
        return []
    in_tree = {points[0][0]}
    remaining = list(points[1:])
    edges = []
    while remaining:
        best = None
        for pid, px, py in remaining:
            for tid in in_tree:
                t = next(p for p in points if p[0] == tid)
                d = math.hypot(px - t[1], py - t[2])
                if best is None or d < best[0]:
                    best = (d, tid, pid)
        _, tid, pid = best
        edges.append((tid, pid))
        in_tree.add(pid)
        remaining = [p for p in remaining if p[0] != pid]
    return edges


def _nearest_node(net, x, y, kinds=('source', 'sector', 'zone', 'tap')):
    best = None
    for nid, n in net.nodes.items():
        if n['kind'] not in kinds:
            continue
        d = math.hypot(n['x'] - x, n['y'] - y)
        if best is None or d < best[0]:
            best = (d, nid)
    return best[1] if best else next(iter(net.nodes))


def _graph_path_edges(parent_edge, node):
    path = []
    seen = 0
    while node in parent_edge and seen < 10000:
        e = parent_edge[node]
        path.append(e)
        node = e['a'] if e['b'] == node else e['b']
        seen += 1
    return path


def _collect_reductions(net, source_id, parent_edge):
    """Find nodes where the parent diameter differs from a child diameter."""
    parent_map = dict(parent_edge)
    for chain in net.attachments:
        for e in chain:
            parent_map.setdefault(e['b'], e)

    incident = defaultdict(list)
    for e in net.edges:
        incident[e['a']].append(e)
        incident[e['b']].append(e)
    for chain in net.attachments:
        for e in chain:
            incident[e['a']].append(e)
            incident[e['b']].append(e)

    specs = []
    for nid in net.nodes:
        pe = parent_map.get(nid)
        if pe is None:
            continue
        parent_d = pe['diameter']
        child_ds = sorted({c['diameter'] for c in incident.get(nid, [])
                           if c is not pe and c['diameter'] < parent_d})
        for cd in child_ds:
            specs.append((nid, parent_d, cd))
    return specs


def _insert_elements(project_id, net, unproj, valve_elements,
                     reduction_specs, air_element, extra_elements):
    counters = defaultdict(int)
    lengths = defaultdict(float)
    diameters = defaultdict(set)

    def emit(elem_type, name_en, name_ar, geometry, properties):
        counters[elem_type] += 1
        mongo.db.network_elements.insert_one({
            'id': get_next_id('network_elements'),
            'project_id': project_id,
            'type': elem_type,
            'name_en': name_en,
            'name_ar': name_ar,
            'geometry': geometry,
            'properties': properties,
            'validated': False,
        })

    def pipe_name(kind):
        if kind == 'mp':
            return 'Main Pipe', 'الأنبوب الرئيسي'
        if kind == 'zp':
            return 'Zone Pipe', 'أنبوب المنطقة'
        if kind == 'lat':
            return 'Lateral Pipe', 'أنبوب فرعي'
        return 'Drip Line', 'خط التنقيط'

    def emit_pipe(e):
        kind = e['kind']
        base_en, base_ar = pipe_name(kind)
        counters[kind + '_edges'] += 1
        idx = counters[kind + '_edges']
        props = {
            'generated': 'ai',
            'diameter': e['diameter'],
            'flow_lps': round(e['flow_lps'], 4),
            'velocity_mps': round(pipe_velocity_mps(e['flow_lps'] / 1000.0,
                                                     e['diameter']), 3),
            'length_m': round(e['length_m'], 2),
        }
        if e.get('zone_id'):
            props['zone_id'] = e['zone_id']
        if e.get('row_id'):
            props['row_id'] = e['row_id']
        if kind == 'lat':
            props['role'] = 'lateral'

        coords = [unproj(x, y) for x, y in e['coords']]
        emit(e['type'], '%s %d' % (base_en, idx), '%s %d' % (base_ar, idx),
             {'type': 'LineString', 'coordinates': coords}, props)

        lengths[e['type']] += e['length_m']
        diameters[e['type']].add(e['diameter'])

    # graph edges (mp, zp, stub): merge tap-split fragments back into the
    # original pipes so each zone/main pipe is stored as a single element.
    merged_pipes = []
    by_origin = defaultdict(list)
    for e in net.edges:
        if e['kind'] == 'stub':
            continue
        by_origin[(e['type'], e['origin'])].append(e)
    for frags in by_origin.values():
        frags.sort(key=lambda f: f['dist0'])
        coords = [list(frags[0]['coords'][0])]
        for f in frags:
            for c in f['coords'][1:]:
                coords.append(list(c))
        rep = max(frags, key=lambda f: f['flow_lps'])
        merged_pipes.append({
            'type': frags[0]['type'], 'kind': frags[0]['kind'],
            'coords': coords,
            'length_m': _polyline_length(coords),
            'diameter': rep['diameter'],
            'flow_lps': rep['flow_lps'],
            'zone_id': frags[0].get('zone_id'),
            'row_id': frags[0].get('row_id'),
        })
    merged_pipes.sort(key=lambda p: p['type'])
    for p in merged_pipes:
        emit_pipe(p)

    # row attachments (driplines + laterals)
    for chain in net.attachments:
        for e in chain:
            emit_pipe(e)

    # valves
    for elem_type, node_id, props in valve_elements:
        n = net.nodes[node_id]
        idx = counters[elem_type] + 1
        emit(elem_type,
             '%s %d' % (_TYPE_EN[elem_type], idx),
             '%s %d' % (_TYPE_AR[elem_type], idx),
             {'type': 'Point', 'coordinates': unproj(n['x'], n['y'])},
             props)

    # reductions
    for i, (node_id, from_d, to_d) in enumerate(reduction_specs, 1):
        n = net.nodes[node_id]
        emit('reduction', 'Reduction %d (%d→%d)' % (i, from_d, to_d),
             'تقليل قطر %d (%d→%d)' % (i, from_d, to_d),
             {'type': 'Point', 'coordinates': unproj(n['x'], n['y'])},
             {'generated': 'ai', 'from_diameter': from_d, 'to_diameter': to_d})

    # air release
    if air_element:
        x, y = air_element
        emit('air_release', 'Air Release 1', 'تنفيس الهواء 1',
             {'type': 'Point', 'coordinates': unproj(x, y)},
             {'generated': 'ai'})

    # secondary sources / check valves
    for elem_type, node_id, props in extra_elements:
        n = net.nodes[node_id]
        idx = counters[elem_type] + 1
        emit(elem_type, '%s %d' % (_TYPE_EN[elem_type], idx),
             '%s %d' % (_TYPE_AR[elem_type], idx),
             {'type': 'Point', 'coordinates': unproj(n['x'], n['y'])}, props)

    # expose counters for the report
    counts = {
        'main_pipe': counters.get('mp_edges', 0),
        'zone_pipe': counters.get('zp_edges', 0),
        'lateral': counters.get('lat_edges', 0),
        'dripline': counters.get('drip_edges', 0),
        'master_valve': counters.get('master_valve', 0),
        'sector_valve': counters.get('sector_valve', 0),
        'zone_valve': counters.get('zone_valve', 0),
        'reduction': counters.get('reduction', 0),
        'air_release': counters.get('air_release', 0),
        'check_valve': counters.get('check_valve', 0),
    }
    counts['sub_pipe_total'] = counts['zone_pipe'] + counts['lateral']
    counts['total'] = sum(v for k, v in counts.items() if k not in
                          ('sub_pipe_total', 'total'))

    return {
        'counts': counts,
        'lengths_m': {k: round(v, 2) for k, v in lengths.items()},
        'diameter_schedules': {k: sorted(v) for k, v in diameters.items()},
    }


def _coverage(rows, row_data, zones, trees, total_trees):
    rows_served = len(row_data)
    served_row_ids = {rd[0]['id'] for rd in row_data}
    trees_served = sum(1 for t in trees if t.get('row_id') in served_row_ids)
    zone_ids_with_rows = {rd[0].get('zone_id') for rd in row_data}
    zones_without_rows = [z.get('name_en') or z.get('name_ar')
                          for z in zones if z['id'] not in zone_ids_with_rows]
    return {
        'rows_total': len(rows),
        'rows_served': rows_served,
        'trees_total': total_trees,
        'trees_served': trees_served,
        'zones_total': len(zones),
        'zones_served': len(zone_ids_with_rows),
        'zones_without_rows': zones_without_rows,
    }
