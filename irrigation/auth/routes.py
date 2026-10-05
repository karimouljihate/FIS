from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from irrigation.extensions import mongo
from irrigation.auth.user import User
from irrigation.models import get_next_id

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Username and password are required / اسم المستخدم وكلمة المرور مطلوبان', 'danger')
            return redirect(url_for('auth.register'))

        existing = mongo.db.users.find_one({'$or': [{'username': username}, {'email': email}]})
        if existing:
            flash('User already exists / المستخدم موجود بالفعل', 'danger')
            return redirect(url_for('auth.register'))

        user_doc = {
            'id': get_next_id('users'),
            'username': username,
            'email': email,
            'password_hash': User.set_password(password),
            'is_active': True
        }
        mongo.db.users.insert_one(user_doc)
        flash('Registration successful / تم التسجيل بنجاح', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user_doc = mongo.db.users.find_one({'username': username})
        if user_doc and User.check_password(user_doc['password_hash'], password):
            login_user(User(user_doc))
            flash('Welcome back / مرحبا بعودتك', 'success')
            return redirect(url_for('project.index'))

        flash('Invalid credentials / بيانات اعتماد غير صالحة', 'danger')
        return redirect(url_for('auth.login'))

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out / تم تسجيل الخروج', 'info')
    return redirect(url_for('auth.login'))
