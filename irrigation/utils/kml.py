"""KML/KMZ parsing and export utilities."""
import io
import zipfile
from fastkml import kml, styles
from lxml import etree

from irrigation.extensions import mongo
from irrigation.models import get_next_id


def parse_kml_content(kml_text):
    """Parse KML text and extract all polygons and points as GeoJSON."""
    doc = etree.fromstring(kml_text.encode('utf-8'))
    k = kml.KML()
    k.from_element(doc)

    features = []

    def walk_feature(folder, depth=0):
        for feature in folder.features():
            name = feature.name or ''
            if hasattr(feature, 'geometry') and feature.geometry:
                geom = feature.geometry.geometry
                geojson = _kml_geom_to_geojson(geom)
                if geojson:
                    features.append({
                        'name': name,
                        'geometry': geojson
                    })
            # Recurse into folders
            if hasattr(feature, 'features'):
                walk_feature(feature, depth + 1)

    walk_feature(k)

    return features


def parse_kmz_file(kmz_path):
    """Extract doc.kml from a KMZ file and parse it."""
    with zipfile.ZipFile(kmz_path, 'r') as zf:
        kml_files = [f for f in zf.namelist() if f.endswith('.kml')]
        if not kml_files:
            return []
        kml_data = zf.read(kml_files[0]).decode('utf-8')
    return parse_kml_content(kml_data)


def _kml_geom_to_geojson(geom):
    """Convert a shapely geometry from fastkml to GeoJSON dict."""
    if geom is None:
        return None

    from shapely.geometry import mapping
    return mapping(geom)


def export_project_to_kml(project_id):
    """Build a KML document from a project's geometry elements."""
    project = mongo.db.projects.find_one({'_id': project_id})
    if not project:
        return None

    k = kml.KML()
    ns = '{http://www.opengis.net/kml/2.2}'

    # Main document folder
    doc = kml.Document(ns, 'project-doc', project.get('name', 'Project'), '')
    k.append(doc)

    # Land boundary
    if project.get('boundary'):
        boundary_geom = project['boundary']
        from shapely.geometry import shape as shp_shape
        shapely_geom = shp_shape(boundary_geom)
        pm = kml.Placemark(ns, 'boundary', 'Land Boundary / حدود الأرض', '')
        pm.geometry = kml.Geometry(geometry=shapely_geom)
        doc.append(pm)

    # Sectors
    sectors_folder = kml.Folder(ns, 'sectors', 'Sectors / القطاعات', '')
    doc.append(sectors_folder)
    for sector in mongo.db.sectors.find({'project_id': project_id}):
        if sector.get('polygon'):
            from shapely.geometry import shape as shp_shape
            shapely_geom = shp_shape(sector['polygon'])
            name = sector.get('name_en', sector.get('name', ''))
            pm = kml.Placemark(ns, f'sector-{sector["id"]}', name, '')
            pm.geometry = kml.Geometry(geometry=shapely_geom)
            sectors_folder.append(pm)

    # Zones
    zones_folder = kml.Folder(ns, 'zones', 'Zones / المناطق', '')
    doc.append(zones_folder)
    for zone in mongo.db.zones.find({'project_id': project_id}):
        if zone.get('polygon'):
            from shapely.geometry import shape as shp_shape
            shapely_geom = shp_shape(zone['polygon'])
            name = zone.get('name_en', zone.get('name', ''))
            pm = kml.Placemark(ns, f'zone-{zone["id"]}', name, '')
            pm.geometry = kml.Geometry(geometry=shapely_geom)
            zones_folder.append(pm)

    # Network elements (pipes, valves, etc.)
    net_folder = kml.Folder(ns, 'network', 'Network / الشبكة', '')
    doc.append(net_folder)
    for elem in mongo.db.network_elements.find({'project_id': project_id}):
        if elem.get('geometry'):
            from shapely.geometry import shape as shp_shape
            shapely_geom = shp_shape(elem['geometry'])
            name = f"{elem.get('type', 'element')}"
            pm = kml.Placemark(ns, f'elem-{elem["id"]}', name, '')
            pm.geometry = kml.Geometry(geometry=shapely_geom)
            net_folder.append(pm)

    # Tree rows
    rows_folder = kml.Folder(ns, 'rows', 'Tree Rows / صفوف الأشجار', '')
    doc.append(rows_folder)
    for row in mongo.db.tree_rows.find({'project_id': project_id}):
        if row.get('geometry'):
            from shapely.geometry import shape as shp_shape
            shapely_geom = shp_shape(row['geometry'])
            pm = kml.Placemark(ns, f'row-{row["id"]}', 'Tree Row / صف', '')
            pm.geometry = kml.Geometry(geometry=shapely_geom)
            rows_folder.append(pm)

    # Trees
    trees_folder = kml.Folder(ns, 'trees', 'Trees / الأشجار', '')
    doc.append(trees_folder)
    for tree in mongo.db.trees.find({'project_id': project_id}):
        if tree.get('geometry'):
            from shapely.geometry import shape as shp_shape
            shapely_geom = shp_shape(tree['geometry'])
            pm = kml.Placemark(ns, f'tree-{tree["id"]}', 'Tree / شجرة', '')
            pm.geometry = kml.Geometry(geometry=shapely_geom)
            trees_folder.append(pm)

    return etree.tostring(doc.to_element(), pretty_print=True).decode('utf-8')
