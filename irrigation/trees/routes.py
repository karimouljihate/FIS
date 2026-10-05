from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from irrigation.extensions import mongo
from irrigation.models import get_next_id
import json

trees_bp = Blueprint('trees', __name__)


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

    rows = list(mongo.db.tree_rows.find({'project_id': project_id}).sort('id', 1))
    for r in rows:
        r['_id'] = str(r['_id'])

    return render_template('trees/rows.html', project=project, zones=zones, rows=rows)


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
