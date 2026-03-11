import os
import threading
import json
import re
from flask import Blueprint, render_template, jsonify, request
from database.db import db
from database.models import Settings

google_bp = Blueprint('google', __name__)
_login_browser_open = False


def _detect_chrome_roots():
    home = os.path.expanduser('~')
    paths = [
        os.path.join(home, 'Library', 'Application Support', 'Google', 'Chrome'),
        os.path.join(home, 'Library', 'Application Support', 'Google', 'Chrome Beta'),
        os.path.join(home, 'Library', 'Application Support', 'Chromium'),
        os.path.join(home, '.config', 'google-chrome'),
        os.path.join(home, '.config', 'google-chrome-beta'),
        os.path.join(home, '.config', 'chromium'),
        os.path.join(home, 'AppData', 'Local', 'Google', 'Chrome', 'User Data'),
        os.path.join(home, 'AppData', 'Local', 'Google', 'Chrome SxS', 'User Data'),
        os.path.join(home, 'AppData', 'Local', 'Chromium', 'User Data'),
    ]
    return [p for p in paths if os.path.exists(p)]


def _extract_email(text):
    matches = re.findall(r'[\w\.\-+]+@[\w\.\-]+\.[\w\.\-]+', text or '')
    return matches[0] if matches else None


def _safe_json_load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _get_profile_metadata(profile_dir, profile_name):
    prefs_path = os.path.join(profile_dir, 'Preferences')
    root_path = os.path.dirname(profile_dir)
    local_state = _safe_json_load(os.path.join(root_path, 'Local State'))

    if not os.path.isfile(prefs_path):
        return profile_name

    data = _safe_json_load(prefs_path)
    if not isinstance(data, dict):
        return profile_name

    discovered_name = None
    email = None
    local_profile_name = profile_name

    try:
        info_cache = (
            local_state.get('profile', {})
            .get('info_cache', {})
            .get(profile_name, {})
        )
        if isinstance(info_cache, dict):
            local_profile_name = (
                info_cache.get('name')
                or info_cache.get('shortcut_name')
                or local_profile_name
            )
            if isinstance(local_profile_name, str):
                local_profile_name = local_profile_name.strip() or local_profile_name
            email = info_cache.get('user_name') or info_cache.get('email')
    except Exception:
        pass

    try:
        profile_data = data.get('profile', {})
        if isinstance(profile_data, dict):
            discovered_name = (
                profile_data.get('name')
                or profile_data.get('display_name')
                or profile_data.get('gaia_name')
            )
            if isinstance(discovered_name, str):
                discovered_name = discovered_name.strip() or discovered_name
    except Exception:
        pass

    if not email:
        account_info = data.get('account_info')
        if isinstance(account_info, dict):
            email = account_info.get('email')
            if not discovered_name:
                discovered_name = account_info.get('full_name') or account_info.get('name')

        if not email and isinstance(account_info, list):
            for item in account_info:
                if isinstance(item, dict) and isinstance(item.get('email'), str):
                    email = item.get('email')
                    if not discovered_name:
                        discovered_name = item.get('full_name') or item.get('name')
                    if email:
                        break

    if not email:
        email = _extract_email(json.dumps(data))

    label_parts = []
    if discovered_name:
        label_parts.append(discovered_name)
    if local_profile_name and local_profile_name not in label_parts:
        label_parts.append(local_profile_name)
    if email:
        if email not in label_parts:
            label_parts.append(email.strip())

    if label_parts:
        return ' - '.join(label_parts)

    return profile_name


def _discover_chrome_profiles():
    profiles = []
    for root in _detect_chrome_roots():
        for item in sorted(os.listdir(root)):
            profile_dir = os.path.join(root, item)
            if not os.path.isdir(profile_dir):
                continue
            if item == 'Default' or item.startswith('Profile'):
                metadata = _get_profile_metadata(profile_dir, item)
                label = metadata or item
                profiles.append({
                    'root': root,
                    'name': item,
                    'path': os.path.join(root, item),
                    'display': label,
                    'exists': os.path.exists(os.path.join(profile_dir, 'Preferences'))
                })
    # Sort Default to top, then Profile 1...N
    profiles.sort(key=lambda p: (0 if p['name'] == 'Default' else 1, p['name'].lower()))
    return profiles


def _profile_is_valid(root_path, profile_name):
    if not root_path or not profile_name:
        return False
    profile_dir = os.path.join(root_path, profile_name)
    return os.path.isdir(root_path) and os.path.isdir(profile_dir)


def _has_profile_configured(settings):
    has_profile = settings.chrome_profile_path is not None and settings.chrome_profile_name is not None
    if not has_profile:
        return False
    return _profile_is_valid(settings.chrome_profile_path, settings.chrome_profile_name)


@google_bp.route('/setup')
def setup():
    settings = Settings.get()
    available_profiles = _discover_chrome_profiles()
    has_profile = _has_profile_configured(settings)
    return render_template('google_setup.html',
                           has_profile=has_profile,
                           available_profiles=available_profiles,
                           selected_root=settings.chrome_profile_path,
                           selected_profile=settings.chrome_profile_name,
                           browser_open=_login_browser_open)


@google_bp.route('/launch-login', methods=['POST'])
def launch_login():
    global _login_browser_open
    if _login_browser_open:
        return jsonify({'error': 'Login browser already open. Log in and close it first.'}), 400

    settings = Settings.get()
    payload = request.get_json(silent=True) or {}
    root_path = (payload.get('root_path') or '').strip()
    profile_name = (payload.get('profile_name') or '').strip()

    if not root_path or not profile_name:
        return jsonify({'error': 'Please select a Chrome profile first'}), 400

    if not _profile_is_valid(root_path, profile_name):
        return jsonify({'error': 'Selected Chrome profile is not valid'}), 400

    settings.chrome_profile_path = root_path
    settings.chrome_profile_name = profile_name
    db.session.commit()

    def open_chrome():
        global _login_browser_open
        import subprocess
        try:
            import undetected_chromedriver as uc

            try:
                result = subprocess.run(
                    ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'],
                    capture_output=True, text=True
                )
                chrome_ver = int(result.stdout.strip().split()[-1].split('.')[0])
            except Exception:
                chrome_ver = None

            options = uc.ChromeOptions()
            options.add_argument(f'--user-data-dir={root_path}')
            options.add_argument(f'--profile-directory={profile_name}')
            options.add_argument('--no-first-run')
            options.add_argument('--no-default-browser-check')

            _login_browser_open = True
            driver = uc.Chrome(options=options, version_main=chrome_ver)
            driver.get('https://accounts.google.com')

            import time
            while True:
                try:
                    _ = driver.title
                    time.sleep(1)
                except Exception:
                    break

            try:
                driver.quit()
            except Exception:
                pass
        except Exception as e:
            print(f'Chrome launch error: {e}')
        finally:
            _login_browser_open = False

    t = threading.Thread(target=open_chrome, daemon=True)
    t.start()

    return jsonify({'success': True})


@google_bp.route('/status')
def status():
    settings = Settings.get()
    has_profile = _has_profile_configured(settings)
    return jsonify({
        'configured': has_profile,
        'browser_open': _login_browser_open
    })
