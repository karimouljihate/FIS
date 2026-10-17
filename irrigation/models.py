import re

from irrigation.extensions import mongo


def get_next_id(collection_name):
    """Auto-increment integer ID for a collection."""
    counters = mongo.db.counters
    counter = counters.find_one_and_update(
        {'_id': collection_name},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=True
    )
    return counter['seq']


def serialize_doc(doc):
    """Convert MongoDB doc to JSON-serializable dict with string IDs."""
    if doc is None:
        return None
    doc = dict(doc)
    doc['_id'] = str(doc['_id'])
    return doc


def _sector_short_code(sector, ordinal):
    name = (sector.get('name_en') or '').strip()
    match = re.match(r'^[Ss](\d+)$', re.sub(r'[^Ss0-9]', '', name)) or re.search(r'(\d+)', name)
    if match:
        return 'S{}'.format(match.group(1))
    return 'S{}'.format(ordinal)


def assign_short_names(project_id):
    """Compute and persist hierarchical short names for a project.

    Sector -> ``S1`` (number parsed from the name, else its ordinal),
    Zone -> ``S1Z1`` (numbered per sector), Row -> ``S1Z1R1`` (per zone),
    Tree -> ``S1Z1R1T1`` (per row). Numbering follows ascending ``id``.
    """
    def _sorted(collection):
        return sorted(
            list(mongo.db[collection].find({'project_id': project_id})),
            key=lambda doc: doc.get('id', 0)
        )

    def _save(collection, doc, value):
        if doc.get('short_name') != value:
            mongo.db[collection].update_one({'_id': doc['_id']}, {'$set': {'short_name': value}})
        return value

    sector_codes = {}
    for ordinal, sector in enumerate(_sorted('sectors'), start=1):
        sector_codes[sector['id']] = _save('sectors', sector, _sector_short_code(sector, ordinal))

    zone_codes = {}
    zone_counters = {}
    for zone in _sorted('zones'):
        sector_id = zone.get('sector_id')
        zone_counters[sector_id] = zone_counters.get(sector_id, 0) + 1
        base = sector_codes.get(sector_id, 'S{}'.format(sector_id))
        zone_codes[zone['id']] = _save(
            'zones', zone, '{}Z{}'.format(base, zone_counters[sector_id])
        )

    row_codes = {}
    row_counters = {}
    for row in _sorted('tree_rows'):
        zone_id = row.get('zone_id')
        row_counters[zone_id] = row_counters.get(zone_id, 0) + 1
        base = zone_codes.get(zone_id, 'Z{}'.format(zone_id))
        row_codes[row['id']] = _save(
            'tree_rows', row, '{}R{}'.format(base, row_counters[zone_id])
        )

    tree_counters = {}
    for tree in _sorted('trees'):
        row_id = tree.get('row_id')
        tree_counters[row_id] = tree_counters.get(row_id, 0) + 1
        base = row_codes.get(row_id, 'R{}'.format(row_id))
        _save('trees', tree, '{}T{}'.format(base, tree_counters[row_id]))
