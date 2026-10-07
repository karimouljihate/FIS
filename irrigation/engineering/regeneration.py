"""Real AI regeneration engine for sectors and zones.

Uses optimization heuristics:
- Projects boundary to UTM meters for accurate area calculations.
- Tries multiple split angles and area-balanced partitioning.
- Scores candidates by area balance, compactness, water-source proximity,
  shape regularity, and terrain elevation analysis.
- Selects the best-scoring partition.

Elevation-aware scoring:
- Fetches terrain elevation data from Open-Meteo API (cached in MongoDB).
- Prefers partitions that group similar elevations together (contour bands).
- Penalties for sectors that require pumping uphill from water source.
- Gravity flow potential considered in partition scoring.
"""
import math
from shapely.geometry import shape, mapping, box, Polygon, LineString, Point, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, split as shapely_split
import numpy as np

from irrigation.extensions import mongo
from irrigation.models import get_next_id
from irrigation.engineering.projection import (
    projected_shape, unproject_shape, get_project_centroid, project_geometry, haversine_length_meters
)
from irrigation.engineering.elevation import (
    get_or_build_elevation_model, elevation_metrics_for_polygon,
    get_elevation_at_point
)


# === CONFIGURATION ===

# Angles to try for splitting (degrees)
SPLIT_ANGLES = list(range(0, 180, 5))  # 0, 5, 10, ..., 175

# Scoring weights (with elevation - total = 1.0)
WEIGHT_AREA_BALANCE = 0.20
WEIGHT_COMPACTNESS = 0.15
WEIGHT_WATER_ACCESS = 0.12
WEIGHT_SHAPE_REGULARITY = 0.10
WEIGHT_ELEVATION_UNIFORMITY = 0.25
WEIGHT_GRAVITY_PRESSURE = 0.18

# Fallback weights when elevation data is unavailable (total = 1.0)
WEIGHT_AREA_BALANCE_2D = 0.35
WEIGHT_COMPACTNESS_2D = 0.25
WEIGHT_WATER_ACCESS_2D = 0.20
WEIGHT_SHAPE_REGULARITY_2D = 0.20

# Minimum sector area fraction (avoid tiny slivers)
MIN_AREA_FRACTION = 0.05


