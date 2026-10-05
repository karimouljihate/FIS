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
