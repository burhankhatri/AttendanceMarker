import os
import threading
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from database.db import db
from database.models import AudioRecording, MeetingSession, Settings
from services.provider_routing import resolve_provider, validate_link_for_provider

meeting_bp = Blueprint('meeting', __name__)

TERMINAL_STATES = {'ended', 'error', 'needs_reauth', 'unsupported_flow'}


def _is_browser_profile_ready(settings):
    if not settings:
        return False

    return (
        bool(settings.chrome_profile_path)
        and bool(settings.chrome_profile_name)
        and os.path.isdir(os.path.join(settings.chrome_profile_path, settings.chrome_profile_name))
    )


@meeting_bp.route('/join')
def join():
    settings = Settings.get()
    has_recording = AudioRecording.query.first() is not None
    has_profile = _is_browser_profile_ready(settings)
    return render_template('join_meeting.html', has_recording=has_recording, has_profile=has_profile)


@meeting_bp.route('/start', methods=['POST'])
def start():
    meeting_link = (
        request.form.get('meeting_link', '').strip()
        or request.form.get('meet_link', '').strip()
    )
    provider_override = request.form.get('provider', '').strip()
    provider = resolve_provider(meeting_link, provider_override)

    if not provider:
        return jsonify({'error': 'Unsupported meeting provider. Use a Google Meet or Microsoft Teams link.'}), 400

    if not validate_link_for_provider(meeting_link, provider):
        return jsonify({'error': f'Invalid {provider.title()} meeting link format'}), 400

    recording = AudioRecording.query.first()
    if not recording:
        return jsonify({'error': 'Please record your "present" audio first'}), 400

    settings = Settings.get()
    if not _is_browser_profile_ready(settings):
        return jsonify({'error': 'Please set up your browser profile first'}), 400

    active = MeetingSession.query.filter(MeetingSession.status.notin_(list(TERMINAL_STATES))).first()
    if active:
        return jsonify({'error': 'Already have an active meeting session', 'session_id': active.id}), 400

    session = MeetingSession(meet_link=meeting_link, provider=provider, status='pending')
    session.add_log(f'{provider.title()} meeting session created')
    db.session.add(session)
    db.session.commit()

    session_id = session.id
    flask_app = current_app._get_current_object()
    sio = flask_app.extensions['socketio']

    def run_bot():
        from app import active_bots
        from services.meeting_bot import MeetingBot

        with flask_app.app_context():
            bot = MeetingBot(
                session_id=session_id,
                meeting_link=meeting_link,
                provider=provider,
                socketio=sio,
            )
            active_bots[session_id] = bot
            try:
                bot.run()
            finally:
                active_bots.pop(session_id, None)

    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()

    return jsonify({'success': True, 'session_id': session.id, 'provider': provider})


@meeting_bp.route('/status/<int:session_id>')
def status(session_id):
    session = db.session.get(MeetingSession, session_id)
    if session is None:
        abort(404)
    return render_template('meeting_status.html', session=session)


@meeting_bp.route('/status-data/<int:session_id>')
def status_data(session_id):
    session = db.session.get(MeetingSession, session_id)
    if session is None:
        abort(404)
    return jsonify(
        {
            'status': session.status,
            'log': session.log,
            'meeting_link': session.meet_link,
            'meet_link': session.meet_link,
            'provider': session.provider,
        }
    )


@meeting_bp.route('/stop/<int:session_id>', methods=['POST'])
def stop(session_id):
    session = db.session.get(MeetingSession, session_id)
    if session is None:
        abort(404)

    from app import active_bots

    bot = active_bots.get(session_id)
    if bot:
        bot.stop()

    session.status = 'ended'
    session.ended_at = datetime.utcnow()
    session.add_log('Meeting ended by user')
    db.session.commit()

    return jsonify({'success': True})
