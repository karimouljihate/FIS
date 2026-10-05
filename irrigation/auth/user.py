from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class User(UserMixin):
    def __init__(self, user_doc):
        self.id = str(user_doc['id'])
        self.username = user_doc['username']
        self.email = user_doc.get('email', '')
        self.password_hash = user_doc.get('password_hash', '')
        self.is_active_user = user_doc.get('is_active', True)

    def get_id(self):
        return self.id

    @property
    def is_active(self):
        return self.is_active_user

    @staticmethod
    def set_password(password):
        return generate_password_hash(password)

    @staticmethod
    def check_password(password_hash, password):
        return check_password_hash(password_hash, password)