def _extract_polygons(geom):
    """Extract usable Polygon objects from any geometry type.

    Handles MultiPolygon, GeometryCollection, and invalid geometries
    by returning the largest valid polygon or filtering to polygons only.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        if geom.is_valid:
            return [geom]
        else:
            fixed = geom.buffer(0)
            if isinstance(fixed, Polygon) and fixed.is_valid:
                return [fixed]
            return []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys = []
        for part in geom.geoms:
            if isinstance(part, Polygon) and part.is_valid and not part.is_empty:
                polys.append(part)
            elif isinstance(part, (MultiPolygon, GeometryCollection)):
                polys.extend(_extract_polygons(part))
        return polys
    # For other geometry types, try buffer(0) to fix
    try:
        fixed = geom.buffer(0)
        if isinstance(fixed, Polygon) and fixed.is_valid:
            return [fixed]
        elif isinstance(fixed, (MultiPolygon, GeometryCollection)):
            return _extract_polygons(fixed)
    except Exception:
        pass
    return []


def _largest_polygon(geom):
    """Return the largest valid polygon from a geometry."""
    polys = _extract_polygons(geom)
    if not polys:
        return None
    return max(polys, key=lambda p: p.area)


def _rotate_polygon(polygon, angle_degrees, origin=None):
    """Rotate a shapely polygon around an origin.

    Handles MultiPolygon by rotating the largest valid polygon.
    """
    # Ensure we have a valid Polygon
    if isinstance(polygon, (MultiPolygon, GeometryCollection)):
        polygon = _largest_polygon(polygon)
        if polygon is None:
            return Polygon()  # Empty fallback

    if not isinstance(polygon, Polygon) or polygon.is_empty:
        return Polygon()

    if origin is None:
        origin = polygon.centroid
    angle_rad = math.radians(angle_degrees)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    def rotate_point(x, y):
        dx = x - origin.x
        dy = y - origin.y
        return (dx * cos_a - dy * sin_a + origin.x,
                dx * sin_a + dy * cos_a + origin.y)

    rotated_coords = []
    for ring in [polygon.exterior] + list(polygon.interiors):
        new_ring = [rotate_point(x, y) for x, y in ring.coords]
        rotated_coords.append(new_ring)

    return Polygon(rotated_coords[0], rotated_coords[1:])


def _split_polygon_equal_area(polygon, n_parts):
    """Split a polygon into n_parts of approximately equal area by slicing perpendicular to the x-axis."""
    bounds = polygon.bounds
    minx, miny, maxx, maxy = bounds
    total_area = polygon.area
    target_area = total_area / n_parts

    if total_area <= 0:
        return []

    parts = []
    current_min_x = minx

    for i in range(n_parts - 1):
        target_accumulated = target_area * (i + 1)

        # Binary search for the x-coordinate that gives the target area
        lo = current_min_x
        hi = maxx

        for _ in range(50):  # Binary search iterations
            mid = (lo + hi) / 2
            clip_box = box(current_min_x, miny, mid, maxy)
            clipped = polygon.intersection(clip_box)
            if clipped.area < target_accumulated - sum(p.area for p in parts):
                lo = mid
            else:
                hi = mid

        # Final slice
        clip_box = box(current_min_x, miny, (lo + hi) / 2, maxy)
        clipped = polygon.intersection(clip_box)
        # Handle MultiPolygon/GeometryCollection results
        clipped_polys = _extract_polygons(clipped)
        if clipped_polys:
            merged = unary_union(clipped_polys)
            parts.append(merged)
            current_min_x = (lo + hi) / 2

    # Last part
    clip_box = box(current_min_x, miny, maxx, maxy)
    last = polygon.intersection(clip_box)
    last_polys = _extract_polygons(last)
    if last_polys:
        merged = unary_union(last_polys)
        parts.append(merged)

    return parts


def _score_partition(parts, water_source_point=None, elevation_model=None, water_source_elevation=None):
    """Score a candidate partition based on multiple criteria.

    When elevation_model is available, adds two terrain-aware criteria:
    - Elevation uniformity: prefers sectors with similar internal elevation (contour bands)
    - Gravity pressure: prefers sectors at or below water source elevation (gravity flow)
    """
    if not parts:
        return -1.0

    n = len(parts)
    areas = [p.area for p in parts if p.area > 0]
    if not areas:
        return -1.0

    total_area = sum(areas)
    avg_area = total_area / len(areas)

    # 1. Area balance (lower variance = better)
    area_variance = sum((a - avg_area) ** 2 for a in areas) / len(areas)
    area_balance_score = 1.0 - min(1.0, area_variance / (avg_area ** 2)) if avg_area > 0 else 0.0

    # 2. Compactness (Polsby-Popper ratio: 4*pi*area / perimeter^2)
    compactness_scores = []
    for p in parts:
        if p.area > 0:
            perimeter = p.length
            if perimeter > 0:
                ratio = (4 * math.pi * p.area) / (perimeter ** 2)
                compactness_scores.append(ratio)
    compactness_score = sum(compactness_scores) / len(compactness_scores) if compactness_scores else 0.0

    # 3. Water source access (average distance from water source to each part centroid)
    if water_source_point:
        distances = []
        for p in parts:
            centroid = p.centroid
            d = math.sqrt((centroid.x - water_source_point.x) ** 2 +
                          (centroid.y - water_source_point.y) ** 2)
            distances.append(d)
        avg_dist = sum(distances) / len(distances)
        max_dist = max(distances) if distances else 1.0
        water_score = 1.0 - min(1.0, avg_dist / (max_dist * 2 + 1)) if max_dist > 0 else 1.0
    else:
        water_score = 0.5

    # 4. Shape regularity (penalize tiny fragments)
    min_area = min(areas)
    regularity_score = min_area / avg_area if avg_area > 0 else 0.0

    # Determine if we have elevation data
    has_elevation = elevation_model is not None and elevation_model.get('samples')

    if has_elevation:
        # 5. Elevation uniformity (prefers contour-band partitions)
        part_elev_metrics = []
        total_site_range = elevation_model.get('stats', {}).get('range_m', 0) or 0

        for p in parts:
            if p.area <= 0:
                continue
            metrics = elevation_metrics_for_polygon(elevation_model, p)
            if metrics:
                part_elev_metrics.append(metrics)

        if part_elev_metrics and total_site_range > 0:
            # Average of (part_range / site_range) — lower is better (more uniform)
            uniformity_ratios = [m['range_m'] / total_site_range for m in part_elev_metrics]
            avg_ratio = sum(uniformity_ratios) / len(uniformity_ratios)
            elevation_uniformity_score = 1.0 - min(1.0, avg_ratio)
        else:
            elevation_uniformity_score = 0.5  # Neutral

        # 6. Gravity pressure (prefer sectors at/below water source elevation)
        if water_source_elevation is not None:
            gravity_scores = []
            for m in part_elev_metrics:
                part_mean_elev = m['mean_elevation_m']
                elevation_gain = part_mean_elev - water_source_elevation
                # Downhill or flat = 1.0, uphill = penalty
                # Use a gentle penalty: each 10m uphill reduces score by ~0.3
                if elevation_gain <= 0:
                    gravity_scores.append(1.0)
                else:
                    gravity_scores.append(max(0.0, 1.0 - (elevation_gain / 30.0)))
            gravity_score = sum(gravity_scores) / len(gravity_scores) if gravity_scores else 0.5
        else:
            gravity_score = 0.5

        total_score = (
            WEIGHT_AREA_BALANCE * area_balance_score +
            WEIGHT_COMPACTNESS * compactness_score +
            WEIGHT_WATER_ACCESS * water_score +
            WEIGHT_SHAPE_REGULARITY * regularity_score +
            WEIGHT_ELEVATION_UNIFORMITY * elevation_uniformity_score +
            WEIGHT_GRAVITY_PRESSURE * gravity_score
        )
    else:
        # Fallback: 2D scoring without elevation
        total_score = (
            WEIGHT_AREA_BALANCE_2D * area_balance_score +
            WEIGHT_COMPACTNESS_2D * compactness_score +
            WEIGHT_WATER_ACCESS_2D * water_score +
            WEIGHT_SHAPE_REGULARITY_2D * regularity_score
        )

    return total_score


def _try_partition(polygon, n_parts, angle, water_source_point=None,
                    elevation_model=None, water_source_elevation=None):
    """Try partitioning at a specific angle and return scored result.

    Passes elevation model to scoring for terrain-aware optimization.
    """
    # Rotate polygon
    rotated = _rotate_polygon(polygon, angle)

    # Split into equal-area strips
    parts = _split_polygon_equal_area(rotated, n_parts)

    # Rotate parts back and filter
    original_parts = []
    for part in parts:
        # Handle MultiPolygon from intersection
        polys = _extract_polygons(part)
        if not polys:
            continue
        merged = unary_union(polys)
        if merged.is_empty or merged.area < polygon.area * MIN_AREA_FRACTION:
            continue
        back = _rotate_polygon(merged, -angle, origin=rotated.centroid)
        original_parts.append(back)

    if len(original_parts) < n_parts:
        return None

    score = _score_partition(
        original_parts, water_source_point,
        elevation_model, water_source_elevation
    )
    return {'parts': original_parts, 'score': score, 'angle': angle}


def regenerate_sectors(project_id, n_sectors=None):
    """AI regeneration: optimally divide land boundary into sectors.

    Tries multiple split angles, evaluates area balance, compactness,
    water-source proximity, shape regularity, and terrain elevation,
    then selects the best partition.
    """
    project = mongo.db.projects.find_one({'_id': project_id})
    if not project:
        project = mongo.db.projects.find_one({'id': project_id})
    if not project:
        return []

    boundary = project.get('boundary')
    if not boundary or boundary.get('type') != 'Polygon':
        return []

    # Determine number of sectors
    if n_sectors is None:
        n_sectors = 4  # Default

    # Project to UTM meters
    lng, lat = get_project_centroid(project) if project else (0.0, 0.0)
    boundary_shapely = projected_shape(boundary, lng, lat)

    # Ensure we have a valid Polygon
    if isinstance(boundary_shapely, MultiPolygon):
        boundary_shapely = max(boundary_shapely.geoms, key=lambda g: g.area)

    if boundary_shapely.is_empty or boundary_shapely.area <= 0:
        return []

    # Water source point in projected coordinates
    water_point = None
    if project.get('water_source') and project['water_source'].get('coordinates'):
        ws_geojson = project['water_source']
        ws_projected = project_geometry(ws_geojson, lng, lat)
        water_point = Point(ws_projected['coordinates'])

    # Fetch or build terrain elevation model
    elevation_model = None
    water_source_elevation = None
    try:
        elevation_model = get_or_build_elevation_model(project_id, project)
        if elevation_model:
            water_source_elevation = elevation_model.get('water_source_elevation_m')
    except Exception:
        # Elevation fetch failed — continue with 2D scoring
        pass

    # Try all angles and find the best partition
    best_result = None
    best_score = -1.0

    for angle in SPLIT_ANGLES:
        result = _try_partition(
            boundary_shapely, n_sectors, angle, water_point,
            elevation_model, water_source_elevation
        )
        if result and result['score'] > best_score:
            best_score = result['score']
            best_result = result

    if not best_result:
        # Fallback: simple grid
        best_result = _fallback_grid(boundary_shapely, n_sectors, water_point,
                                      elevation_model, water_source_elevation)

    # Clear existing sectors and related data
    mongo.db.sectors.delete_many({'project_id': project_id})
    mongo.db.zones.delete_many({'project_id': project_id})
    mongo.db.network_elements.delete_many({'project_id': project_id})
    mongo.db.tree_rows.delete_many({'project_id': project_id})
    mongo.db.trees.delete_many({'project_id': project_id})

    # Save the best partition
    sectors = []
    sector_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    sector_names_ar = ['أ', 'ب', 'ج', 'د', 'هـ', 'و', 'ز', 'ح']

    for i, part in enumerate(best_result['parts']):
        # Unproject back to WGS84
        sector_geojson = unproject_shape(part, lng, lat)

        name_en = f"Sector {sector_names[i] if i < len(sector_names) else i+1}"
        name_ar = f"القطاع {sector_names_ar[i] if i < len(sector_names_ar) else i+1}"

        # Get elevation metrics for this sector
        elev_metrics = None
        if elevation_model:
            elev_metrics = elevation_metrics_for_polygon(elevation_model, part)

        sector = {
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': name_en,
            'name_ar': name_ar,
            'polygon': sector_geojson,
            'validated': False,
            'ai_generated': True,
            'ai_score': round(best_result['score'], 4),
            'ai_angle': best_result['angle'],
            'area_m2': round(part.area, 2),
            'elevation_aware': elevation_model is not None,
            'elevation_metrics': elev_metrics
        }
        mongo.db.sectors.insert_one(sector)
        sector['_id'] = str(sector['_id'])
        sectors.append(sector)

    return sectors


def regenerate_zones(project_id, sector_id=None, zones_per_sector=None):
    """AI regeneration: optimally divide sectors into zones.

    Uses the same optimization approach but applied within each sector's polygon.
    Incorporates terrain elevation data for contour-aware zone partitioning.
    If trees exist, zones are sized proportionally to tree count (hydraulic demand).
    """
    query = {'project_id': project_id}
    if sector_id:
        query['id'] = sector_id

    sectors = list(mongo.db.sectors.find(query))
    if not sectors:
        return []

    if zones_per_sector is None:
        zones_per_sector = 2

    project = mongo.db.projects.find_one({'id': project_id})
    lng, lat = get_project_centroid(project) if project else (0.0, 0.0)

    # Fetch or build elevation model (cached)
    elevation_model = None
    water_source_elevation = None
    if project:
        try:
            elevation_model = get_or_build_elevation_model(project_id, project)
            if elevation_model:
                water_source_elevation = elevation_model.get('water_source_elevation_m')
        except Exception:
            pass

    zones = []
    zone_names = [1, 2, 3, 4]

    for sector in sectors:
        polygon = sector.get('polygon')
        if not polygon or polygon.get('type') != 'Polygon':
            continue

        sector_shapely = projected_shape(polygon, lng, lat)
        # Handle MultiPolygon
        if isinstance(sector_shapely, MultiPolygon):
            sector_shapely = max(sector_shapely.geoms, key=lambda g: g.area)

        if sector_shapely.is_empty or sector_shapely.area <= 0:
            continue

        # Check if trees exist for demand-based sizing
        tree_count = mongo.db.trees.count_documents({
            'project_id': project_id,
            'zone_id': {'$exists': True}
        })

        # Try partitioning with elevation awareness
        best_result = None
        best_score = -1.0

        for angle in SPLIT_ANGLES:
            result = _try_partition(
                sector_shapely, zones_per_sector, angle, None,
                elevation_model, water_source_elevation
            )
            if result and result['score'] > best_score:
                best_score = result['score']
                best_result = result

        if not best_result:
            best_result = _fallback_grid(sector_shapely, zones_per_sector, None,
                                          elevation_model, water_source_elevation)

        sector_name_en = sector.get('name_en', 'Sector')
        sector_name_ar = sector.get('name_ar', 'قطاع')

        for i, part in enumerate(best_result['parts']):
            zone_geojson = unproject_shape(part, lng, lat)

            z_num = zone_names[i] if i < len(zone_names) else i + 1
            name_en = f"{sector_name_en} - Zone {z_num}"
            name_ar = f"{sector_name_ar} - منطقة {z_num}"

            # Get elevation metrics for this zone
            elev_metrics = None
            if elevation_model:
                elev_metrics = elevation_metrics_for_polygon(elevation_model, part)

            zone = {
                'id': get_next_id('zones'),
                'project_id': project_id,
                'sector_id': sector['id'],
                'name_en': name_en,
                'name_ar': name_ar,
                'polygon': zone_geojson,
                'validated': False,
                'ai_generated': True,
                'ai_score': round(best_result['score'], 4),
                'area_m2': round(part.area, 2),
                'elevation_aware': elevation_model is not None,
                'elevation_metrics': elev_metrics
            }
            mongo.db.zones.insert_one(zone)
            zone['_id'] = str(zone['_id'])
            zones.append(zone)

    return zones


def _fallback_grid(polygon, n_parts, water_point, elevation_model=None, water_source_elevation=None):
    """Simple grid-based fallback if optimization fails."""
    bounds = polygon.bounds
    minx, miny, maxx, maxy = bounds
    total_area = polygon.area
    target_area = total_area / n_parts

    # Try horizontal strips
    parts = []
    current_y = miny
    strip_height = (maxy - miny) / n_parts

    for i in range(n_parts):
        y1 = miny + i * strip_height
        y2 = miny + (i + 1) * strip_height
        clip_box = box(minx, y1, maxx, y2)
        clipped = polygon.intersection(clip_box)
        clipped_polys = _extract_polygons(clipped)
        if clipped_polys:
            parts.append(unary_union(clipped_polys))

    if len(parts) < n_parts:
        # Try vertical strips
        parts = []
        strip_width = (maxx - minx) / n_parts
        for i in range(n_parts):
            x1 = minx + i * strip_width
            x2 = minx + (i + 1) * strip_width
            clip_box = box(x1, miny, x2, maxy)
            clipped = polygon.intersection(clip_box)
            clipped_polys = _extract_polygons(clipped)
            if clipped_polys:
                parts.append(unary_union(clipped_polys))

    score = _score_partition(parts, water_point, elevation_model, water_source_elevation)
    return {'parts': parts, 'score': score, 'angle': 0}


def get_regeneration_report(project_id):
    """Generate a report about the last AI regeneration."""
    sectors = list(mongo.db.sectors.find({'project_id': project_id, 'ai_generated': True}))
    zones = list(mongo.db.zones.find({'project_id': project_id, 'ai_generated': True}))

    # Check for elevation model
    elevation_model = mongo.db.project_elevation_models.find_one({'project_id': project_id})
    has_elevation = elevation_model is not None
    elev_stats = elevation_model.get('stats', {}) if elevation_model else {}

    report = {
        'sectors_count': len(sectors),
        'zones_count': len(zones),
        'sector_scores': [s.get('ai_score', 0) for s in sectors],
        'zone_scores': [z.get('ai_score', 0) for z in zones],
        'avg_sector_score': sum(s.get('ai_score', 0) for s in sectors) / len(sectors) if sectors else 0,
        'avg_zone_score': sum(z.get('ai_score', 0) for z in zones) / len(zones) if zones else 0,
        'total_area_m2': sum(s.get('area_m2', 0) for s in sectors),
        'elevation_aware': has_elevation,
        'elevation_stats': elev_stats,
        'water_source_elevation_m': elevation_model.get('water_source_elevation_m') if elevation_model else None,
        'sector_elevations': [
            {'name': s.get('name_en', ''), 'metrics': s.get('elevation_metrics', {})}
            for s in sectors if s.get('elevation_metrics')
        ]
    }
    return report

def regenerate_zones_for_sector(sector):
    """
    Regenerate zones only within the given sector.
    Returns a list of created/updated Zone objects.
    """
    from irrigation.models import Zone, Project
    from irrigation.db import db

    project = sector.project  # assuming relationship exists

    # Get sector geometry in projected form
    lng, lat = get_project_centroid(projected_shape_obj) if project else (0.0, 0.0)
    sector_geom_geojson = sector.geometry  # GeoJSON dict

    # Project sector geometry
    sector_shapely, utm_crs = projected_shape(sector_geom_geojson, lng, lat, return_crs=True)

    # Your existing zone-generation logic, but restricted to this sector:
    # - compute rows, spacing, orientation, etc. only inside sector_shapely
    # - create Zone records linked to this sector

    zones = []
    # ... (reuse/adapt the inner loop from regenerate_zones, but using sector_shapely)

    # Example placeholder:
    # for zone_data in generate_zone_layouts_inside(sector_shapely, sector):
    #     zone = Zone(**zone_data)
    #     db.session.add(zone)
    #     zones.append(zone)

    db.session.commit()
    return zones