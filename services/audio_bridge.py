"""
Audio bridge service - handles WebRTC audio interception and playback injection.
"""


def get_webrtc_intercept_js(session_id):
    """
    Injected before page scripts load to capture WebRTC senders and media streams.
    """
    return f'''
    (function() {{
        window.__meetBot_peerConnections = [];
        window.__meetBot_audioSenders = [];
        window.__meetBot_userMediaStreams = [];
        window.__meetBot_sessionId = {session_id};

        const OriginalRTCPeerConnection = window.RTCPeerConnection;
        if (OriginalRTCPeerConnection) {{
            window.RTCPeerConnection = function(...args) {{
                const pc = new OriginalRTCPeerConnection(...args);
                window.__meetBot_peerConnections.push(pc);

                const origAddTrack = pc.addTrack.bind(pc);
                pc.addTrack = function(track, ...streams) {{
                    const sender = origAddTrack(track, ...streams);
                    if (track && track.kind === 'audio') {{
                        window.__meetBot_audioSenders.push(sender);
                    }}
                    return sender;
                }};

                const origAddTransceiver = pc.addTransceiver.bind(pc);
                pc.addTransceiver = function(trackOrKind, ...rest) {{
                    const transceiver = origAddTransceiver(trackOrKind, ...rest);
                    try {{
                        if (
                            transceiver &&
                            transceiver.sender &&
                            transceiver.sender.track &&
                            transceiver.sender.track.kind === 'audio'
                        ) {{
                            window.__meetBot_audioSenders.push(transceiver.sender);
                        }}
                    }} catch (e) {{}}
                    return transceiver;
                }};

                return pc;
            }};

            for (const prop of Object.getOwnPropertyNames(OriginalRTCPeerConnection)) {{
                try {{
                    if (!(prop in window.RTCPeerConnection)) {{
                        window.RTCPeerConnection[prop] = OriginalRTCPeerConnection[prop];
                    }}
                }} catch (e) {{}}
            }}
            window.RTCPeerConnection.prototype = OriginalRTCPeerConnection.prototype;
        }}

        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
            const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async function(constraints) {{
                const stream = await originalGetUserMedia(constraints);
                try {{
                    window.__meetBot_userMediaStreams.push(stream);
                }} catch (e) {{}}
                return stream;
            }};
        }}
    }})();
    '''


def get_audio_playback_js(recording_id, provider='meet'):
    """
    Plays pre-recorded audio by replacing active outbound audio sender tracks.
    """
    mic_selector_meet = (
        '[aria-label*="microphone" i][role="button"], '
        '[data-is-muted][aria-label*="microphone" i]'
    )
    mic_selector_teams = (
        'button[data-tid="toggle-mute"], '
        'button[data-tid="prejoin-toggle-mute"], '
        'button[aria-label*="microphone" i], '
        'button[title*="microphone" i]'
    )
    mic_selector = mic_selector_teams if provider == 'teams' else mic_selector_meet

    return f'''
    (async function() {{
        try {{
            const response = await fetch('/audio/playback/{recording_id}');
            const arrayBuffer = await response.arrayBuffer();

            const audioContext = new AudioContext();
            const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

            const destination = audioContext.createMediaStreamDestination();
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(destination);

            const audioTrack = destination.stream.getAudioTracks()[0];
            const senders = window.__meetBot_audioSenders || [];
            const replacedSenders = [];

            for (const pc of (window.__meetBot_peerConnections || [])) {{
                try {{
                    const pcSenders = pc.getSenders() || [];
                    for (const sender of pcSenders) {{
                        if (sender && sender.track && sender.track.kind === 'audio' && !senders.includes(sender)) {{
                            senders.push(sender);
                        }}
                    }}
                }} catch (e) {{}}
            }}

            try {{
                const micButtons = document.querySelectorAll('{mic_selector}');
                for (const btn of micButtons) {{
                    const attrs = ((btn.getAttribute('aria-label') || '') + ' ' + (btn.getAttribute('title') || '')).toLowerCase();
                    const state = (btn.getAttribute('data-is-muted') || '').toLowerCase();
                    if (state === 'true' || attrs.includes('unmute') || attrs.includes('turn on microphone')) {{
                        btn.click();
                        await new Promise(r => setTimeout(r, 400));
                    }}
                    break;
                }}
            }} catch (e) {{}}

            for (const sender of senders) {{
                try {{
                    const originalTrack = sender.track;
                    replacedSenders.push({{ sender, originalTrack }});
                    await sender.replaceTrack(audioTrack);
                }} catch (e) {{}}
            }}

            source.start(0);
            await new Promise(resolve => {{
                source.onended = resolve;
                setTimeout(resolve, (audioBuffer.duration * 1000) + 1200);
            }});

            for (const item of replacedSenders) {{
                try {{
                    await item.sender.replaceTrack(item.originalTrack);
                }} catch (e) {{}}
            }}

            if (replacedSenders.length === 0) {{
                console.warn('[MeetBot] No WebRTC audio sender found. Audio NOT played to avoid local speaker bleed.');
            }}

            try {{
                const micButtons = document.querySelectorAll('{mic_selector}');
                for (const btn of micButtons) {{
                    const attrs = ((btn.getAttribute('aria-label') || '') + ' ' + (btn.getAttribute('title') || '')).toLowerCase();
                    const state = (btn.getAttribute('data-is-muted') || '').toLowerCase();
                    if (state === 'false' || attrs.includes('mute') || attrs.includes('turn off microphone')) {{
                        btn.click();
                    }}
                    break;
                }}
            }} catch (e) {{}}

            audioContext.close();
        }} catch (err) {{
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
        # Placeholder fallback path if browser chunk streaming is added later.
        pass

    @socketio.on('join_session')
    def handle_join_session(data):
        from flask_socketio import join_room

        session_id = data.get('session_id')
        if session_id:
            join_room(f'session_{session_id}')
