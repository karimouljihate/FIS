import os
import re
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from irrigation.extensions import mongo
from irrigation.models import get_next_id, serialize_doc
from irrigation.utils.kml import parse_kml_content, parse_kmz_file, export_project_to_kml
from bson import ObjectId

project_bp = Blueprint('project', __name__)


def _default_spec():
    """Default project specification (owner, configuration, rules, tree plan)."""
    return {
        'owner': {'name': '', 'email': '', 'phone': ''},
        'config': {
            'zones_per_sector': 2,
            'row_spacing_m': 3.0,
            'tree_spacing_m': 4.0,
            'emitter_flow_lph': 2.0,
            'emitters_per_tree': 2,
        },
        'rules': {
            'min_sectors': 1,
            'max_sectors': 8,
            'min_sector_area_m2': 5000,
            'max_sector_area_m2': 50000,
            'min_zones_per_sector': 1,
            'max_zones_per_sector': 8,
        },
        'tree_plan': [
            {'variety_en': 'Olive', 'variety_ar': 'زيتون', 'percentage': 60, 'scope': 'per_sector'},
            {'variety_en': 'Almond', 'variety_ar': 'لوز', 'percentage': 40, 'scope': 'per_sector'},
        ],
    }


def _deep_merge(defaults, override):
    """Merge override dict into defaults recursively (keeps default keys)."""
    result = dict(defaults)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_spec(project):
    spec = _default_spec()
    if project and project.get('spec'):
        spec = _deep_merge(spec, project['spec'])
    return spec


def _spec_from_form(request):
    """Build a spec dict from the project settings form."""
    def f(name, cast=str, default=None):
        value = request.form.get(name, '').strip()
        if not value:
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    varieties_en = request.form.getlist('tree_variety_en')
    varieties_ar = request.form.getlist('tree_variety_ar')
    percentages = request.form.getlist('tree_percentage')
    scopes = request.form.getlist('tree_scope')

    tree_plan = []
    for i, en in enumerate(varieties_en):
        en = en.strip()
        if not en:
            continue
        tree_plan.append({
            'variety_en': en,
            'variety_ar': varieties_ar[i].strip() if i < len(varieties_ar) else '',
            'percentage': percentages[i].strip() if i < len(percentages) else '',
            'scope': scopes[i].strip() if i < len(scopes) else 'per_sector',
        })

    return {
        'owner': {
            'name': f('owner_name'),
            'email': f('owner_email'),
            'phone': f('owner_phone'),
        },
        'config': {
            'zones_per_sector': f('zones_per_sector', int, 2),
            'row_spacing_m': f('row_spacing_m', float, 3.0),
            'tree_spacing_m': f('tree_spacing_m', float, 4.0),
            'emitter_flow_lph': f('emitter_flow_lph', float, 2.0),
            'emitters_per_tree': f('emitters_per_tree', int, 2),
        },
        'rules': {
            'min_sectors': f('min_sectors', int, 1),
            'max_sectors': f('max_sectors', int, 8),
            'min_sector_area_m2': f('min_sector_area_m2', int, 5000),
            'max_sector_area_m2': f('max_sector_area_m2', int, 50000),
            'min_zones_per_sector': f('min_zones_per_sector', int, 1),
            'max_zones_per_sector': f('max_zones_per_sector', int, 8),
        },
        'tree_plan': tree_plan or _default_spec()['tree_plan'],
    }


@project_bp.route('/')
@login_required
def index():
    projects = list(mongo.db.projects.find({'user_id': int(current_user.id)}).sort('created_at', -1))
    for p in projects:
        p['_id'] = str(p['_id'])
    return render_template('project/index.html', projects=projects)


@project_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        location = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        water_source_lat = request.form.get('water_source_lat', '').strip()
        water_source_lng = request.form.get('water_source_lng', '').strip()

        # Manual land boundary coordinates
        boundary_coords = request.form.get('boundary_coords', '').strip()

        spec = _spec_from_form(request)

        project_doc = {
            'id': get_next_id('projects'),
            'user_id': int(current_user.id),
            'name': name,
            'location': location,
            'description': description,
            'water_source': {},
            'boundary': None,
            'kml_file': None,
            'status': 'created',
            'spec': spec,
            'created_at': __import__('datetime').datetime.utcnow()
        }

        if water_source_lat and water_source_lng:
            project_doc['water_source'] = {
                'type': 'Point',
                'coordinates': [float(water_source_lng), float(water_source_lat)]
            }

        if boundary_coords:
            try:
                import json
                coords = json.loads(boundary_coords)
                project_doc['boundary'] = {
                    'type': 'Polygon',
                    'coordinates': [coords]
                }
            except Exception:
                flash('Invalid boundary coordinates / إحداثيات الحدود غير صالحة', 'danger')

        result = mongo.db.projects.insert_one(project_doc)
        project_id = project_doc['id']

        flash('Project created successfully / تم إنشاء المشروع بنجاح', 'success')
        return redirect(url_for('project.detail', project_id=project_id))

    return render_template('project/create.html')


