from flask_pymongo import PyMongo
from flask_login import LoginManager

mongo = PyMongo()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'warning'
