from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id
import json

hydrology_bp = Blueprint('hydrology', __name__)


def get_project_or_redirect(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return None
    project['_id'] = str(project['_id'])
    return project


def add_network_element(project_id, elem_type, name_en, name_ar, geometry, properties=None):
    elem = {
        'id': get_next_id('network_elements'),
        'project_id': project_id,
        'type': elem_type,
        'name_en': name_en,
        'name_ar': name_ar,
        'geometry': geometry,
        'properties': properties or {},
        'validated': False
    }
    mongo.db.network_elements.insert_one(elem)
    return elem


# === MAIN PIPE ===

@hydrology_bp.route('/<int:project_id>/main_pipe')
@login_required
def main_pipe(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    for s in sectors:
        s['_id'] = str(s['_id'])

    main_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'main_pipe'}))
    for p in main_pipes:
        p['_id'] = str(p['_id'])

    water_source = project.get('water_source')

    return render_template('hydrology/main_pipe.html', project=project, sectors=sectors, main_pipes=main_pipes, water_source=water_source)


@hydrology_bp.route('/<int:project_id>/main_pipe/add', methods=['POST'])
@login_required
def main_pipe_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=110, type=int)

    if not coords:
        flash('Pipe path required / مسار الأنبوب مطلوب', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.main_pipe', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}

    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'main_pipe'})
    add_network_element(project_id, 'main_pipe',
                        f'Main Pipe {count+1}', f'الأنبوب الرئيسي {count+1}',
                        geometry, {'diameter': diameter})

    flash('Main pipe added / تمت إضافة الأنبوب الرئيسي', 'success')
    return redirect(url_for('hydrology.main_pipe', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/main_pipe/validate', methods=['POST'])
@login_required
def main_pipe_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'main_pipe'},
        {'$set': {'validated': True}}
    )
    flash('Main pipe validated / تم التحقق من الأنبوب الرئيسي', 'success')
    return redirect(url_for('hydrology.sub_pipe', project_id=project_id))


# === SUB PIPE ===

@hydrology_bp.route('/<int:project_id>/sub_pipe')
@login_required
def sub_pipe(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for z in zones:
        z['_id'] = str(z['_id'])

    sub_pipes = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'sub_pipe'}))
    for p in sub_pipes:
        p['_id'] = str(p['_id'])

    return render_template('hydrology/sub_pipe.html', project=project, zones=zones, sub_pipes=sub_pipes)


@hydrology_bp.route('/<int:project_id>/sub_pipe/add', methods=['POST'])
@login_required
def sub_pipe_add(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=63, type=int)
    zone_id = request.form.get('zone_id', type=int)

    if not coords:
        flash('Pipe path required / مسار الأنبوب مطلوب', 'danger')
        return redirect(url_for('hydrology.sub_pipe', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.sub_pipe', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}
    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'sub_pipe'})

    add_network_element(project_id, 'sub_pipe',
                        f'Sub Pipe {count+1}', f'الأنبوب الفرعي {count+1}',
                        geometry, {'diameter': diameter, 'zone_id': zone_id})

    flash('Sub pipe added / تمت إضافة الأنبوب الفرعي', 'success')
    return redirect(url_for('hydrology.sub_pipe', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/sub_pipe/validate', methods=['POST'])
@login_required
def sub_pipe_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'sub_pipe'},
        {'$set': {'validated': True}}
    )
    flash('Sub pipes validated / تم التحقق من الأنابيب الفرعية', 'success')
    return redirect(url_for('trees.rows_view', project_id=project_id))


# === VALVES ===

@hydrology_bp.route('/<int:project_id>/valves')
@login_required
def valves(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    for s in sectors + zones:
        s['_id'] = str(s['_id'])

    master_valves = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'master_valve'}))
    sector_valves = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'sector_valve'}))
    zone_valves = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'zone_valve'}))
    for v in master_valves + sector_valves + zone_valves:
        v['_id'] = str(v['_id'])

    return render_template('hydrology2/valves.html', project=project,
                           sectors=sectors, zones=zones,
                           master_valves=master_valves,
                           sector_valves=sector_valves,
                           zone_valves=zone_valves)


