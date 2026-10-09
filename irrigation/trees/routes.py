from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id
import json
import math

trees_bp = Blueprint('trees', __name__)


def _principal_axis_angle(poly):
    """Angle (radians) of the dominant axis of a projected polygon exterior."""
    xs, ys = poly.exterior.xy
    xs = list(xs)[:-1]
    ys = list(ys)[:-1]
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov_xx = sum((x - mx) ** 2 for x in xs)
    cov_xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    cov_yy = sum((y - my) ** 2 for y in ys)
    return 0.5 * math.atan2(2 * cov_xy, cov_xx - cov_yy)


def _trace_rows_in_poly(poly, spacing_m):
    """Trace parallel rows inside a projected polygon along its dominant axis.

    Returns a list of ``(LineString, orientation_deg)`` pairs.
    """
    from shapely.geometry import LineString
    parts = poly.geoms if poly.geom_type == 'MultiPolygon' else [poly]
    rows = []
    for part in parts:
        angle = _principal_axis_angle(part)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        minx, miny, maxx, maxy = part.bounds
        corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
        tvals = [c[0] * nx + c[1] * ny for c in corners]
        tmin, tmax = min(tvals), max(tvals)
        half = math.hypot(maxx - minx, maxy - miny) * 2.0
        t = tmin
        while t <= tmax + 1e-9:
            line = LineString([
                (t * nx - half * ux, t * ny - half * uy),
                (t * nx + half * ux, t * ny + half * uy),
            ])
            inter = part.intersection(line)
            segs = inter.geoms if inter.geom_type in ('MultiLineString', 'GeometryCollection') else [inter]
            for seg in segs:
                if seg.geom_type == 'LineString' and seg.length >= max(spacing_m * 0.5, 1.0):
                    rows.append((seg, math.degrees(angle) % 180))
            t += spacing_m
    return rows


def get_project_or_redirect(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return None
    project['_id'] = str(project['_id'])
    return project


# === ROWS ===

@trees_bp.route('/<int:project_id>/rows')
@login_required
def rows_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])
    zone_by_id = {z['id']: z for z in zones}

    rows = list(mongo.db.tree_rows.find({'project_id': project_id}).sort('id', 1))
    for r in rows:
        r['_id'] = str(r['_id'])
        zz = zone_by_id.get(r.get('zone_id'))
        r['zone_name_en'] = zz['name_en'] if zz else f"Zone {r.get('zone_id')}"
        r['zone_name_ar'] = zz['name_ar'] if zz else ''

    return render_template('trees/rows.html', project=project, rows=rows)


@trees_bp.route('/<int:project_id>/rows/add', methods=['POST'])
@login_required
def row_add(project_id):
    zone_id = request.form.get('zone_id', type=int)
    coords = request.form.get('coordinates', '').strip()
    spacing = request.form.get('spacing', default=5, type=float)
    orientation = request.form.get('orientation', default=0, type=float)

    if not zone_id or not coords:
        flash('Zone and row path required / المنطقة ومسار الصف مطلوبان', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    count = mongo.db.tree_rows.count_documents({'project_id': project_id})
    row = {
        'id': get_next_id('tree_rows'),
        'project_id': project_id,
        'zone_id': zone_id,
        'name_en': f'Row {count+1}',
        'name_ar': f'صف {count+1}',
        'geometry': {'type': 'LineString', 'coordinates': coords_list},
        'spacing_m': spacing,
        'orientation': orientation,
        'validated': False
    }
    mongo.db.tree_rows.insert_one(row)
    flash('Row added / تمت إضافة الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/generate', methods=['POST'])
@login_required
def rows_generate(project_id):
    """Trace rows inside every zone at the given distance between rows."""
    spacing = request.form.get('spacing', default=5.0, type=float)
    replace = request.form.get('replace') in ('1', 'on', 'true')
    if spacing < 1:
        flash('Invalid row spacing / مسافة الصفوف غير صالحة', 'danger')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    if not zones:
        flash('No zones found. Create zones first / لا توجد مناطق. أنشئ المناطق أولاً', 'warning')
        return redirect(url_for('trees.rows_view', project_id=project_id))

    from irrigation.engineering.projection import projected_shape, unproject_shape, get_project_centroid
    project_doc = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project_doc) if project_doc else (0.0, 0.0)

    if replace:
        mongo.db.tree_rows.delete_many({'project_id': project_id})

    count = mongo.db.tree_rows.count_documents({'project_id': project_id})
    total = 0
    for z in zones:
        geom = z.get('polygon')
        if not geom:
            continue
        try:
            poly = projected_shape(geom, lng, lat)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        for line, orientation in _trace_rows_in_poly(poly, spacing):
            count += 1
            row = {
                'id': get_next_id('tree_rows'),
                'project_id': project_id,
                'zone_id': z['id'],
                'name_en': f'Row {count}',
                'name_ar': f'صف {count}',
                'geometry': unproject_shape(line, lng, lat),
                'spacing_m': round(spacing, 2),
                'orientation': round(orientation, 1),
                'validated': False,
                'auto_generated': True
            }
            mongo.db.tree_rows.insert_one(row)
            total += 1

    flash(f'Generated {total} rows / تم إنشاء {total} صف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/<int:row_id>/edit', methods=['POST'])
