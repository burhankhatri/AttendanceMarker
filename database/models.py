import json
from datetime import datetime
from database.db import db


class Settings(db.Model):
    """Single-row settings table. No auth needed — one user app."""
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(120), nullable=False, default='')
    roll_number = db.Column(db.String(50), nullable=True)
    _name_variants = db.Column('name_variants', db.Text, default='[]')
    chrome_profile_path = db.Column(db.String(500), nullable=True)
    chrome_profile_name = db.Column(db.String(120), nullable=True)

    @property
    def name_variants(self):
        return json.loads(self._name_variants) if self._name_variants else []

    @name_variants.setter
    def name_variants(self, variants):
        self._name_variants = json.dumps(variants)

    def get_all_detection_terms(self):
        terms = set(v.lower().strip() for v in self.name_variants if v and v.strip())
        if self.display_name:
            display_name = self.display_name.lower().strip()
            terms.add(display_name)
            for part in display_name.split():
                if len(part) > 2:
                    terms.add(part)
        if self.roll_number:
            terms.add(self.roll_number.lower())
        return list(terms)

    @staticmethod
    def get():
        """Get or create the single settings row."""
        s = Settings.query.first()
        if not s:
            s = Settings(id=1, display_name='')
            db.session.add(s)
            db.session.commit()
        return s


class AudioRecording(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MeetingSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    meet_link = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(50), default='pending')
    _log = db.Column('log', db.Text, default='[]')
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)

    @property
    def log(self):
        return json.loads(self._log) if self._log else []

    def add_log(self, message):
        logs = self.log
        logs.append({
            'time': datetime.utcnow().strftime('%H:%M:%S'),
            'message': message
        })
        self._log = json.dumps(logs)
