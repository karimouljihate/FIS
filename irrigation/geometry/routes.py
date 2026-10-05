from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id, serialize_doc
from irrigation.engineering.regeneration import regenerate_sectors, regenerate_zones
import json

geometry_bp = Blueprint('geometry', __name__)


def get_project_or_redirect(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return None
    project['_id'] = str(project['_id'])
    return project


# === SECTORS ===

@geometry_bp.route('/<int:project_id>/sectors')
@login_required
def sectors_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    sectors = list(mongo.db.sectors.find({'project_id': project_id}).sort('id', 1))
    for s in sectors:
        s['_id'] = str(s['_id'])

    return render_template('geometry/sectors.html', project=project, sectors=sectors)


@geometry_bp.route('/<int:project_id>/sectors/add', methods=['POST'])
@login_required
def sector_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    polygon_coords = request.form.get('polygon_coords', '').strip()

    if not polygon_coords:
        flash('Polygon coordinates required / مطلوب إحداثيات المضلع', 'danger')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    try:
        coords = json.loads(polygon_coords)
    except Exception:
        flash('Invalid coordinates format / تنسيق الإحداثيات غير صالح', 'danger')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    sector = {
        'id': get_next_id('sectors'),
        'project_id': project_id,
        'name_en': name_en or f"Sector {mongo.db.sectors.count_documents({'project_id': project_id}) + 1}",
        'name_ar': name_ar or f"القطاع {mongo.db.sectors.count_documents({'project_id': project_id}) + 1}",
        'polygon': {
            'type': 'Polygon',
            'coordinates': [coords]
        },
        'validated': False
    }
    mongo.db.sectors.insert_one(sector)
    flash('Sector added / تم إضافة القطاع', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/<int:sector_id>/edit', methods=['POST'])
@login_required
def sector_edit(project_id, sector_id):
    sector = mongo.db.sectors.find_one({'id': sector_id, 'project_id': project_id})
    if not sector:
        flash('Sector not found / القطاع غير موجود', 'danger')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    polygon_coords = request.form.get('polygon_coords', '').strip()

    update = {}
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    if polygon_coords:
        try:
            coords = json.loads(polygon_coords)
            update['polygon'] = {'type': 'Polygon', 'coordinates': [coords]}
        except Exception:
            pass

    if update:
        mongo.db.sectors.update_one({'id': sector_id}, {'$set': update})
        flash('Sector updated / تم تحديث القطاع', 'success')

    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/<int:sector_id>/rename', methods=['POST'])
@login_required
def sector_rename(project_id, sector_id):
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    update = {}
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    if update:
        mongo.db.sectors.update_one({'id': sector_id}, {'$set': update})
        flash('Sector renamed / تمت إعادة تسمية القطاع', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/swap', methods=['POST'])
@login_required
def sector_swap(project_id):
    id1 = request.form.get('id1', type=int)
    id2 = request.form.get('id2', type=int)
    if id1 and id2:
        s1 = mongo.db.sectors.find_one({'id': id1, 'project_id': project_id})
        s2 = mongo.db.sectors.find_one({'id': id2, 'project_id': project_id})
        if s1 and s2:
            mongo.db.sectors.update_one({'id': id1}, {'$set': {'polygon': s2['polygon'], 'name_en': s2.get('name_en'), 'name_ar': s2.get('name_ar')}})
            mongo.db.sectors.update_one({'id': id2}, {'$set': {'polygon': s1['polygon'], 'name_en': s1.get('name_en'), 'name_ar': s1.get('name_ar')}})
            flash('Sectors swapped / تم تبديل القطاعات', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/merge', methods=['POST'])
@login_required
def sector_merge(project_id):
    sector_ids = request.form.getlist('sector_ids')
    if len(sector_ids) < 2:
        flash('Select at least 2 sectors to merge / اختر قطاعين على الأقل للدمج', 'warning')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    sectors = list(mongo.db.sectors.find({'id': {'$in': [int(s) for s in sector_ids]}, 'project_id': project_id}))
    if len(sectors) < 2:
        flash('Not enough sectors found / لم يتم العثور على قطاعات كافية', 'danger')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    from shapely.geometry import shape as shp_shape
    from shapely.ops import unary_union
    merged = None
    for s in sectors:
        geom = shp_shape(s['polygon'])
        merged = geom if merged is None else unary_union([merged, geom])

    from shapely.geometry import mapping
    merged_geojson = mapping(merged)

    new_sector = {
        'id': get_next_id('sectors'),
        'project_id': project_id,
        'name_en': f"Merged Sector",
        'name_ar': f"قطاع مدمج",
        'polygon': merged_geojson,
        'validated': False
    }
    mongo.db.sectors.insert_one(new_sector)
    mongo.db.sectors.delete_many({'id': {'$in': [int(s) for s in sector_ids]}})

    # Reassign zones from deleted sectors to new merged sector
    for sid in sector_ids:
        mongo.db.zones.update_many(
            {'project_id': project_id, 'sector_id': int(sid)},
            {'$set': {'sector_id': new_sector['id']}}
        )

    flash('Sectors merged / تم دمج القطاعات', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/<int:sector_id>/split', methods=['POST'])
@login_required
def sector_split(project_id, sector_id):
    sector = mongo.db.sectors.find_one({'id': sector_id, 'project_id': project_id})
    if not sector:
        flash('Sector not found / القطاع غير موجود', 'danger')
        return redirect(url_for('geometry.sectors_view', project_id=project_id))

    parts = request.form.get('parts', default=2, type=int)
    if parts < 2:
        parts = 2

    from shapely.geometry import shape as shp_shape
    geom = shp_shape(sector['polygon'])
    bounds = geom.bounds  # (minx, miny, maxx, maxy)
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    split_w = width / parts

    from shapely.geometry import box, mapping
    from shapely.ops import intersect as _  # noqa
    from shapely.geometry import shape

    for i in range(parts):
        x1 = minx + i * split_w
        x2 = minx + (i + 1) * split_w
        clip_box = box(x1, miny, x2, maxy)
        clipped = geom.intersection(clip_box)
        if clipped.is_empty:
            continue

        new_sector = {
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': f"{sector.get('name_en', 'Sector')} - {i+1}",
            'name_ar': f"{sector.get('name_ar', 'قطاع')} - {i+1}",
            'polygon': mapping(clipped),
            'validated': False
        }
        mongo.db.sectors.insert_one(new_sector)

    mongo.db.sectors.delete_one({'id': sector_id})
    flash(f'Sector split into {parts} parts / تم تقسيم القطاع إلى {parts} أجزاء', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/ai_regenerate', methods=['POST'])
@login_required
def sector_ai_regenerate(project_id):
    # Clear existing sectors
    mongo.db.sectors.delete_many({'project_id': project_id})
    mongo.db.zones.delete_many({'project_id': project_id})
    mongo.db.network_elements.delete_many({'project_id': project_id})
    mongo.db.tree_rows.delete_many({'project_id': project_id})
    mongo.db.trees.delete_many({'project_id': project_id})

    sectors = regenerate_sectors(project_id)
    flash(f'AI generated {len(sectors)} sectors / الذكاء الاصطناعي أنشأ {len(sectors)} قطاعات', 'success')
    return redirect(url_for('geometry.sectors_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/sectors/validate', methods=['POST'])
@login_required
def sector_validate(project_id):
    mongo.db.sectors.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'status': 'sectors_validated'}}
    )
    flash('All sectors validated / تم التحقق من جميع القطاعات', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


# === ZONES ===

@geometry_bp.route('/<int:project_id>/zones')
@login_required
def zones_view(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    sectors = list(mongo.db.sectors.find({'project_id': project_id}).sort('id', 1))
    zones = list(mongo.db.zones.find({'project_id': project_id}).sort('id', 1))
    for s in sectors + zones:
        s['_id'] = str(s['_id'])

    # Build a table-friendly structure
    table_data = []
    for sector in sectors:
        sector_zones = [z for z in zones if z.get('sector_id') == sector['id']]
        if sector_zones:
            for zone in sector_zones:
                table_data.append({
                    'sector': sector,
                    'zone': zone
                })
        else:
            table_data.append({
                'sector': sector,
                'zone': None
            })

    return render_template('geometry/zones.html', project=project, sectors=sectors, zones=zones, table_data=table_data)


@geometry_bp.route('/<int:project_id>/zones/add', methods=['POST'])
@login_required
def zone_add(project_id):
    sector_id = request.form.get('sector_id', type=int)
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    polygon_coords = request.form.get('polygon_coords', '').strip()

    if not sector_id or not polygon_coords:
        flash('Sector and polygon required / القطاع والمضلع مطلوبان', 'danger')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    try:
        coords = json.loads(polygon_coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    zone = {
        'id': get_next_id('zones'),
        'project_id': project_id,
        'sector_id': sector_id,
        'name_en': name_en or f"Zone {mongo.db.zones.count_documents({'project_id': project_id}) + 1}",
        'name_ar': name_ar or f"منطقة {mongo.db.zones.count_documents({'project_id': project_id}) + 1}",
        'polygon': {'type': 'Polygon', 'coordinates': [coords]},
        'validated': False
    }
    mongo.db.zones.insert_one(zone)
    flash('Zone added / تمت إضافة المنطقة', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/<int:zone_id>/edit', methods=['POST'])
@login_required
def zone_edit(project_id, zone_id):
    zone = mongo.db.zones.find_one({'id': zone_id, 'project_id': project_id})
    if not zone:
        flash('Zone not found / المنطقة غير موجودة', 'danger')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    polygon_coords = request.form.get('polygon_coords', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    if polygon_coords:
        try:
            coords = json.loads(polygon_coords)
            update['polygon'] = {'type': 'Polygon', 'coordinates': [coords]}
        except Exception:
            pass

    if update:
        mongo.db.zones.update_one({'id': zone_id}, {'$set': update})
        flash('Zone updated / تم تحديث المنطقة', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/<int:zone_id>/rename', methods=['POST'])
@login_required
def zone_rename(project_id, zone_id):
    update = {}
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    if name_en:
        update['name_en'] = name_en
    if name_ar:
        update['name_ar'] = name_ar
    if update:
        mongo.db.zones.update_one({'id': zone_id}, {'$set': update})
        flash('Zone renamed / تمت إعادة تسمية المنطقة', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/<int:zone_id>/split', methods=['POST'])
@login_required
def zone_split(project_id, zone_id):
    zone = mongo.db.zones.find_one({'id': zone_id, 'project_id': project_id})
    if not zone:
        flash('Zone not found / المنطقة غير موجودة', 'danger')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    parts = request.form.get('parts', default=2, type=int)
    if parts < 2:
        parts = 2

    from shapely.geometry import shape as shp_shape, box, mapping
    geom = shp_shape(zone['polygon'])
    bounds = geom.bounds
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    split_w = width / parts

    for i in range(parts):
        x1 = minx + i * split_w
        x2 = minx + (i + 1) * split_w
        clip_box = box(x1, miny, x2, maxy)
        clipped = geom.intersection(clip_box)
        if clipped.is_empty:
            continue

        new_zone = {
            'id': get_next_id('zones'),
            'project_id': project_id,
            'sector_id': zone['sector_id'],
            'name_en': f"{zone.get('name_en', 'Zone')} - {i+1}",
            'name_ar': f"{zone.get('name_ar', 'منطقة')} - {i+1}",
            'polygon': mapping(clipped),
            'validated': False
        }
        mongo.db.zones.insert_one(new_zone)

    mongo.db.zones.delete_one({'id': zone_id})
    flash(f'Zone split into {parts} parts / تم تقسيم المنطقة إلى {parts} أجزاء', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/merge', methods=['POST'])
@login_required
def zone_merge(project_id):
    zone_ids = request.form.getlist('zone_ids')
    if len(zone_ids) < 2:
        flash('Select at least 2 zones to merge / اختر منطقتين على الأقل للدمج', 'warning')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    zones = list(mongo.db.zones.find({'id': {'$in': [int(z) for z in zone_ids]}, 'project_id': project_id}))
    if len(zones) < 2:
        flash('Not enough zones found / لم يتم العثور على مناطق كافية', 'danger')
        return redirect(url_for('geometry.zones_view', project_id=project_id))

    from shapely.geometry import shape as shp_shape
    from shapely.ops import unary_union, mapping
    merged = None
    for z in zones:
        geom = shp_shape(z['polygon'])
        merged = geom if merged is None else unary_union([merged, geom])

    new_zone = {
        'id': get_next_id('zones'),
        'project_id': project_id,
        'sector_id': zones[0]['sector_id'],
        'name_en': 'Merged Zone',
        'name_ar': 'منطقة مدمجة',
        'polygon': mapping(merged),
        'validated': False
    }
    mongo.db.zones.insert_one(new_zone)
    mongo.db.zones.delete_many({'id': {'$in': [int(z) for z in zone_ids]}})
    flash('Zones merged / تم دمج المناطق', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/ai_regenerate', methods=['POST'])
@login_required
def zone_ai_regenerate(project_id):
    mongo.db.zones.delete_many({'project_id': project_id})
    zones = regenerate_zones(project_id)
    flash(f'AI generated {len(zones)} zones / الذكاء الاصطناعي أنشأ {len(zones)} مناطق', 'success')
    return redirect(url_for('geometry.zones_view', project_id=project_id))


@geometry_bp.route('/<int:project_id>/zones/validate', methods=['POST'])
@login_required
def zone_validate(project_id):
    mongo.db.zones.update_many(
        {'project_id': project_id},
        {'$set': {'validated': True}}
    )
    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'status': 'zones_validated'}}
    )
    flash('All zones validated / تم التحقق من جميع المناطق', 'success')
    return redirect(url_for('hydrology.main_pipe', project_id=project_id))
