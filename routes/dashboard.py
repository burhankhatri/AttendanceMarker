from flask import Blueprint, render_template, redirect, url_for
from database.models import Settings, AudioRecording, MeetingSession
import os

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
def dashboard():
    settings = Settings.get()
    if not settings.display_name:
        return redirect(url_for('settings.setup'))

    has_recording = AudioRecording.query.first() is not None
    has_google = (
        settings.chrome_profile_path is not None
        and settings.chrome_profile_name is not None
        and os.path.isdir(settings.chrome_profile_path)
        and os.path.isdir(os.path.join(settings.chrome_profile_path, settings.chrome_profile_name))
    )
    active_session = MeetingSession.query.filter(
        MeetingSession.status.notin_(['ended', 'error'])
    ).first()
    return render_template('dashboard.html',
                           settings=settings,
                           has_recording=has_recording,
                           has_google=has_google,
                           active_session=active_session)