@hydrology_bp.route('/<int:project_id>/valves/add', methods=['POST'])
@login_required
def valve_add(project_id):
    valve_type = request.form.get('valve_type', '')
    coords = request.form.get('coordinates', '').strip()
    sector_id = request.form.get('sector_id', type=int)
    zone_id = request.form.get('zone_id', type=int)

    if not coords or not valve_type:
        flash('Valve type and location required / نوع وموقع الصمام مطلوبان', 'danger')
        return redirect(url_for('hydrology.valves', project_id=project_id))

    try:
        coord = json.loads(coords)
        if isinstance(coord, list) and len(coord) == 2:
            geometry = {'type': 'Point', 'coordinates': coord}
        else:
            flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
            return redirect(url_for('hydrology.valves', project_id=project_id))
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.valves', project_id=project_id))

    name_map = {
        'master_valve': ('Master Valve', 'الصمام الرئيسي'),
        'sector_valve': ('Sector Valve', 'صمام القطاع'),
        'zone_valve': ('Zone Valve', 'صمام المنطقة'),
    }

    if valve_type not in name_map:
        flash('Invalid valve type / نوع صمام غير صالح', 'danger')
        return redirect(url_for('hydrology.valves', project_id=project_id))

    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': valve_type})
    name_en, name_ar = name_map[valve_type]
    props = {}
    if sector_id:
        props['sector_id'] = sector_id
    if zone_id:
        props['zone_id'] = zone_id

    add_network_element(project_id, valve_type,
                        f'{name_en} {count+1}', f'{name_ar} {count+1}',
                        geometry, props)

    flash(f'{name_en} added / تمت إضافة {name_ar}', 'success')
    return redirect(url_for('hydrology.valves', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/valves/validate', methods=['POST'])
@login_required
def valves_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': {'$in': ['master_valve', 'sector_valve', 'zone_valve']}},
        {'$set': {'validated': True}}
    )
    flash('Valves validated / تم التحقق من الصمامات', 'success')
    return redirect(url_for('hydrology.dripline', project_id=project_id))


# === DRIP LINE ===

@hydrology_bp.route('/<int:project_id>/dripline')
@login_required
def dripline(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    tree_rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    for r in tree_rows:
        r['_id'] = str(r['_id'])

    driplines = list(mongo.db.network_elements.find({'project_id': project_id, 'type': 'dripline'}))
    for d in driplines:
        d['_id'] = str(d['_id'])

    return render_template('hydrology2/dripline.html', project=project, tree_rows=tree_rows, driplines=driplines)


@hydrology_bp.route('/<int:project_id>/dripline/add', methods=['POST'])
@login_required
def dripline_add(project_id):
    coords = request.form.get('coordinates', '').strip()
    diameter = request.form.get('diameter', default=16, type=int)
    row_id = request.form.get('row_id', type=int)

    if not coords:
        flash('Dripline path required / مسار خط التنقيط مطلوب', 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))

    try:
        coords_list = json.loads(coords)
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.dripline', project_id=project_id))

    geometry = {'type': 'LineString', 'coordinates': coords_list}
    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': 'dripline'})

    add_network_element(project_id, 'dripline',
                        f'Drip Line {count+1}', f'خط التنقيط {count+1}',
                        geometry, {'diameter': diameter, 'row_id': row_id})

    flash('Dripline added / تمت إضافة خط التنقيط', 'success')
    return redirect(url_for('hydrology.dripline', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/dripline/validate', methods=['POST'])
@login_required
def dripline_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': 'dripline'},
        {'$set': {'validated': True}}
    )
    flash('Dripline validated / تم التحقق من خط التنقيط', 'success')
    return redirect(url_for('hydrology.network_elements', project_id=project_id))


# === NETWORK ELEMENTS ===

@hydrology_bp.route('/<int:project_id>/network')
@login_required
def network_elements(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    elem_types = ['check_valve', 'air_release', 'pressure_reducer', 'other']
    elements = {}
    for t in elem_types:
        elements[t] = list(mongo.db.network_elements.find({'project_id': project_id, 'type': t}))
        for e in elements[t]:
            e['_id'] = str(e['_id'])

    return render_template('hydrology2/network.html', project=project, elements=elements)


@hydrology_bp.route('/<int:project_id>/network/add', methods=['POST'])
@login_required
def network_add(project_id):
    elem_type = request.form.get('elem_type', '')
    coords = request.form.get('coordinates', '').strip()
    name_en = request.form.get('name_en', '').strip()
    name_ar = request.form.get('name_ar', '').strip()
    notes = request.form.get('notes', '').strip()

    if not coords or not elem_type:
        flash('Element type and location required / نوع وموقع العنصر مطلوبان', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    try:
        coord = json.loads(coords)
        if isinstance(coord, list) and len(coord) == 2:
            geometry = {'type': 'Point', 'coordinates': coord}
        else:
            flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
            return redirect(url_for('hydrology.network_elements', project_id=project_id))
    except Exception:
        flash('Invalid coordinates / إحداثيات غير صالحة', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    type_map = {
        'check_valve': ('Check Valve', 'صمام عدم رجوع'),
        'air_release': ('Air Release', 'تنفيس الهواء'),
        'pressure_reducer': ('Pressure Reducer', 'خافض الضغط'),
        'other': ('Other Element', 'عنصر آخر'),
    }

    if elem_type not in type_map:
        flash('Invalid element type / نوع عنصر غير صالح', 'danger')
        return redirect(url_for('hydrology.network_elements', project_id=project_id))

    count = mongo.db.network_elements.count_documents({'project_id': project_id, 'type': elem_type})
    default_en, default_ar = type_map[elem_type]
    add_network_element(project_id, elem_type,
                        name_en or f'{default_en} {count+1}',
                        name_ar or f'{default_ar} {count+1}',
                        geometry, {'notes': notes})

    flash('Network element added / تمت إضافة عنصر الشبكة', 'success')
    return redirect(url_for('hydrology.network_elements', project_id=project_id))


@hydrology_bp.route('/<int:project_id>/network/validate', methods=['POST'])
@login_required
def network_validate(project_id):
    mongo.db.network_elements.update_many(
        {'project_id': project_id, 'type': {'$in': ['check_valve', 'air_release', 'pressure_reducer', 'other']}},
        {'$set': {'validated': True}}
    )
    mongo.db.projects.update_one(
        {'id': project_id},
        {'$set': {'status': 'complete'}}
    )
    flash('Network validated / تم التحقق من الشبكة', 'success')
    return redirect(url_for('project.detail', project_id=project_id))
