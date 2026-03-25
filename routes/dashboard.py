import os

from flask import Blueprint, redirect, render_template, url_for

from database.models import AudioRecording, MeetingSession, Settings

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
def dashboard():
    settings = Settings.get()
    if not settings.display_name:
        return redirect(url_for('settings.setup'))

    has_recording = AudioRecording.query.first() is not None
    has_profile = (
        bool(settings.chrome_profile_path)
        and bool(settings.chrome_profile_name)
        and os.path.isdir(settings.chrome_profile_path)
        and os.path.isdir(os.path.join(settings.chrome_profile_path, settings.chrome_profile_name))
    )

    active_session = MeetingSession.query.filter(
        MeetingSession.status.notin_(['ended', 'error', 'needs_reauth', 'unsupported_flow'])
    ).first()

    return render_template(
        'dashboard.html',
        settings=settings,
        has_recording=has_recording,
        has_profile=has_profile,
        active_session=active_session,
    )
