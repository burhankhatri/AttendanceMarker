import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime

import config
from database.db import db
from database.models import AudioRecording, MeetingSession, Settings
from services.audio_bridge import get_audio_playback_js, get_webrtc_intercept_js
from services.edge_driver import create_edge_driver
from services.meeting_providers import build_join_flow
from services.teams_selectors import TEAMS_SELECTOR_MANIFEST
from services.teams_utils import (
    contains_detection_term,
    hash_dom_snippet,
    is_auth_redirect_url,
    is_waiting_lobby_text,
    normalize_text,
)


class ReauthRequiredError(Exception):
    pass


class UnsupportedFlowError(Exception):
    pass


class MeetingBot:
    def __init__(self, session_id, meeting_link, provider, socketio):
        self.session_id = session_id
        self.meeting_link = meeting_link
        self.provider = provider or 'meet'
        self.socketio = socketio
        self.driver = None
        self.running = False
        self.runtime_profile_root = None
        self.join_flow = build_join_flow(self.provider)
        self.artifact_dir = os.path.join(
            config.BASE_DIR,
            'instance',
            'runtime_logs',
            f'session-{self.session_id}',
        )
        self._caption_seen = set()
        self._speech_started = False

    def _update_session(self, status=None, log_message=None):
        session = db.session.get(MeetingSession, self.session_id)
        if not session:
            return

        if status:
            session.status = status
        if log_message:
            session.add_log(log_message)
        db.session.commit()

        try:
            if self.socketio:
                self.socketio.emit(
                    'meeting_update',
                    {
                        'session_id': self.session_id,
                        'status': session.status,
                        'log': session.log,
                    },
                    namespace='/',
                )
        except Exception as exc:
            print(f'SocketIO emit error (non-fatal): {exc}')

    def _write_telemetry(self, event, payload):
        try:
            os.makedirs(self.artifact_dir, exist_ok=True)
            record = {
                'time': datetime.utcnow().isoformat() + 'Z',
                'event': event,
                'session_id': self.session_id,
                'provider': self.provider,
                'payload': payload,
            }
            with open(os.path.join(self.artifact_dir, 'telemetry.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=True) + '\n')
        except Exception:
            pass

    def _capture_dom_and_screenshot(self, stage):
        screenshot_path = None
        dom_hash = None
        dom_snippet = ''
        current_url = ''

        if not self.driver:
            return {
                'screenshot_path': screenshot_path,
                'dom_hash': dom_hash,
                'dom_snippet': dom_snippet,
                'url': current_url,
            }

        try:
            os.makedirs(self.artifact_dir, exist_ok=True)
            stamp = int(time.time() * 1000)
            screenshot_path = os.path.join(self.artifact_dir, f'{stamp}-{stage}.png')
            self.driver.save_screenshot(screenshot_path)
        except Exception:
            screenshot_path = None

        try:
            page_source = self.driver.page_source or ''
            dom_hash, dom_snippet = hash_dom_snippet(page_source)
        except Exception:
            dom_hash, dom_snippet = None, ''

        try:
            current_url = self.driver.current_url or ''
        except Exception:
            current_url = ''

        return {
            'screenshot_path': screenshot_path,
            'dom_hash': dom_hash,
            'dom_snippet': dom_snippet,
            'url': current_url,
        }

    def _record_stage_failure(self, stage, selector_group, stage_started_at, error_message):
        elapsed = max(0.0, time.time() - (stage_started_at or time.time()))
        artifact = self._capture_dom_and_screenshot(stage)
        payload = {
            'stage': stage,
            'selector_group': selector_group,
            'elapsed_sec': round(elapsed, 2),
            'error': error_message,
            'url': artifact['url'],
            'screenshot_path': artifact['screenshot_path'],
            'dom_hash': artifact['dom_hash'],
        }
        self._write_telemetry('stage_failure', payload)

    def run(self):
        self.running = True
        try:
            self._update_session('joining', f'Starting browser for {self.provider.title()}...')
            self._launch_chrome()

            join_state = self.join_flow.join(self)
            if join_state == 'waiting_lobby':
                self._update_session('waiting_lobby', 'In lobby. Waiting for admission while monitoring captions/audio...')
            else:
                self._update_session('listening', 'Joined meeting. Listening for attendance cues...')

            self._listen_loop()
        except ReauthRequiredError as exc:
            self._update_session('needs_reauth', str(exc))
        except UnsupportedFlowError as exc:
            self._update_session('unsupported_flow', str(exc))
        except Exception as exc:
            self._update_session('error', f'Error: {exc}')
            self._write_telemetry('bot_exception', {'error': str(exc)})
        finally:
            self._cleanup()

    def _wait_for_page_ready(self, timeout=12):
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            WebDriverWait(self.driver, timeout).until(
                lambda drv: drv.execute_script('return document.readyState;') == 'complete'
            )
        except Exception:
            pass

    @staticmethod
    def _is_windows_profile_lock_error(exc):
        text = str(exc or '').lower()
        return 'winerror 32' in text or 'being used by another process' in text

    def _launch_chrome(self):
        import subprocess

        from selenium import webdriver

        settings = Settings.get()
        browser_type = (settings.browser_type or 'edge').lower()

        if not settings.chrome_profile_path:
            raise UnsupportedFlowError('Browser profile is not configured. Open Browser Profile Setup first.')

        profile_root = settings.chrome_profile_path
        profile_name = settings.chrome_profile_name or 'Default'
        source_profile_dir = os.path.join(profile_root, profile_name)

        if not os.path.isdir(profile_root) or not os.path.isdir(source_profile_dir):
            raise UnsupportedFlowError('Configured browser profile was not found. Reconfigure Browser Profile Setup.')

        # Kill the browser so Selenium can use the real profile
        if browser_type == 'edge':
            self._update_session(log_message='Closing Edge to use your real profile...')
            subprocess.run(['taskkill', '/IM', 'msedge.exe', '/F'], capture_output=True, check=False)
        else:
            self._update_session(log_message='Closing Chrome to use your real profile...')
            subprocess.run(['taskkill', '/IM', 'chrome.exe', '/F'], capture_output=True, check=False)
        time.sleep(2)

        if browser_type == 'edge':
            options = webdriver.EdgeOptions()
            options.add_argument(f'--user-data-dir={profile_root}')
            options.add_argument(f'--profile-directory={profile_name}')
            options.add_argument('--use-fake-ui-for-media-stream')
            options.add_argument('--disable-notifications')
            options.add_argument('--disable-popup-blocking')
            options.add_argument('--no-first-run')
            options.add_argument('--no-default-browser-check')
            options.add_argument('--disable-features=msEdgeSidebarV2')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_experimental_option('detach', True)
            options.page_load_strategy = 'eager'
        else:
            options = webdriver.ChromeOptions()
            options.add_argument(f'--user-data-dir={profile_root}')
            options.add_argument(f'--profile-directory={profile_name}')
            options.add_argument('--use-fake-ui-for-media-stream')
            options.add_argument('--disable-notifications')
            options.add_argument('--disable-popup-blocking')
            options.add_argument('--no-first-run')
            options.add_argument('--no-default-browser-check')
            options.add_argument('--disable-features=ProfilePickerOnStartup')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_experimental_option('detach', True)
            options.add_argument('--remote-allow-origins=*')
            options.page_load_strategy = 'eager'

        result = {'driver': None, 'error': None}

        def _start_driver():
            try:
                if browser_type == 'edge':
                    result['driver'] = create_edge_driver(options)
                else:
                    result['driver'] = webdriver.Chrome(options=options)
            except Exception as exc:
                result['error'] = exc

        self._update_session(log_message=f'Opening {browser_type.title()} with your real profile...')
        thread = threading.Thread(target=_start_driver, daemon=True)
        thread.start()
        thread.join(timeout=35)

        if thread.is_alive():
            raise UnsupportedFlowError('Browser launch timed out.')
        if result['error']:
            raise UnsupportedFlowError(f'Browser launch failed: {result["error"]}')
        if result['driver'] is None:
            raise UnsupportedFlowError('Browser did not start correctly.')

        self.driver = result['driver']

        stealth_js = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        intercept_js = get_webrtc_intercept_js(self.session_id)
        self.driver.execute_cdp_cmd(
            'Page.addScriptToEvaluateOnNewDocument',
            {'source': stealth_js + '\n' + intercept_js},
        )
        self._update_session(log_message='Browser session ready (stealth mode, real profile)')
        
        try:
            self._update_session(log_message='Re-opening app dashboard in background tab...')
            self.driver.execute_script("window.open('http://127.0.0.1:5001/dashboard', '_blank');")
            self.driver.switch_to.window(self.driver.window_handles[0])
        except Exception:
            pass


    def _get_body_text(self):
        from selenium.webdriver.common.by import By

        try:
            return (self.driver.find_element(By.TAG_NAME, 'body').text or '').strip()
        except Exception:
            return ''

    def _try_click_candidates(self, stage, selector_group, css_selectors=None, xpath_selectors=None, timeout=4):
        from selenium.webdriver.common.by import By

        started = time.time()
        css_selectors = css_selectors or []
        xpath_selectors = xpath_selectors or []

        while time.time() - started < timeout:
            if not getattr(self, 'running', True):
                break

            for css in css_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, css)
                    for button in elements:
                        if button.is_displayed() and button.is_enabled():
                            button.click()
                            return True, f'css:{css}'
                except Exception:
                    pass

            for xpath in xpath_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, xpath)
                    for button in elements:
                        if button.is_displayed() and button.is_enabled():
                            button.click()
                            return True, f'xpath:{xpath}'
                except Exception:
                    pass
            
            time.sleep(1)

        if getattr(self, 'running', True):
            self._record_stage_failure(stage, selector_group, started, 'No clickable selector matched')
        return False, None

    def _toggle_control_if_unmuted(self, selectors, label):
        from selenium.webdriver.common.by import By
        import time

        for attempt in range(6):
            if not getattr(self, 'running', True):
                break
                
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for button in elements:
                        if not button.is_displayed():
                            continue
                        attrs = ' '.join(
                            [
                                button.get_attribute('aria-label') or '',
                                button.get_attribute('title') or '',
                                button.get_attribute('data-state') or '',
                                button.get_attribute('data-is-muted') or '',
                            ]
                        ).lower()
                        should_click = 'on' in attrs or 'unmute' in attrs or 'turn off' in attrs
                        if button.get_attribute('data-is-muted') == 'false':
                            should_click = True
                        if should_click:
                            button.click()
                            self._update_session(log_message=f'{label} toggled off')
                        return True
                except Exception:
                    pass
            time.sleep(1)
        return False

    def _join_meet_flow(self):
        join_stage_started = time.time()
        self._update_session(log_message='Navigating to Google Meet...')

        nav_ok = False
        for attempt in range(1, 4):
            try:
                self.driver.get(self.meeting_link)
                self._wait_for_page_ready(timeout=12)
                if 'meet.google.com' in (self.driver.current_url or '').lower():
                    nav_ok = True
                    break
            except Exception as exc:
                self._update_session(log_message=f'Meet navigation attempt {attempt} failed: {exc}')
            time.sleep(2)

        if not nav_ok:
            self._record_stage_failure('join', 'meet_navigation', join_stage_started, 'Could not reach meet.google.com')
            raise UnsupportedFlowError('Could not open Google Meet link.')

        self._toggle_control_if_unmuted(
            [
                '[aria-label*="camera" i][role*="button" i]',
                '[title*="camera" i][role*="button" i]',
                'button[aria-label*="camera" i]',
                'button[title*="camera" i]'
            ],
            'Camera',
        )
        self._toggle_control_if_unmuted(
            [
                '[aria-label*="microphone" i][role*="button" i]',
                '[title*="microphone" i][role*="button" i]',
                '[aria-label*="mic" i][role*="button" i]',
                'button[aria-label*="microphone" i]'
            ],
            'Microphone',
        )

        join_ok, meta = self._try_click_candidates(
            stage='join',
            selector_group='meet_join_button',
            xpath_selectors=[
                '//button[contains(., "Join now")]',
                '//button[contains(., "Ask to join")]',
                '//button[contains(., "Request to join")]',
                '//span[contains(text(), "Join now")]/ancestor::button',
                '//span[contains(text(), "Ask to join")]/ancestor::button',
            ],
            timeout=25,
        )
        if not join_ok:
            raise UnsupportedFlowError('Meet join button not found. UI may have changed.')

        self._write_telemetry('join_click', {'provider': 'meet', 'selector': meta})
        self._update_session(log_message='Meet join button clicked')
        time.sleep(6)

        body_text = self._get_body_text()
        if 'waiting for someone to let you in' in body_text.lower() or 'you are in the lobby' in body_text.lower():
            return 'waiting_lobby'
        return 'in_meeting'

    def _check_reauth_or_raise(self):
        current_url = self.driver.current_url or ''
        body_text = self._get_body_text().lower()
        if is_auth_redirect_url(current_url):
            raise ReauthRequiredError(
                'Teams redirected to Microsoft sign-in. Re-open Browser Profile Setup and login again, then retry.'
            )
        if 'enter password' in body_text and 'sign in' in body_text:
            raise ReauthRequiredError(
                'Teams session is not active in selected browser profile. Re-login in Browser Profile Setup and retry.'
            )

    def _click_manifest_group(self, stage, group_name, timeout=4, optional=False):
        manifest = TEAMS_SELECTOR_MANIFEST.get(stage, {})
        group = manifest.get(group_name, manifest)
        css = group.get('css', [])
        xpath = group.get('xpath', [])

        ok, selector_used = self._try_click_candidates(
            stage=stage,
            selector_group=group_name,
            css_selectors=css,
            xpath_selectors=xpath,
            timeout=timeout,
        )
        if ok:
            return True, selector_used
        if optional:
            return False, None
        raise UnsupportedFlowError(f'Teams stage "{stage}" failed for selector group "{group_name}"')

    def _join_teams_flow(self):
        self._update_session(log_message='Navigating to Microsoft Teams web meeting...')
        stage_started = time.time()

        nav_ok = False
        for attempt in range(1, 4):
            try:
                self.driver.get(self.meeting_link)
                self._wait_for_page_ready(timeout=15)
                self._check_reauth_or_raise()
                current_url = (self.driver.current_url or '').lower()
                if 'teams.microsoft.com' in current_url or 'teams.live.com' in current_url:
                    nav_ok = True
                    break
            except ReauthRequiredError:
                raise
            except Exception as exc:
                self._update_session(log_message=f'Teams navigation attempt {attempt} failed: {exc}')
            time.sleep(2)

        if not nav_ok:
            self._record_stage_failure('app_prompt', 'teams_navigation', stage_started, 'Could not reach teams web host')
            raise UnsupportedFlowError('Could not open Teams web meeting link.')

        self._update_session(log_message='Handling app redirect / browser continue prompt...')
        try:
            clicked, selector_used = self._click_manifest_group(
                stage='app_prompt',
                group_name='app_prompt',
                timeout=4,
                optional=True,
            )
            if clicked:
                self._update_session(log_message='Selected "Continue on this browser"')
                self._write_telemetry('app_prompt_click', {'selector': selector_used})
                time.sleep(3)
                self._wait_for_page_ready(timeout=10)
        except Exception:
            pass

        self._check_reauth_or_raise()

        wait_started = time.time()
        wait_deadline = time.time() + 45
        join_visible = False
        while time.time() < wait_deadline:
            self._check_reauth_or_raise()
            body_text = self._get_body_text()
            if 'join now' in body_text.lower() or 'join' in body_text.lower():
                join_visible = True
                break
            elapsed = int(time.time() - wait_started)
            if elapsed and elapsed % 10 == 0:
                self._update_session(log_message=f'Waiting for Teams pre-join controls... {elapsed}s')
            time.sleep(1)

        if not join_visible:
            self._record_stage_failure('prejoin', 'join_visibility', wait_started, 'Join controls did not appear')
            raise UnsupportedFlowError('Teams join controls did not appear in time.')

        prejoin = TEAMS_SELECTOR_MANIFEST.get('prejoin', {})
        self._toggle_control_if_unmuted(prejoin.get('camera', {}).get('css', []), 'Camera')
        self._toggle_control_if_unmuted(prejoin.get('mic', {}).get('css', []), 'Microphone')

        self._update_session(log_message='Attempting Teams Join now button...')
        join_started = time.time()
        ok, selector_used = self._click_manifest_group(
            stage='join',
            group_name='join',
            timeout=25,
            optional=False,
        )
        if not ok:
            self._record_stage_failure('join', 'join', join_started, 'Join click failed')
            raise UnsupportedFlowError('Could not click Teams Join now button.')

        self._write_telemetry('join_click', {'provider': 'teams', 'selector': selector_used})
        self._update_session(log_message='Teams join interaction completed')
        time.sleep(8)
        self._check_reauth_or_raise()

        body_text = self._get_body_text()
        if is_waiting_lobby_text(body_text):
            return 'waiting_lobby'
        return 'in_meeting'

    def _collect_teams_caption_lines(self):
        from selenium.webdriver.common.by import By

        captions = []
        for selector in TEAMS_SELECTOR_MANIFEST.get('captions', {}).get('css', []):
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    text = (element.text or '').strip()
                except Exception:
                    text = ''
                if not text:
                    continue
                normalized = normalize_text(text)
                if not normalized or normalized in self._caption_seen:
                    continue
                self._caption_seen.add(normalized)
                captions.append(text)
        return captions

    def _start_speech_recognition(self, detection_terms):
        terms_js = json.dumps(detection_terms)
        recognition_js = f'''
        (function() {{
            if (window.__meetBot_speech_started) {{ return; }}
            window.__meetBot_speech_started = true;
            window.__meetBot_detected = null;
            window.__meetBot_transcriptions = [];
            const detectionTerms = {terms_js};
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {{
                window.__meetBot_speech_error = 'SpeechRecognition unavailable';
                return;
            }}
            function normalizeText(value) {{
                return String(value || '')
                    .toLowerCase()
                    .replace(/[^a-z0-9\\s]/g, ' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
            }}
            function escapeRegExp(value) {{
                return String(value).replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
            }}
            function startRecognition() {{
                const recognition = new SpeechRecognition();
                recognition.continuous = true;
                recognition.interimResults = false;
                recognition.lang = 'en-IN';
                recognition.maxAlternatives = 3;
                recognition.onresult = function(event) {{
                    for (let i = event.resultIndex; i < event.results.length; i++) {{
                        if (!event.results[i].isFinal) {{ continue; }}
                        const transcript = normalizeText(event.results[i][0].transcript);
                        window.__meetBot_transcriptions.push(transcript);
                        for (const term of detectionTerms) {{
                            const normalizedTerm = normalizeText(term);
                            if (!normalizedTerm) {{ continue; }}
                            const re = new RegExp('(^|\\\\W)' + escapeRegExp(normalizedTerm) + '(\\\\W|$)');
                            if (re.test(transcript)) {{
                                window.__meetBot_detected = {{ text: transcript, term: term }};
                                break;
                            }}
                        }}
                    }}
                }};
                recognition.onerror = function(event) {{
                    if (event.error !== 'aborted') {{
                        setTimeout(startRecognition, 1000);
                    }}
                }};
                recognition.onend = function() {{ setTimeout(startRecognition, 500); }};
                recognition.start();
            }}
            startRecognition();
        }})();
        '''
        self.driver.execute_script(recognition_js)
        self._speech_started = True

    def _poll_speech_detection(self):
        result = self.driver.execute_script('return window.__meetBot_detected || null;')
        if result:
            self.driver.execute_script('window.__meetBot_detected = null;')
        return result

    def _poll_speech_transcriptions(self):
        return self.driver.execute_script(
            'var t = window.__meetBot_transcriptions || []; window.__meetBot_transcriptions = []; return t;'
        )

    def _listen_loop(self):
        settings = Settings.get()
        detection_terms = settings.get_all_detection_terms()
        recording = AudioRecording.query.first()

        if not detection_terms:
            self._update_session(log_message='No detection terms configured. Listening loop skipped.')
            return

        self._update_session(log_message=f'Listening for: {", ".join(detection_terms)}')

        if self.provider == 'meet':
            self._start_speech_recognition(detection_terms)
            self._update_session(log_message='Speech recognition active')
        else:
            self._update_session(log_message='Teams caption listener active (speech fallback will start if captions are absent).')

        caption_miss_cycles = 0
        responded_count = 0

        while self.running:
            time.sleep(2)
            try:
                caption_detected = None
                caption_lines = []
                if self.provider == 'teams':
                    caption_lines = self.join_flow.collect_caption_lines(self)
                    if caption_lines:
                        caption_miss_cycles = 0
                        for line in caption_lines:
                            self._update_session(log_message=f'Caption: "{line}"')
                            matched, term, normalized = contains_detection_term(line, detection_terms)
                            if matched:
                                caption_detected = {
                                    'text': line,
                                    'term': term,
                                    'normalized': normalized,
                                }
                                break
                    else:
                        caption_miss_cycles += 1

                    if not self._speech_started and caption_miss_cycles >= 10:
                        self._start_speech_recognition(detection_terms)
                        self._update_session(log_message='No captions detected. Speech recognition fallback active.')

                speech_detected = None
                if self._speech_started:
                    speech_detected = self._poll_speech_detection()
                    transcriptions = self._poll_speech_transcriptions()
                    for line in transcriptions or []:
                        self._update_session(log_message=f'Heard: "{line}"')

                detected = caption_detected or speech_detected
                if detected:
                    detected_text = detected.get('text', '')
                    self._update_session('name_detected', f'NAME DETECTED! Heard: "{detected_text}"')

                    if recording:
                        self._play_response(recording.id)
                        responded_count += 1
                        self._update_session('responded', f'Played "present" response (#{responded_count})')

                    time.sleep(2)
                    if self.provider != 'teams':
                        self._update_session('listening', 'Back to listening...')
                    else:
                        current_session = db.session.get(MeetingSession, self.session_id)
                        if current_session and current_session.status != 'waiting_lobby':
                            self._update_session('listening', 'Back to listening...')

            except Exception as exc:
                err = str(exc).lower()
                if 'no such window' in err or 'not reachable' in err:
                    self._update_session('ended', 'Browser window closed')
                    self.running = False
                    break
                self._write_telemetry('listen_loop_exception', {'error': str(exc)})

    def _play_response(self, recording_id):
        if not self.driver:
            return

        try:
            probe = self.driver.execute_script(
                '''
                const senders = window.__meetBot_audioSenders || [];
                const peerConnections = window.__meetBot_peerConnections || [];
                let discovered = senders.length;
                for (const pc of peerConnections) {
                    try {
                        const pcSenders = pc.getSenders() || [];
                        for (const sender of pcSenders) {
                            if (sender && sender.track && sender.track.kind === 'audio') {
                                discovered += 1;
                            }
                        }
                    } catch (e) {}
                }
                return { discoveredSenders: discovered };
                '''
            )
        except Exception:
            probe = {'discoveredSenders': 0}

        if (probe or {}).get('discoveredSenders', 0) <= 0:
            self._update_session(log_message='audio_swap_unavailable: no compatible WebRTC audio sender detected yet.')

        playback_js = get_audio_playback_js(recording_id, provider=self.provider)
        try:
            self.driver.execute_script(playback_js)
            time.sleep(4)
        except Exception as exc:
            self._update_session(log_message=f'Audio playback error: {exc}')

    def stop(self):
        self.running = False
        self.user_stopped = True

    def _cleanup(self):
        self.running = False

        if self.driver:
            if getattr(self, 'user_stopped', False):
                try:
                    # Close the active meeting tab but leave other tabs (like the dashboard) open
                    self.driver.close()
                except Exception:
                    pass
            try:
                # Stop the webdriver service process to prevent zombies
                self.driver.service.stop()
            except Exception:
                pass

        if self.runtime_profile_root:
            try:
                shutil.rmtree(self.runtime_profile_root, ignore_errors=True)
            except Exception:
                pass
            finally:
                self.runtime_profile_root = None

        session = db.session.get(MeetingSession, self.session_id)
        if session and session.status not in ('ended', 'error', 'needs_reauth', 'unsupported_flow'):
            session.status = 'ended'
            session.ended_at = datetime.utcnow()
            session.add_log('Session ended')
            db.session.commit()
