import os
import re
import threading
import time
import json
import shutil
import tempfile
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from datetime import datetime
from database.db import db
from database.models import Settings, AudioRecording, MeetingSession
from services.audio_bridge import get_webrtc_intercept_js, get_audio_playback_js
from services.speech_listener import SpeechListener


class MeetingBot:
    def __init__(self, session_id, meet_link, socketio):
        self.session_id = session_id
        self.meet_link = meet_link
        self.socketio = socketio
        self.driver = None
        self.running = False
        self.runtime_profile_root = None

    def _update_session(self, status=None, log_message=None):
        session = db.session.get(MeetingSession, self.session_id)
        if session:
            if status:
                session.status = status
            if log_message:
                session.add_log(log_message)
            db.session.commit()
            try:
                if self.socketio:
                    self.socketio.emit('meeting_update', {
                        'session_id': self.session_id,
                        'status': session.status,
                        'log': session.log
                    }, namespace='/')
            except Exception as e:
                print(f'SocketIO emit error (non-fatal): {e}')

    def _debug_log(self, run_id, hypothesis_id, location, message, data=None):
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

    def run(self):
        self.running = True
        try:
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H1',
                location='services/meeting_bot.py:run',
                message='MeetingBot.run start',
                data={'session_id': self.session_id, 'meet_link': self.meet_link}
            )
            # endregion
            self._update_session('joining', 'Starting Chrome browser...')
            self._launch_chrome()

            self._update_session(log_message='Navigating to Google Meet...')
            self._join_meeting()

            self._update_session('listening', 'Joined meeting. Listening for your name...')
            self._listen_loop()
        except Exception as e:
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H5',
                location='services/meeting_bot.py:run-except',
                message='MeetingBot.run exception',
                data={'error': str(e)}
            )
            # endregion
            self._update_session('error', f'Error: {str(e)}')
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

    def _navigate_to_meeting_url(self, url, attempts=3):
        from selenium.common.exceptions import WebDriverException
        for attempt in range(1, attempts + 1):
            try:
                self._update_session(log_message=f'Opening meeting URL (attempt {attempt}/{attempts})...')
                self.driver.get(url)
                self._wait_for_page_ready(timeout=12)
                current_url = (self.driver.current_url or '')
                if 'meet.google.com' in current_url:
                    return True

                try:
                    self.driver.execute_script('window.location.href = arguments[0];', url)
                    self._wait_for_page_ready(timeout=12)
                    current_url = (self.driver.current_url or '')
                    if 'meet.google.com' in current_url:
                        return True
                except WebDriverException:
                    pass
            except Exception as e:
                self._update_session(log_message=f'Navigation attempt {attempt} failed: {str(e)}')
            time.sleep(2)
        return False

    def _extract_meet_code(self):
        match = re.search(
            r'meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3}(?:-[a-z]{3})?)',
            self.meet_link or '',
            re.IGNORECASE
        )
        return match.group(1).lower() if match else ''

    def _force_navigate_with_code_page(self):
        meet_code = self._extract_meet_code()
        if not meet_code:
            return False

        for url in [
            f'https://meet.google.com/{meet_code}',
            'https://meet.google.com/new',
            'https://meet.google.com'
        ]:
            try:
                self._update_session(log_message=f'Forcing Meet navigation via {url}')
                self.driver.execute_script('window.location.href = arguments[0];', url)
                self._wait_for_page_ready(timeout=12)
                if 'meet.google.com' in (self.driver.current_url or ''):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _fill_meeting_code(self):
        from selenium.common.exceptions import ElementClickInterceptedException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        meet_code = self._extract_meet_code()
        if not meet_code:
            return False

        selectors = [
            'input[aria-label*="meeting code" i]',
            'input[aria-label*="Enter code" i]',
            'input[placeholder*="meeting code" i]',
            'input[placeholder*="Enter code" i]',
            'input[type="text"]',
            'input[name="code"]',
            'input[name="meetingCode"]'
        ]

        for sel in selectors:
            try:
                field = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                if not field.is_displayed():
                    continue
                try:
                    field.click()
                except Exception:
                    pass
                self.driver.execute_script('arguments[0].value = "";', field)
                field.send_keys(meet_code)
                try:
                    field.send_keys(Keys.ENTER)
                except ElementClickInterceptedException:
                    self.driver.execute_script(
                        'var f=arguments[0]; f.dispatchEvent(new Event("input", {bubbles: true})); '
                        'f.dispatchEvent(new Event("change", {bubbles: true}));',
                        field
                    )
                self._update_session(log_message='Typed meeting code and submitted')
                time.sleep(1.5)
                return True
            except Exception:
                continue

        try:
            self.driver.execute_script('''
                var code = arguments[0];
                var inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                for (const input of inputs) {
                    var label = (input.getAttribute('aria-label') || '').toLowerCase();
                    var placeholder = (input.getAttribute('placeholder') || '').toLowerCase();
                    var name = (input.getAttribute('name') || '').toLowerCase();
                    if (
                        label.includes('meeting') || label.includes('code') ||
                        placeholder.includes('meeting') || placeholder.includes('code') ||
                        name.includes('code') || name.includes('meeting')
                    ) {
                        input.focus();
                        input.value = code;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter' }));
                        input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
                        return true;
                    }
                }
                return false;
            ''', meet_code)
            self._update_session(log_message='Used JS fallback for meeting code entry')
            return True
        except Exception:
            return False

    def _close_prejoin_modal(self):
        from selenium.webdriver.common.by import By

        button_texts = [
            'got it',
            'continue',
            'dismiss',
            'not now',
            'no, thanks',
            'close',
            'skip',
            'ok',
            'allow',
            'done',
            'next'
        ]

        try:
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H11',
                location='services/meeting_bot.py:_close_prejoin_modal',
                message='Modal close scan start',
                data={}
            )
            # endregion
            candidates = self.driver.find_elements(By.XPATH, '//button | //*[@role="button"]')
        except Exception:
            candidates = []

        for btn in candidates:
            try:
                text_value = ((btn.text or '') + ' ' + (btn.get_attribute('aria-label') or '')).strip().lower()
                if not text_value:
                    continue
                if any(token in text_value for token in button_texts):
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        # region agent log
                        self._debug_log(
                            run_id='initial',
                            hypothesis_id='H11',
                            location='services/meeting_bot.py:_close_prejoin_modal',
                            message='Modal button clicked',
                            data={'text_value': text_value[:80]}
                        )
                        # endregion
                        time.sleep(0.3)
                        return True
            except Exception:
                continue
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H11',
            location='services/meeting_bot.py:_close_prejoin_modal',
            message='Modal close scan complete with no click',
            data={'candidate_count': len(candidates)}
        )
        # endregion
        return False

    def _is_join_ui_ready(self):
        try:
            return self.driver.execute_script('''
                const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
                const hasJoinNow = /join now/i.test(bodyText);
                const hasAskToJoin = /ask to join/i.test(bodyText);
                const hasCannotJoin = /you can't join this video call/i.test(bodyText);
                const candidateButtons = Array.from(document.querySelectorAll('button,[role="button"]'))
                    .filter((el) => el.offsetParent !== null)
                    .map((el) => ((el.innerText || el.textContent || '').trim().toLowerCase()));
                const hasJoinButton = candidateButtons.some((t) => t === 'join now' || t === 'ask to join' || t === 'request to join');
                return {
                    has_join_now: hasJoinNow,
                    has_ask_to_join: hasAskToJoin,
                    has_cannot_join: hasCannotJoin,
                    has_join_button: hasJoinButton
                };
            ''')
        except Exception:
            return {'snapshot_error': True}

    def _is_join_ui_ready_dom(self):
        from selenium.webdriver.common.by import By

        flags = {
            'has_join_now': False,
            'has_ask_to_join': False,
            'has_cannot_join': False,
            'has_join_button': False
        }

        body_text = ''
        try:
            body_text = (self.driver.find_element(By.TAG_NAME, 'body').text or '').lower()
        except Exception:
            body_text = ''

        if body_text:
            flags['has_join_now'] = 'join now' in body_text
            flags['has_ask_to_join'] = 'ask to join' in body_text or 'request to join' in body_text
            if "you can't join this video call" in body_text:
                flags['state_hint'] = "blocked_by_meet"
            elif "ask to join" in body_text or "request to join" in body_text:
                flags['state_hint'] = "ask_to_join_visible"
            elif "join now" in body_text or re.search(r'\bjoin\b', body_text):
                flags['state_hint'] = "join_visible"
            elif "meeting code" in body_text and "invalid" in body_text:
                flags['state_hint'] = "invalid_meeting_code"
            elif "meeting has ended" in body_text or "no one else is here" in body_text:
                flags['state_hint'] = "meeting_not_active"
            elif "you're the first one here" in body_text:
                flags['state_hint'] = "waiting_for_organizer"

        try:
            for el in self.driver.find_elements(By.CSS_SELECTOR, 'button,[role="button"]'):
                try:
                    if not el.is_displayed():
                        continue
                    text_value = ((el.text or '') + ' ' + (el.get_attribute('aria-label') or '')).strip().lower()
                    if not text_value:
                        continue
                    if 'join now' in text_value:
                        flags['has_join_now'] = True
                        flags['has_join_button'] = True
                    elif 'ask to join' in text_value or 'request to join' in text_value:
                        flags['has_ask_to_join'] = True
                        flags['has_join_button'] = True
                    elif 'join' in text_value and "can't join" not in text_value and 'rejoin' not in text_value:
                        flags['has_join_button'] = True
                    elif "you can't join this video call" in text_value:
                        flags['has_cannot_join'] = True
                except Exception:
                    continue
        except Exception:
            pass

        # Mark cannot-join only when the message is visible in actionable UI, not just present in raw page text.
        try:
            block_nodes = self.driver.find_elements(
                By.XPATH,
                '//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "you can\'t join this video call")]'
            )
            flags['has_cannot_join'] = any(node.is_displayed() for node in block_nodes)
        except Exception:
            pass

        return flags

    def _meet_url_with_authuser(self, authuser):
        parsed = urlparse(self.meet_link)
        pairs = [(k, v) for (k, v) in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != 'authuser']
        pairs.append(('authuser', str(authuser)))
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(pairs), parsed.fragment))

    def _attempt_authuser_recovery(self):
        any_unblocked_candidate = False
        last_flags = {
            'has_join_now': False,
            'has_ask_to_join': False,
            'has_cannot_join': True,
            'has_join_button': False
        }
        for authuser in (0, 1, 2, 3):
            candidate_url = self._meet_url_with_authuser(authuser)
            try:
                self._update_session(log_message=f'Trying alternate signed-in account (authuser={authuser})...')
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H12',
                    location='services/meeting_bot.py:_attempt_authuser_recovery',
                    message='authuser candidate start',
                    data={'authuser': authuser, 'url': candidate_url}
                )
                # endregion
                self.driver.execute_script('window.location.href = arguments[0];', candidate_url)
                candidate_deadline = time.time() + 10
                flags = {
                    'has_join_now': False,
                    'has_ask_to_join': False,
                    'has_cannot_join': False,
                    'has_join_button': False
                }
                blocked_streak = 0
                while time.time() < candidate_deadline:
                    flags = self._is_join_ui_ready_dom()
                    if flags.get('has_join_now') or flags.get('has_ask_to_join') or flags.get('has_join_button'):
                        break
                    if flags.get('has_cannot_join'):
                        blocked_streak += 1
                        if blocked_streak >= 2:
                            break
                    else:
                        blocked_streak = 0
                    time.sleep(1)
                last_flags = flags
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H12',
                    location='services/meeting_bot.py:_attempt_authuser_recovery',
                    message='authuser candidate evaluated',
                    data={'authuser': authuser, 'flags': flags, 'url': candidate_url}
                )
                # endregion
                self._update_session(
                    log_message=(
                        f'Authuser {authuser} flags: join={flags.get("has_join_button")}, '
                        f'blocked={flags.get("has_cannot_join")}, hint={flags.get("state_hint", "n/a")}'
                    )
                )
                if flags.get('has_join_now') or flags.get('has_ask_to_join') or flags.get('has_join_button'):
                    self._update_session(log_message=f'Found join-capable account with authuser={authuser}')
                    return flags
                if not flags.get('has_cannot_join'):
                    any_unblocked_candidate = True
            except Exception as e:
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H12',
                    location='services/meeting_bot.py:_attempt_authuser_recovery',
                    message='authuser candidate failed',
                    data={'authuser': authuser, 'error': str(e)}
                )
                # endregion
                self._update_session(log_message=f'Account attempt authuser={authuser} failed')
                continue
        if any_unblocked_candidate:
            return {
                'has_join_now': False,
                'has_ask_to_join': False,
                'has_cannot_join': False,
                'has_join_button': False
            }
        return {
            'has_join_now': False,
            'has_ask_to_join': False,
            'has_cannot_join': bool(last_flags.get('has_cannot_join', True)),
            'has_join_button': False
        }

    def _launch_chrome(self):
        from selenium import webdriver

        settings = Settings.get()
        if not settings.chrome_profile_path:
            raise Exception('Google profile not configured. Go to Google Setup first.')

        profile_dir = settings.chrome_profile_path
        profile_name = settings.chrome_profile_name or 'Default'
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H1',
            location='services/meeting_bot.py:_launch_chrome',
            message='Launch config resolved',
            data={'profile_dir': profile_dir, 'profile_name': profile_name}
        )
        # endregion
        if not os.path.exists(profile_dir) or not os.path.isdir(os.path.join(profile_dir, profile_name)):
            raise Exception('Chrome profile not found. Please redo Google Setup.')

        self._update_session(log_message='Launching Chrome...')
        self._update_session(log_message='Preparing isolated runtime Chrome profile...')

        source_profile_dir = os.path.join(profile_dir, profile_name)
        temp_profile_root = tempfile.mkdtemp(prefix='meetbot-profile-')
        self.runtime_profile_root = temp_profile_root
        temp_profile_dir = os.path.join(temp_profile_root, profile_name)

        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H9',
            location='services/meeting_bot.py:_launch_chrome',
            message='Created temp runtime profile root',
            data={'temp_profile_root': temp_profile_root}
        )
        # endregion

        try:
            local_state_path = os.path.join(profile_dir, 'Local State')
            if os.path.isfile(local_state_path):
                shutil.copy2(local_state_path, os.path.join(temp_profile_root, 'Local State'))
            shutil.copytree(source_profile_dir, temp_profile_dir, dirs_exist_ok=True)
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H9',
                location='services/meeting_bot.py:_launch_chrome',
                message='Copied source profile into temp runtime profile',
                data={'source_profile_dir': source_profile_dir, 'temp_profile_dir': temp_profile_dir}
            )
            # endregion
        except Exception as e:
            raise Exception(f'Failed to prepare runtime Chrome profile copy: {e}')

        options = webdriver.ChromeOptions()
        options.add_argument(f'--user-data-dir={temp_profile_root}')
        options.add_argument(f'--profile-directory={profile_name}')
        options.page_load_strategy = 'eager'
        options.add_argument('--use-fake-ui-for-media-stream')
        options.add_argument('--disable-notifications')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-features=ProfilePickerOnStartup')
        options.add_argument('--remote-allow-origins=*')

        self._update_session(log_message='Opening browser automation session...')
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H9',
            location='services/meeting_bot.py:_launch_chrome',
            message='Starting Selenium Chrome launch with temp profile',
            data={'temp_profile_root': temp_profile_root, 'profile_name': profile_name}
        )
        # endregion

        launch_result = {'driver': None, 'error': None}
        def _launch_selenium():
            try:
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H9',
                    location='services/meeting_bot.py:_launch_chrome._launch_selenium',
                    message='webdriver.Chrome invocation start',
                    data={}
                )
                # endregion
                launch_result['driver'] = webdriver.Chrome(options=options)
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H9',
                    location='services/meeting_bot.py:_launch_chrome._launch_selenium',
                    message='webdriver.Chrome invocation success',
                    data={'driver_created': launch_result['driver'] is not None}
                )
                # endregion
            except Exception as e:
                launch_result['error'] = e
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H9',
                    location='services/meeting_bot.py:_launch_chrome._launch_selenium',
                    message='webdriver.Chrome invocation exception',
                    data={'error': str(e)}
                )
                # endregion

        launch_thread = threading.Thread(target=_launch_selenium, daemon=True)
        launch_thread.start()
        elapsed = 0
        launch_timeout_sec = 30
        while launch_thread.is_alive() and elapsed < launch_timeout_sec:
            launch_thread.join(timeout=5)
            elapsed += 5
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H9',
                location='services/meeting_bot.py:_launch_chrome',
                message='Waiting for Selenium launch thread',
                data={'elapsed_sec': elapsed, 'thread_alive': launch_thread.is_alive()}
            )
            # endregion
        thread_alive = launch_thread.is_alive()
        has_error = launch_result['error'] is not None
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H9',
            location='services/meeting_bot.py:_launch_chrome',
            message='Launch thread join complete',
            data={'thread_alive': thread_alive, 'has_error': has_error}
        )
        # endregion

        if thread_alive:
            raise Exception('Chrome launch timed out while opening temporary runtime profile.')
        if has_error:
            raise Exception(f'Chrome launch failed with temporary runtime profile: {launch_result["error"]}')

        self.driver = launch_result['driver']
        if self.driver is None:
            raise Exception('Chrome did not start correctly with temporary runtime profile.')

        self._update_session(log_message='Browser session ready')

        intercept_js = get_webrtc_intercept_js(self.session_id)
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': intercept_js
        })

    def _join_meeting(self):
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        nav_ok = self._navigate_to_meeting_url(self.meet_link)
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H3',
            location='services/meeting_bot.py:_join_meeting',
            message='Primary navigation result',
            data={
                'nav_ok': nav_ok,
                'current_url': (self.driver.current_url if self.driver else '')
            }
        )
        # endregion
        if not nav_ok:
            if not self._force_navigate_with_code_page():
                raise Exception('Could not open meeting URL after retries.')

        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H11',
            location='services/meeting_bot.py:_join_meeting',
            message='About to close prejoin modal',
            data={'current_url': (self.driver.current_url or '')}
        )
        # endregion
        self._close_prejoin_modal()
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H11',
            location='services/meeting_bot.py:_join_meeting',
            message='Returned from close prejoin modal',
            data={}
        )
        # endregion
        self._update_session(log_message='Opened meeting page...')
        time.sleep(4)
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H11',
            location='services/meeting_bot.py:_join_meeting',
            message='Starting join readiness phase',
            data={'current_url': (self.driver.current_url or '')}
        )
        # endregion

        if 'meet.google.com' not in (self.driver.current_url or '').lower():
            self._update_session(log_message='Direct URL did not open Meet room; trying manual meet code entry...')
            filled_code = self._fill_meeting_code()
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H4',
                location='services/meeting_bot.py:_join_meeting',
                message='Manual code fill after non-meet URL',
                data={'filled_code': filled_code, 'current_url': (self.driver.current_url or '')}
            )
            # endregion
            time.sleep(2)

        # Turn off camera
        try:
            for sel in ['[aria-label*="camera" i]', '[aria-label*="Turn off camera" i]']:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    btn.click()
                    self._update_session(log_message='Camera turned off')
                    break
                except Exception:
                    continue
        except Exception:
            pass

        time.sleep(2)

        if 'meet.google.com' not in (self.driver.current_url or '').lower():
            filled_code_again = self._fill_meeting_code()
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H4',
                location='services/meeting_bot.py:_join_meeting',
                message='Second manual code fill check',
                data={'filled_code': filled_code_again, 'current_url': (self.driver.current_url or '')}
            )
            # endregion
            time.sleep(2)

        # Mute mic
        try:
            for sel in ['[aria-label*="microphone" i]', '[aria-label*="Turn off microphone" i]']:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    is_muted = btn.get_attribute('data-is-muted')
                    if is_muted == 'false':
                        btn.click()
                        self._update_session(log_message='Microphone muted')
                    break
                except Exception:
                    continue
        except Exception:
            pass

        time.sleep(2)

        # Click join button
        join_clicked = False
        join_click_meta = {}
        # region agent log
        prejoin_ready = False
        prejoin_flags = {'snapshot_error': True}
        prejoin_deadline = time.time() + 45
        prejoin_started_at = time.time()
        cannot_join_streak = 0
        cannot_join_grace_sec = 20
        cannot_join_min_streak = 3
        next_wait_log_at = 5
        while time.time() < prejoin_deadline:
            prejoin_flags = self._is_join_ui_ready_dom()
            elapsed = int(time.time() - prejoin_started_at)
            if prejoin_flags.get('has_join_now') or prejoin_flags.get('has_ask_to_join') or prejoin_flags.get('has_join_button'):
                break
            if prejoin_flags.get('has_cannot_join'):
                cannot_join_streak += 1
                if elapsed >= cannot_join_grace_sec and cannot_join_streak >= cannot_join_min_streak:
                    break
            else:
                cannot_join_streak = 0
            if elapsed >= next_wait_log_at:
                self._update_session(log_message=f'Waiting for join controls... {elapsed}s')
                next_wait_log_at += 5
            time.sleep(1)

        if prejoin_flags.get('has_cannot_join'):
            # region agent log
            self._debug_log(
                run_id='initial',
                hypothesis_id='H12',
                location='services/meeting_bot.py:_join_meeting',
                message='Cannot-join state detected, attempting authuser recovery',
                data={'current_url': (self.driver.current_url or ''), 'flags': prejoin_flags}
            )
            # endregion
            prejoin_flags = self._attempt_authuser_recovery()

        # If recovery found unblocked state but still no join controls, keep waiting briefly.
        if (
            not prejoin_flags.get('has_cannot_join', False) and
            not prejoin_flags.get('has_join_now') and
            not prejoin_flags.get('has_ask_to_join') and
            not prejoin_flags.get('has_join_button')
        ):
            self._update_session(
                log_message=(
                    f'Join controls not visible yet, waiting a bit longer... '
                    f'(hint={prejoin_flags.get("state_hint", "n/a")})'
                )
            )
            late_deadline = time.time() + 20
            late_next_log_at = 5
            late_started_at = time.time()
            while time.time() < late_deadline:
                prejoin_flags = self._is_join_ui_ready_dom()
                late_elapsed = int(time.time() - late_started_at)
                if prejoin_flags.get('has_join_now') or prejoin_flags.get('has_ask_to_join') or prejoin_flags.get('has_join_button'):
                    break
                if prejoin_flags.get('has_cannot_join'):
                    break
                if late_elapsed >= late_next_log_at:
                    self._update_session(
                        log_message=(
                            f'Still waiting for join controls... {late_elapsed}s '
                            f'(hint={prejoin_flags.get("state_hint", "n/a")})'
                        )
                    )
                    late_next_log_at += 5
                time.sleep(1)

        prejoin_ready = not prejoin_flags.get('has_cannot_join', False) and (
            prejoin_flags.get('has_join_now') or
            prejoin_flags.get('has_ask_to_join') or
            prejoin_flags.get('has_join_button')
        )
        self._debug_log(
            run_id='initial',
            hypothesis_id='H10',
            location='services/meeting_bot.py:_join_meeting',
            message='Pre-join readiness check result',
            data={'prejoin_ready': prejoin_ready, 'flags': prejoin_flags}
        )
        # endregion

        if not prejoin_ready:
            if prejoin_flags.get('has_cannot_join'):
                raise Exception("Google Meet blocked join for available signed-in accounts (shows 'You can't join this video call').")
            raise Exception('Join controls did not appear in time. The selected account may not have access or is waiting for organizer approval.')

        # region agent log
        try:
            join_ui_snapshot = self.driver.execute_script('''
                const buttons = Array.from(document.querySelectorAll('button,[role="button"]'))
                    .filter((el) => (el.offsetParent !== null))
                    .slice(0, 25)
                    .map((el) => ({
                        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                        aria: (el.getAttribute('aria-label') || '').trim().slice(0, 80),
                        disabled: !!el.disabled
                    }));
                const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
                return {
                    button_count: buttons.length,
                    buttons: buttons,
                    has_join_now: /join now/i.test(bodyText),
                    has_ask_to_join: /ask to join/i.test(bodyText),
                    has_cannot_join: /you can't join this video call/i.test(bodyText)
                };
            ''')
        except Exception:
            join_ui_snapshot = {'snapshot_error': True}
        self._debug_log(
            run_id='initial',
            hypothesis_id='H10',
            location='services/meeting_bot.py:_join_meeting',
            message='Pre-click join UI snapshot',
            data=join_ui_snapshot
        )
        # endregion
        join_selectors = [
            '//button[contains(., "Join now")]',
            '//button[contains(., "Ask to join")]',
            '//span[contains(text(), "Join now")]/ancestor::button',
            '//span[contains(text(), "Ask to join")]/ancestor::button',
            '//button[contains(., "Request to join")]',
            '//span[contains(text(), "Request to join")]/ancestor::button'
        ]

        for xpath in join_selectors:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                btn_text = (btn.text or '').strip()
                btn_aria = (btn.get_attribute('aria-label') or '').strip()
                btn.click()
                join_clicked = True
                join_click_meta = {'xpath': xpath, 'text': btn_text, 'aria': btn_aria}
                self._update_session(log_message='Clicked join button')
                break
            except Exception:
                continue

        if not join_clicked:
            try:
                for btn in self.driver.find_elements(By.TAG_NAME, 'button'):
                    txt = (btn.text or '').strip().lower()
                    if txt in ('join now', 'ask to join', 'request to join'):
                        btn_text = (btn.text or '').strip()
                        btn_aria = (btn.get_attribute('aria-label') or '').strip()
                        btn.click()
                        join_clicked = True
                        join_click_meta = {'xpath': 'fallback_text_join', 'text': btn_text, 'aria': btn_aria}
                        self._update_session(log_message='Clicked join button (fallback)')
                        break
            except Exception:
                pass

        # If still not joined, try typing URL into any meet code input (some locales/show states)
        if not join_clicked:
            from selenium.webdriver.common.keys import Keys
            input_selectors = [
                '//input[contains(@aria-label, "meeting code")]',
                '//input[contains(@placeholder, "meeting")]',
                '//input[contains(@id, "i3")][@type="text"]',
                '//input[@type="text"]'
            ]
            for xpath in input_selectors:
                try:
                    code_input = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    if code_input.is_displayed():
                        code_input.click()
                        code_input.clear()
                        code_input.send_keys(self.meet_link)
                        code_input.send_keys(Keys.ENTER)
                        join_clicked = True
                        self._update_session(log_message='Entered meeting link manually')
                        break
                except Exception:
                    continue

        if not join_clicked:
            raise Exception('Could not find join button.')
        # region agent log
        self._debug_log(
            run_id='initial',
            hypothesis_id='H10',
            location='services/meeting_bot.py:_join_meeting',
            message='Join interaction completed',
            data={
                'join_clicked': join_clicked,
                'join_click_meta': join_click_meta,
                'final_url': (self.driver.current_url or ''),
                'title': (self.driver.title or '')
            }
        )
        # endregion

        time.sleep(8)
        # region agent log
        try:
            post_click_snapshot = self.driver.execute_script('''
                const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
                return {
                    has_join_now: /join now/i.test(bodyText),
                    has_ask_to_join: /ask to join/i.test(bodyText),
                    has_cannot_join: /you can't join this video call/i.test(bodyText),
                    current_url: window.location.href
                };
            ''')
        except Exception:
            post_click_snapshot = {'snapshot_error': True}
        self._debug_log(
            run_id='initial',
            hypothesis_id='H10',
            location='services/meeting_bot.py:_join_meeting',
            message='Post-click join UI snapshot',
            data=post_click_snapshot
        )
        # endregion
        self._update_session(log_message='In meeting room')

    def _listen_loop(self):
        settings = Settings.get()
        detection_terms = settings.get_all_detection_terms()
        recording = AudioRecording.query.first()

        self._update_session(log_message=f'Listening for: {", ".join(detection_terms)}')

        recognition_js = self._get_speech_recognition_js(detection_terms)
        self.driver.execute_script(recognition_js)
        self._update_session(log_message='Speech recognition active')

        responded_count = 0
        while self.running:
            time.sleep(2)
            try:
                result = self.driver.execute_script('return window.__meetBot_detected || null;')
                if result:
                    self.driver.execute_script('window.__meetBot_detected = null;')
                    detected_text = result.get('text', '')
                    self._update_session('name_detected', f'NAME DETECTED! Heard: "{detected_text}"')

                    if recording:
                        self._play_response(recording.id)
                        responded_count += 1
                        self._update_session('responded', f'Played "present" response (#{responded_count})')

                    time.sleep(3)
                    self._update_session('listening', 'Back to listening...')

                transcriptions = self.driver.execute_script(
                    'var t = window.__meetBot_transcriptions || []; window.__meetBot_transcriptions = []; return t;'
                )
                if transcriptions:
                    for t in transcriptions:
                        self._update_session(log_message=f'Heard: "{t}"')

            except Exception as e:
                err = str(e).lower()
                if 'no such window' in err or 'not reachable' in err:
                    self._update_session('ended', 'Browser window closed')
                    self.running = False
                    break

    def _get_speech_recognition_js(self, detection_terms):
        terms_js = ','.join([f'"{t}"' for t in detection_terms])
        return f'''
        (function() {{
            window.__meetBot_detected = null;
            window.__meetBot_transcriptions = [];
            const detectionTerms = [{terms_js}];
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {{ console.error("No Speech Recognition"); return; }}
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
                        if (event.results[i].isFinal) {{
                            const transcript = normalizeText(event.results[i][0].transcript);
                            window.__meetBot_transcriptions.push(transcript);
                            for (const term of detectionTerms) {{
                                const normalizedTerm = normalizeText(term);
                                if (!normalizedTerm) {{
                                    continue;
                                }}
                                const re = new RegExp('(^|\\\\W)' + escapeRegExp(normalizedTerm) + '(\\\\W|$)');
                                if (re.test(transcript)) {{
                                    window.__meetBot_detected = {{ text: transcript, term: term, confidence: event.results[i][0].confidence }};
                                    break;
                                }}
                            }}
                        }}
                    }}
                }};
                recognition.onerror = function(event) {{
                    if (event.error !== 'aborted') setTimeout(startRecognition, 1000);
                }};
                recognition.onend = function() {{ setTimeout(startRecognition, 500); }};
                recognition.start();
            }}
            startRecognition();
        }})();
        '''

    def _play_response(self, recording_id):
        playback_js = get_audio_playback_js(recording_id)
        try:
            self.driver.execute_script(playback_js)
            time.sleep(4)
        except Exception as e:
            self._update_session(log_message=f'Audio playback error: {str(e)}')

    def stop(self):
        self.running = False

    def _cleanup(self):
        self.running = False
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        if self.runtime_profile_root:
            try:
                shutil.rmtree(self.runtime_profile_root, ignore_errors=True)
                # region agent log
                self._debug_log(
                    run_id='initial',
                    hypothesis_id='H9',
                    location='services/meeting_bot.py:_cleanup',
                    message='Removed temp runtime profile root',
                    data={'temp_profile_root': self.runtime_profile_root}
                )
                # endregion
            except Exception:
                pass
            finally:
                self.runtime_profile_root = None
        session = db.session.get(MeetingSession, self.session_id)
        if session and session.status not in ('ended', 'error'):
            session.status = 'ended'
            session.ended_at = datetime.utcnow()
            session.add_log('Session ended')
            db.session.commit()
