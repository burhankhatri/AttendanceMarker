import os
import uuid
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from flask import current_app as flask_current_app
from database.db import db
from database.models import AudioRecording

audio_bp = Blueprint('audio', __name__)


@audio_bp.route('/record')
def record():
    recording = AudioRecording.query.first()
    return render_template('record_audio.html', recording=recording)


@audio_bp.route('/upload', methods=['POST'])
def upload():
    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']
        if audio_file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400

        os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)

        # Delete old recording if exists
        old = AudioRecording.query.first()
        if old:
            if os.path.exists(old.file_path):
                try:
                    os.remove(old.file_path)
                except Exception:
                    pass
            db.session.delete(old)
            db.session.commit()

        # Save new recording
        filename = f"present_{uuid.uuid4().hex}.webm"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        audio_file.save(filepath)

        # Convert to WAV for better compatibility
        wav_path = filepath.replace('.webm', '.wav')
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(filepath, format='webm')
            audio.export(wav_path, format='wav')
        except Exception:
            wav_path = filepath

        recording = AudioRecording(file_path=wav_path)
        db.session.add(recording)
        db.session.commit()

        return jsonify({'success': True, 'id': recording.id})
    except Exception as e:
        flask_current_app.logger.exception('Audio upload failed')
        return jsonify({'error': str(e)}), 500


@audio_bp.route('/playback/<int:recording_id>')
def playback(recording_id):
    recording = AudioRecording.query.get_or_404(recording_id)
    mime = 'audio/wav'
    if recording.file_path.lower().endswith('.webm'):
        mime = 'audio/webm'
    return send_file(recording.file_path, mimetype=mime)
