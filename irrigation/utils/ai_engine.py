"""AI-powered geometry generation utilities."""
import math
import random

from irrigation.extensions import mongo


def generate_sectors(project_id):
    """AI stub: divide land boundary into sectors using a grid-based approach."""
    project = mongo.db.projects.find_one({'_id': project_id})
    if not project or not project.get('boundary'):
        return []

    boundary = project['boundary']
    if boundary.get('type') != 'Polygon':
        return []

    coords = boundary['coordinates'][0]
    min_x = min(c[0] for c in coords)
    max_x = max(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    max_y = max(c[1] for c in coords)

    # Divide into a 2x2 grid as a starting proposal
    mid_x = (min_x + max_x) / 2
    mid_y = (min_y + max_y) / 2

    sectors = []
    quadrant_defs = [
        ('Sector A', 'القطاع أ', min_x, mid_x, min_y, mid_y),
        ('Sector B', 'القطاع ب', mid_x, max_x, min_y, mid_y),
        ('Sector C', 'القطاع ج', min_x, mid_x, mid_y, max_y),
        ('Sector D', 'القطاع د', mid_x, max_x, mid_y, max_y),
    ]

    from irrigation.models import get_next_id
    for name_en, name_ar, x1, x2, y1, y2 in quadrant_defs:
        sector = {
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': name_en,
            'name_ar': name_ar,
            'polygon': {
                'type': 'Polygon',
                'coordinates': [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]
            },
            'validated': False,
            'ai_generated': True
        }
        mongo.db.sectors.insert_one(sector)
        sector['_id'] = str(sector['_id'])
        sectors.append(sector)

    return sectors


def generate_zones(project_id, sector_id=None):
    """AI stub: divide sectors into zones using a grid approach."""
    query = {'project_id': project_id}
    if sector_id:
        query['id'] = sector_id

    sectors = list(mongo.db.sectors.find(query))
    if not sectors:
        return []

    from irrigation.models import get_next_id
    zones = []

    for sector in sectors:
        polygon = sector.get('polygon')
        if not polygon or polygon.get('type') != 'Polygon':
            continue

        coords = polygon['coordinates'][0]
        min_x = min(c[0] for c in coords)
        max_x = max(c[0] for c in coords)
        min_y = min(c[1] for c in coords)
        max_y = max(c[1] for c in coords)

        # Divide into 2 zones per sector
        mid_x = (min_x + max_x) / 2

        zone_defs = [
            (f"{sector.get('name_en', 'Sector')} - Zone 1",
             f"{sector.get('name_ar', 'قطاع')} - منطقة 1",
             min_x, mid_x, min_y, max_y),
            (f"{sector.get('name_en', 'Sector')} - Zone 2",
             f"{sector.get('name_ar', 'قطاع')} - منطقة 2",
             mid_x, max_x, min_y, max_y),
        ]

        for name_en, name_ar, x1, x2, y1, y2 in zone_defs:
            zone = {
                'id': get_next_id('zones'),
                'project_id': project_id,
                'sector_id': sector['id'],
                'name_en': name_en,
                'name_ar': name_ar,
                'polygon': {
                    'type': 'Polygon',
                    'coordinates': [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]
                },
                'validated': False,
                'ai_generated': True
            }
            mongo.db.zones.insert_one(zone)
            zone['_id'] = str(zone['_id'])
            zones.append(zone)

    return zones
