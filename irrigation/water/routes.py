from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from irrigation.extensions import mongo

water_bp = Blueprint('water', __name__)


def get_project_or_redirect(project_id):
    project = mongo.db.projects.find_one({'id': project_id, 'user_id': int(current_user.id)})
    if not project:
        flash('Project not found / المشروع غير موجود', 'danger')
        return None
    project['_id'] = str(project['_id'])
    return project


@water_bp.route('/<int:project_id>/well')
@login_required
def well(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    return render_template('water/well.html', project=project)


@water_bp.route('/<int:project_id>/basin')
@login_required
def basin(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    return render_template('water/basin.html', project=project)


@water_bp.route('/<int:project_id>/other')
@login_required
def other(project_id):
    project = get_project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    return render_template('water/other.html', project=project)