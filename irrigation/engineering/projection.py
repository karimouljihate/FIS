"""UTM projection utilities for accurate meter-based calculations.

All engineering calculations (lengths, areas, spacing, hydraulics) must run
in a local projected CRS (UTM), not raw WGS84 degrees.
"""
import math
from shapely.geometry import shape, mapping, Point, Polygon, LineString, MultiPolygon

try:
    import pyproj
    _HAS_PYPROJ = True
except ImportError:
    _HAS_PYPROJ = False


def get_utm_epsg(lng, lat):
    """Determine the UTM EPSG code for a given longitude/latitude."""
    if lat >= 0:
        # Northern hemisphere
        zone = int((lng + 180.0) / 6.0) + 1
        return 32600 + zone  # WGS84 / UTM zone N
    else:
        zone = int((lng + 180.0) / 6.0) + 1
        return 32700 + zone  # WGS84 / UTM zone S


def get_transformer(lng, lat):
    """Get forward and inverse transformers for WGS84 <-> UTM."""
    if not _HAS_PYPROJ:
        return None, None
    utm_epsg = get_utm_epsg(lng, lat)
    transformer_fwd = pyproj.Transformer.from_crs('EPSG:4326', f'EPSG:{utm_epsg}', always_xy=True)
    transformer_inv = pyproj.Transformer.from_crs(f'EPSG:{utm_epsg}', 'EPSG:4326', always_xy=True)
    return transformer_fwd, transformer_inv


def project_geometry(geom_geojson, lng, lat):
    """Project a GeoJSON geometry from WGS84 to UTM meters."""
    if not _HAS_PYPROJ:
        return geom_geojson  # Fallback: return as-is

    transformer_fwd, _ = get_transformer(lng, lat)
    if not transformer_fwd:
        return geom_geojson

    geom = shape(geom_geojson)

    def transform_coords(coords):
        """Recursively transform coordinate pairs."""
        if len(coords) == 2 and isinstance(coords[0], (int, float)):
            x, y = transformer_fwd.transform(coords[0], coords[1])
            return [x, y]
        return [transform_coords(c) for c in coords]

    projected = transform_coords(geom_geojson['coordinates'])
    geom_type = geom_geojson['type']

    if geom_type == 'Point':
        return {'type': 'Point', 'coordinates': projected}
    elif geom_type == 'LineString':
        return {'type': 'LineString', 'coordinates': projected}
    elif geom_type == 'Polygon':
        return {'type': 'Polygon', 'coordinates': projected}
    elif geom_type == 'MultiPolygon':
        return {'type': 'MultiPolygon', 'coordinates': projected}

    return geom_geojson


def unproject_geometry(geom_projected, lng, lat):
    """Project a geometry from UTM meters back to WGS84."""
    if not _HAS_PYPROJ:
        return geom_projected

    _, transformer_inv = get_transformer(lng, lat)
    if not transformer_inv:
        return geom_projected

    def transform_coords(coords):
        if len(coords) == 2 and isinstance(coords[0], (int, float)):
            x, y = transformer_inv.transform(coords[0], coords[1])
            return [x, y]
        return [transform_coords(c) for c in coords]

    projected = transform_coords(geom_projected['coordinates'])
    geom_type = geom_projected['type']

    if geom_type == 'Point':
        return {'type': 'Point', 'coordinates': projected}
    elif geom_type == 'LineString':
        return {'type': 'LineString', 'coordinates': projected}
    elif geom_type == 'Polygon':
        return {'type': 'Polygon', 'coordinates': projected}
    elif geom_type == 'MultiPolygon':
        return {'type': 'MultiPolygon', 'coordinates': projected}

    return geom_projected


def get_project_centroid(project):
    """Get the centroid [lng, lat] for projection purposes."""
    if project.get('water_source') and project['water_source'].get('coordinates'):
        return project['water_source']['coordinates'][0], project['water_source']['coordinates'][1]

    if project.get('boundary') and project['boundary'].get('type') == 'Polygon':
        coords = project['boundary']['coordinates'][0]
        cx = sum(c[0] for c in coords) / len(coords)
        cy = sum(c[1] for c in coords) / len(coords)
        return cx, cy

    # Fallback
    return 0.0, 0.0


def projected_shape(geom_geojson, lng, lat):
    """Get a shapely geometry projected to UTM meters."""
    projected = project_geometry(geom_geojson, lng, lat)
    return shape(projected)


def unproject_shape(geom_shapely, lng, lat):
    """Convert a projected shapely geometry back to GeoJSON WGS84."""
    projected_geojson = mapping(geom_shapely)
    return unproject_geometry(projected_geojson, lng, lat)


def haversine_length_meters(coords):
    """Calculate line length in meters using haversine formula (fallback when pyproj unavailable)."""
    R = 6371000.0  # Earth radius in meters
    total = 0.0
    for i in range(1, len(coords)):
        lng1, lat1 = math.radians(coords[i-1][0]), math.radians(coords[i-1][1])
        lng2, lat2 = math.radians(coords[i][0]), math.radians(coords[i][1])
        dlng = lng2 - lng1
        dlat = lat2 - lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
        c = 2 * math.asin(math.sqrt(a))
        total += R * c
    return total
