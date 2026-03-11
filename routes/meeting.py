import re
import threading
import os
import json
import time
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, current_app
from database.db import db
from database.models import Settings, AudioRecording, MeetingSession

meeting_bp = Blueprint('meeting', __name__)


def _debug_log(run_id, hypothesis_id, location, message, data=None):
    payload = {
        'sessionId': 'c9aaec',
        'runId': run_id,
        'hypothesisId': hypothesis_id,
        'location': location,
        'message': message,
        'data': data or {},
        'timestamp': int(time.time() * 1000)
    }
    try:
        # region agent log
        with open('/Users/burhankhatri/Documents/MeetingAttender/Automated_Attending_System/.cursor/debug-c9aaec.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(payload, ensure_ascii=True) + '\n')
        # endregion
    except Exception:
        pass


@meeting_bp.route('/join')
def join():
    settings = Settings.get()
    has_recording = AudioRecording.query.first() is not None
    has_google = (
        settings.chrome_profile_path is not None and
        settings.chrome_profile_name is not None and
        os.path.isdir(os.path.join(settings.chrome_profile_path, settings.chrome_profile_name))
    ) if settings else False
    return render_template('join_meeting.html', has_recording=has_recording, has_google=has_google)


@meeting_bp.route('/start', methods=['POST'])
def start():
    meet_link = request.form.get('meet_link', '').strip()
    # region agent log
    _debug_log(
        run_id='initial',
        hypothesis_id='H1',
        location='routes/meeting.py:start',
        message='Received start request',
        data={'meet_link': meet_link}
    )
    # endregion

    if not re.match(r'https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}', meet_link):
        return jsonify({'error': 'Invalid Google Meet link format'}), 400

    recording = AudioRecording.query.first()
    if not recording:
        return jsonify({'error': 'Please record your "present" audio first'}), 400

    settings = Settings.get()
    if (
        not settings.chrome_profile_path
        or not settings.chrome_profile_name
        or not os.path.isdir(os.path.join(settings.chrome_profile_path, settings.chrome_profile_name))
    ):
        return jsonify({'error': 'Please set up your Google profile first'}), 400

    active = MeetingSession.query.filter(
        MeetingSession.status.notin_(['ended', 'error'])
    ).first()
    if active:
        return jsonify({'error': 'Already have an active meeting session', 'session_id': active.id}), 400

    session = MeetingSession(meet_link=meet_link, status='pending')
    session.add_log('Meeting session created')
    db.session.add(session)
    db.session.commit()
    # region agent log
    _debug_log(
        run_id='initial',
        hypothesis_id='H1',
        location='routes/meeting.py:start',
        message='Meeting session committed',
        data={'session_id': session.id}
    )
    # endregion

    session_id = session.id
    flask_app = current_app._get_current_object()
    sio = flask_app.extensions['socketio']

    def run_bot():
        from app import active_bots
        from services.meeting_bot import MeetingBot

        with flask_app.app_context():
            bot = MeetingBot(
                session_id=session_id,
                meet_link=meet_link,
                socketio=sio
            )
            active_bots[1] = bot
            try:
                bot.run()
            finally:
                active_bots.pop(1, None)

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    return jsonify({'success': True, 'session_id': session.id})


@meeting_bp.route('/status/<int:session_id>')
def status(session_id):
    session = MeetingSession.query.get_or_404(session_id)
    return render_template('meeting_status.html', session=session)


@meeting_bp.route('/status-data/<int:session_id>')
def status_data(session_id):
    session = MeetingSession.query.get_or_404(session_id)
    return jsonify({
        'status': session.status,
        'log': session.log,
        'meet_link': session.meet_link
    })


@meeting_bp.route('/stop/<int:session_id>', methods=['POST'])
def stop(session_id):
    session = MeetingSession.query.get_or_404(session_id)

    from app import active_bots
    bot = active_bots.get(1)
    if bot:
        bot.stop()

    session.status = 'ended'
    session.ended_at = datetime.utcnow()
    session.add_log('Meeting ended by user')
    db.session.commit()

    return jsonify({'success': True})