@project_bp.route('/<int:project_id>')
@login_required
def detail(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return redirect(url_for('project.index'))

    project['_id'] = str(project['_id'])
    spec = _get_spec(project)

    # Get geometry elements for the map
    sectors = list(mongo.db.sectors.find({'project_id': project_id}))
    zones = list(mongo.db.zones.find({'project_id': project_id}))
    network = list(mongo.db.network_elements.find({'project_id': project_id}))
    tree_rows = list(mongo.db.tree_rows.find({'project_id': project_id}))
    trees = list(mongo.db.trees.find({'project_id': project_id}))

    for s in sectors + zones + network + tree_rows + trees:
        s['_id'] = str(s['_id'])

    return render_template('project/detail.html',
                           project=project,
                           spec=spec,
                           sectors=sectors,
                           zones=zones,
                           network=network,
                           tree_rows=tree_rows,
                           trees=trees)


@project_bp.route('/<int:project_id>/settings', methods=['POST'])
@login_required
def settings(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return redirect(url_for('project.index'))

    spec = _spec_from_form(request)
    if 'location' in request.form:
        mongo.db.projects.update_one(
            {'id': project_id, 'user_id': int(current_user.id)},
            {'$set': {'spec': spec, 'location': request.form.get('location', '').strip()}}
        )
    else:
        mongo.db.projects.update_one(
            {'id': project_id, 'user_id': int(current_user.id)},
            {'$set': {'spec': spec}}
        )
    flash('Project settings saved / تم حفظ إعدادات المشروع', 'success')
    return redirect(url_for('project.detail', project_id=project_id))


def _store_kml_features(project_id, project, filename, features):
    from shapely.geometry import shape as shp_shape

    polygon_features = [
        feature for feature in features
        if feature['geometry'].get('type') in ('Polygon', 'MultiPolygon')
    ]
    boundary_feature = max(
        polygon_features,
        key=lambda feature: shp_shape(feature['geometry']).area,
        default=None
    )
    water_feature = next(
        (
            feature for feature in features
            if feature['geometry'].get('type') in ('Point', 'MultiPoint')
            and re.search(r'well|water|source', feature['name'], re.IGNORECASE)
        ),
        None
    )
    sector_features = [
        feature for feature in polygon_features
        if feature is not boundary_feature
        and re.match(r'^(?:s\s*\d+|sector\s*\d*)$', feature['name'].strip(), re.IGNORECASE)
    ]

    for feature in features:
        if feature is boundary_feature:
            feature['category'] = 'boundary'
        elif feature in sector_features:
            feature['category'] = 'sector'
        elif feature is water_feature:
            feature['category'] = 'water_source'
        else:
            feature['category'] = 'overlay'

    mongo.db.sectors.delete_many({'project_id': project_id, 'source': 'kml'})
    for feature in sector_features:
        mongo.db.sectors.insert_one({
            'id': get_next_id('sectors'),
            'project_id': project_id,
            'name_en': feature['name'],
            'name_ar': '',
            'polygon': feature['geometry'],
            'validated': False,
            'source': 'kml'
        })

    update = {
        'kml_file': filename,
        'kml_features': features
    }
    if boundary_feature:
        update['boundary'] = boundary_feature['geometry']
    if water_feature:
        update['water_source'] = water_feature['geometry']
    mongo.db.projects.update_one(
        {'id': project_id, 'user_id': project['user_id']},
        {'$set': update}
    )
    return boundary_feature is not None


@project_bp.route('/<int:project_id>/upload_kml', methods=['POST'])
@login_required
def upload_kml(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return redirect(url_for('project.index'))

    if 'kml_file' not in request.files:
        flash('No file uploaded / لم يتم رفع ملف', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    file = request.files['kml_file']
    if file.filename == '':
        flash('No file selected / لم يتم اختيار ملف', 'danger')
        return redirect(url_for('project.detail', project_id=project_id))

    filename = secure_filename(file.filename)
    upload_dir = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'project_{project_id}_{filename}')
    file.save(filepath)

    features = []
    if filename.lower().endswith('.kmz'):
        features = parse_kmz_file(filepath)
    elif filename.lower().endswith('.kml'):
        with open(filepath, 'r', encoding='utf-8') as f:
            features = parse_kml_content(f.read())

    if features:
        has_boundary = _store_kml_features(project_id, project, filename, features)
        if has_boundary:
            flash(f'Loaded {len(features)} features from KML / تم تحميل {len(features)} عنصر', 'success')
        else:
            flash(f'Loaded {len(features)} KML features; no polygon boundary was found / تم تحميل {len(features)} عنصر دون حدود مضلع', 'warning')
    else:
        flash('Could not parse KML file / تعذر تحليل ملف KML', 'danger')

    return redirect(url_for('project.detail', project_id=project_id))


@project_bp.route('/<int:project_id>/export_kml')
@login_required
def export_kml(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return redirect(url_for('project.index'))

    kml_content = export_project_to_kml(project_id)
    if not kml_content:
        flash('No geometry to export / لا توجد هندسة للتصدير', 'warning')
        return redirect(url_for('project.detail', project_id=project_id))

    import io
    return send_file(
        io.BytesIO(kml_content.encode('utf-8')),
        as_attachment=True,
        download_name=f'project_{project_id}.kml',
        mimetype='application/vnd.google-earth.kml+xml'
    )


@project_bp.route('/<int:project_id>/delete', methods=['POST'])
@login_required
def delete(project_id):
    mongo.db.projects.delete_one({'id': project_id, 'user_id': int(current_user.id)})
    mongo.db.sectors.delete_many({'project_id': project_id})
    mongo.db.zones.delete_many({'project_id': project_id})
    mongo.db.network_elements.delete_many({'project_id': project_id})
    mongo.db.tree_rows.delete_many({'project_id': project_id})
    mongo.db.trees.delete_many({'project_id': project_id})
    flash('Project deleted / تم حذف المشروع', 'info')
    return redirect(url_for('project.index'))
