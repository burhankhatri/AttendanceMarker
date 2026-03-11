"""
Audio bridge service - handles WebRTC audio interception and playback injection.
Provides JavaScript code that gets injected into the Chrome browser to:
1. Capture incoming meeting audio via WebRTC interception
2. Play pre-recorded audio by replacing the microphone track
"""


def get_webrtc_intercept_js(session_id):
    """
    JavaScript to monkey-patch RTCPeerConnection for audio capture.
    Injected via CDP before the page loads so it intercepts all WebRTC connections.
    """
    return f'''
    (function() {{
        // Store references to peer connections and senders
        window.__meetBot_peerConnections = [];
        window.__meetBot_audioSenders = [];
        window.__meetBot_sessionId = {session_id};

        const OriginalRTCPeerConnection = window.RTCPeerConnection;

        window.RTCPeerConnection = function(...args) {{
            const pc = new OriginalRTCPeerConnection(...args);
            window.__meetBot_peerConnections.push(pc);

            // Intercept addTrack to capture audio senders
            const origAddTrack = pc.addTrack.bind(pc);
            pc.addTrack = function(track, ...streams) {{
                const sender = origAddTrack(track, ...streams);
                if (track.kind === 'audio') {{
                    window.__meetBot_audioSenders.push(sender);
                }}
                return sender;
            }};

            // Also capture senders added via addTransceiver
            const origAddTransceiver = pc.addTransceiver.bind(pc);
            pc.addTransceiver = function(trackOrKind, ...rest) {{
                const transceiver = origAddTransceiver(trackOrKind, ...rest);
                if (transceiver.sender && transceiver.sender.track && transceiver.sender.track.kind === 'audio') {{
                    window.__meetBot_audioSenders.push(transceiver.sender);
                }}
                return transceiver;
            }};

            return pc;
        }};

        // Copy static properties
        for (const prop of Object.getOwnPropertyNames(OriginalRTCPeerConnection)) {{
            try {{
                if (!(prop in window.RTCPeerConnection)) {{
                    window.RTCPeerConnection[prop] = OriginalRTCPeerConnection[prop];
                }}
            }} catch(e) {{}}
        }}
        window.RTCPeerConnection.prototype = OriginalRTCPeerConnection.prototype;

        console.log('[MeetBot] WebRTC interception initialized');
    }})();
    '''


def get_audio_playback_js(recording_id):
    """
    JavaScript to play pre-recorded audio into the meeting via track replacement.
    Fetches the audio file, decodes it, and replaces the microphone track.
    """
    return f'''
    (async function() {{
        try {{
            console.log('[MeetBot] Starting audio playback...');

            // Fetch the recorded audio
            const response = await fetch('/audio/playback/{recording_id}');
            const arrayBuffer = await response.arrayBuffer();

            // Decode audio
            const audioContext = new AudioContext();
            const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

            // Create a MediaStream from the audio buffer
            const destination = audioContext.createMediaStreamDestination();
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(destination);

            const audioTrack = destination.stream.getAudioTracks()[0];

            // Find audio senders and replace their tracks
            const senders = window.__meetBot_audioSenders || [];
            const replacedSenders = [];

            // Also check peer connections directly
            for (const pc of (window.__meetBot_peerConnections || [])) {{
                try {{
                    const pcSenders = pc.getSenders();
                    for (const sender of pcSenders) {{
                        if (sender.track && sender.track.kind === 'audio') {{
                            if (!senders.includes(sender)) {{
                                senders.push(sender);
                            }}
                        }}
                    }}
                }} catch(e) {{}}
            }}

            // First, unmute by clicking the mic button if muted
            try {{
                const micButtons = document.querySelectorAll('[aria-label*="microphone" i][role="button"], [data-is-muted][aria-label*="microphone" i]');
                for (const btn of micButtons) {{
                    const isMuted = btn.getAttribute('data-is-muted');
                    if (isMuted === 'true') {{
                        btn.click();
                        await new Promise(r => setTimeout(r, 500));
                    }}
                    break;
                }}
            }} catch(e) {{}}

            // Store original tracks for restoration
            for (const sender of senders) {{
                try {{
                    const originalTrack = sender.track;
                    replacedSenders.push({{ sender, originalTrack }});
                    await sender.replaceTrack(audioTrack);
                }} catch(e) {{
                    console.log('[MeetBot] Could not replace track:', e);
                }}
            }}

            // Start playback
            source.start(0);
            console.log('[MeetBot] Audio playing...');

            // Wait for playback to finish
            await new Promise(resolve => {{
                source.onended = resolve;
                // Safety timeout
                setTimeout(resolve, (audioBuffer.duration * 1000) + 1000);
            }});

            console.log('[MeetBot] Audio playback finished');

            // Restore original tracks and re-mute
            for (const {{ sender, originalTrack }} of replacedSenders) {{
                try {{
                    await sender.replaceTrack(originalTrack);
                }} catch(e) {{}}
            }}

            if (replacedSenders.length === 0) {{
                console.log('[MeetBot] No WebRTC senders found for track replacement; using direct audio fallback.');
                try {{
                    const audioElement = new Audio('/audio/playback/{recording_id}');
                    audioElement.volume = 1.0;
                    await audioElement.play();
                }} catch(e) {{
                    console.log('[MeetBot] Direct audio fallback failed:', e);
                }}
            }}

            // Re-mute microphone
            try {{
                const micButtons = document.querySelectorAll('[aria-label*="microphone" i][role="button"], [data-is-muted][aria-label*="microphone" i]');
                for (const btn of micButtons) {{
                    const isMuted = btn.getAttribute('data-is-muted');
                    if (isMuted === 'false') {{
                        btn.click();
                    }}
                    break;
                }}
            }} catch(e) {{}}

            audioContext.close();
        }} catch(err) {{
            console.error('[MeetBot] Playback error:', err);
        }}
    }})();
    '''


def register_socketio_events(socketio):
    """Register SocketIO event handlers for audio streaming."""

    @socketio.on('connect')
    def handle_connect():
        pass

    @socketio.on('audio_chunk')
    def handle_audio_chunk(data):
        # Handle incoming audio chunks from browser
        # This is a fallback path if Web Speech API is not available
        pass

    @socketio.on('join_session')
    def handle_join_session(data):
        from flask_socketio import join_room
        session_id = data.get('session_id')
        if session_id:
            join_room(f'session_{session_id}')
