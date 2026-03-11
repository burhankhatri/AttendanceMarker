import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "instance", "app.db")}'
SQLALCHEMY_TRACK_MODIFICATIONS = False
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'audio')
CHROME_PROFILES_DIR = os.path.expanduser('~/.chrome-meet-profiles')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
