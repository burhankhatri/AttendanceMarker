import os
from flask import Flask, redirect, url_for
from flask_socketio import SocketIO
from sqlalchemy import text
from database.db import db
import config

socketio = SocketIO()
active_bots = {}


def _remove_user_id_from_single_user_tables():
    legacy_tables = {
        'audio_recording': (
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
            "file_path VARCHAR(500) NOT NULL, "
            "created_at DATETIME"
        ),
        'meeting_session': (
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
            "meet_link VARCHAR(500) NOT NULL, "
            "status VARCHAR(50) DEFAULT 'pending', "
            "log TEXT DEFAULT '[]', "
            "started_at DATETIME, "
            "ended_at DATETIME"
        )
    }

    with db.engine.connect() as conn:
        def table_has_user_id(table_name):
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            col_names = {row[1] for row in rows}
            return 'user_id' in col_names

        for table_name, create_cols in legacy_tables.items():
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name=:name"
                ),
                {'name': table_name}
            ).fetchone()
            if not result:
                continue

            if not table_has_user_id(table_name):
                continue

            with db.engine.begin() as migrate_conn:
                migrate_conn.execute(text(f"""
                    CREATE TABLE {table_name}_single_user_new ({create_cols});
                """))
                if table_name == 'audio_recording':
                    migrate_conn.execute(text(f"""
                        INSERT INTO {table_name}_single_user_new (id, file_path, created_at)
                        SELECT id, file_path, created_at FROM {table_name};
                    """))
                else:
                    migrate_conn.execute(text(f"""
                        INSERT INTO {table_name}_single_user_new
                        (id, meet_link, status, log, started_at, ended_at)
                        SELECT id, meet_link, status, log, started_at, ended_at FROM {table_name};
                    """))
                migrate_conn.execute(text(f"DROP TABLE {table_name};"))
                migrate_conn.execute(
                    text(f"ALTER TABLE {table_name}_single_user_new RENAME TO {table_name};")
                )


def _ensure_single_user_profile_columns():
    rows = []
    with db.engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='settings'"
            )
        ).fetchall()
        if not tables:
            return

        cols = {row[1] for row in conn.execute(text('PRAGMA table_info(settings)')).fetchall()}
        if 'chrome_profile_name' not in cols:
            conn.execute(text("ALTER TABLE settings ADD COLUMN chrome_profile_name VARCHAR(120)"))

        rows = conn.execute(
            text('SELECT id, chrome_profile_path, chrome_profile_name FROM settings')
        ).fetchall()

    with db.engine.begin() as migrate_conn:
        for row in rows:
            settings_id, profile_path, profile_name = row
            if profile_path and not profile_name:
                basename = os.path.basename(profile_path)
                root = os.path.dirname(profile_path)
                if basename and root and basename.lower() == 'default' and os.path.isdir(root):
                    migrate_conn.execute(
                        text(
                            'UPDATE settings '
                            'SET chrome_profile_path = :root, chrome_profile_name = :name '
                            'WHERE id = :id'
                        ),
                        {'root': root, 'name': 'Default', 'id': settings_id}
                    )


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = config.SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH

    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(config.BASE_DIR, 'instance'), exist_ok=True)
    os.makedirs(config.CHROME_PROFILES_DIR, exist_ok=True)

    db.init_app(app)
    socketio.init_app(app, async_mode='threading', cors_allowed_origins='*')

    from routes.dashboard import dashboard_bp
    from routes.audio import audio_bp
    from routes.google_profile import google_bp
    from routes.meeting import meeting_bp
    from routes.settings import settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(audio_bp, url_prefix='/audio')
    app.register_blueprint(google_bp, url_prefix='/google')
    app.register_blueprint(meeting_bp, url_prefix='/meeting')
    app.register_blueprint(settings_bp, url_prefix='/settings')

    from services.audio_bridge import register_socketio_events
    register_socketio_events(socketio)

    @app.route('/')
    def index():
        return redirect(url_for('dashboard.dashboard'))

    with app.app_context():
        _remove_user_id_from_single_user_tables()
        from database.models import Settings, AudioRecording, MeetingSession
        db.create_all()
        _ensure_single_user_profile_columns()
        Settings.get()  # Ensure settings row exists

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)
