let mediaRecorder = null;
let audioChunks = [];
let audioBlob = null;
let isRecording = false;

async function toggleRecording() {
    if (isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = () => {
            audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            const audioUrl = URL.createObjectURL(audioBlob);
            const preview = document.getElementById('audio-preview');
            preview.src = audioUrl;
            document.getElementById('playback-section').style.display = 'block';

            // Stop all tracks
            stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start();
        isRecording = true;

        const btn = document.getElementById('btn-record');
        btn.textContent = 'Stop Recording';
        btn.classList.add('recording');

        const status = document.getElementById('recorder-status');
        status.textContent = 'Recording... Say "present" or "present sir"';
        status.classList.add('recording');
    } catch (err) {
        alert('Microphone access denied. Please allow microphone access and try again.');
        console.error('Microphone error:', err);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        isRecording = false;

        const btn = document.getElementById('btn-record');
        btn.textContent = 'Start Recording';
        btn.classList.remove('recording');

        const status = document.getElementById('recorder-status');
        status.textContent = 'Recording complete. Preview and save below.';
        status.classList.remove('recording');
    }
}

async function saveRecording() {
    if (!audioBlob) {
        alert('No recording to save');
        return;
    }

    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');

    try {
        const uploadUrl = document.getElementById('audio-upload-config')?.dataset.uploadUrl;
        if (!uploadUrl) {
            throw new Error('Upload endpoint is missing');
        }

        const resp = await fetch(uploadUrl, {
            method: 'POST',
            body: formData
        });

        const rawText = await resp.text();
        const isJson = (resp.headers.get('content-type') || '').includes('application/json');
        let data = null;
        if (isJson) {
            try {
                data = JSON.parse(rawText);
            } catch (parseErr) {
                throw new Error('Server returned invalid JSON: ' + parseErr.message);
            }
        }

        if (data && data.success) {
            alert('Recording saved successfully!');
            window.location.reload();
        } else {
            const err = data ? data.error : rawText.slice(0, 180);
            const status = resp.status ? ` (${resp.status} ${resp.statusText})` : '';
            alert('Error saving: ' + (err || 'Unknown error') + status);
        }
    } catch (err) {
        alert('Error saving recording: ' + err.message);
    }
}

function discardRecording() {
    audioBlob = null;
    audioChunks = [];
    document.getElementById('playback-section').style.display = 'none';
    document.getElementById('recorder-status').textContent = 'Ready to record';
}