@login_required
def rows_edit(project_id, row_id):
    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    spacing = request.form.get('spacing', default=0, type=float)
    if spacing >= 1:
        update['spacing_m'] = round(spacing, 2)
    update['orientation'] = request.form.get('orientation', default=0, type=float)
    mongo.db.tree_rows.update_one(
        {'id': row_id, 'project_id': project_id},
        {'$set': update}
    )
    flash('Row updated / تم تحديث الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/<int:row_id>/rename', methods=['POST'])
@login_required
def rows_rename(project_id, row_id):
    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    mongo.db.tree_rows.update_one(
        {'id': row_id, 'project_id': project_id},
        {'$set': update}
    )
    flash('Row renamed / تمت إعادة تسمية الصف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/remove', methods=['POST'])
@login_required
def rows_remove(project_id):
    ids = request.form.getlist('row_ids')
    removed = 0
    for raw in ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        res = mongo.db.tree_rows.delete_one({'id': rid, 'project_id': project_id})
        removed += res.deleted_count
    flash(f'Removed {removed} rows / تمت إزالة {removed} صف', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/rows/validate', methods=['POST'])
@login_required
def rows_validate(project_id):
    mongo.db.tree_rows.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    flash('Rows validated / تم التحقق من الصفوف', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


# === TREES ===

@trees_bp.route('/<int:project_id>/trees')
@login_required
def trees_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    for r in rows:
        r['_id'] = str(r['_id'])

    trees = list(mongo.db.trees.find({'project_id': project_id}))
    for t in trees:
        t['_id'] = str(t['_id'])

    return render_template('trees/trees.html', project=project, rows=rows, trees=trees)


@trees_bp.route('/<int:project_id>/trees/generate', methods=['POST'])
@login_required
def trees_generate(project_id):
    """Generate tree points along validated rows based on spacing."""
    rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    if not rows:
        flash('No rows found. Create rows first / لا توجد صفوف. أنشئ الصفوف أولاً', 'warning')
        return redirect(url_for('trees.trees_view', project_id=project_id))

    spacing = request.form.get('spacing', default=4, type=float)
    variety = request.form.get('variety', default='Olive').strip()
    variety_ar = request.form.get('variety_ar', default='زيتون').strip()

    # Clear existing trees
    mongo.db.trees.delete_many({'project_id': project_id})

    from shapely.geometry import shape as shp_shape, Point
    from shapely.geometry import mapping
    from irrigation.models import get_next_id as _gid
    from irrigation.engineering.projection import projected_shape, unproject_shape, get_project_centroid

    # Get project for projection
    project_doc = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project_doc) if project_doc else (0.0, 0.0)

    total = 0
    for row in rows:
        # Use projected geometry for accurate meter-based length calculation
        row_geom = projected_shape(row['geometry'], lng, lat)
        length = row_geom.length  # Now in meters, not degrees
        row_spacing = row.get('spacing_m', spacing)

        if length <= 0:
            continue

        num_trees = max(1, int(length / row_spacing))
        for i in range(num_trees + 1):
            fraction = i / num_trees if num_trees > 0 else 0
            point = row_geom.interpolate(fraction * length)
            # Convert projected point back to WGS84 for storage
            point_geojson = unproject_shape(point, lng, lat)
            tree = {
                'id': _gid('trees'),
                'project_id': project_id,
                'zone_id': row.get('zone_id'),
                'row_id': row['id'],
                'name_en': f'Tree {row["id"]}-{i+1}',
                'name_ar': f'شجرة {row["id"]}-{i+1}',
                'geometry': point_geojson,
                'variety_en': variety,
                'variety_ar': variety_ar,
                'spacing_m': row_spacing,
                'drips_per_tree': 2,
                'validated': False
            }
            mongo.db.trees.insert_one(tree)
            total += 1

    flash(f'Generated {total} trees / تم إنشاء {total} شجرة', 'success')
    return redirect(url_for('trees.trees_view', project_id=project_id))


@trees_bp.route('/<int:project_id>/trees/validate', methods=['POST'])
@login_required
def trees_validate(project_id):
    mongo.db.trees.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    flash('Trees validated / تم التحقق من الأشجار', 'success')
    return redirect(url_for('hydrology.valves', project_id=project_id))
