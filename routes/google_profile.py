import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import config
from database.db import db
from database.models import Settings
from services.edge_driver import create_edge_driver

google_bp = Blueprint('google', __name__)
_login_browser_open = False

_SUPPORTED_BROWSERS = ('edge', 'chrome')


def _is_windows():
    return os.name == 'nt'


def _browser_type_from_prog_id(prog_id):
    value = str(prog_id or '').lower()
    if 'edge' in value:
        return 'edge'
    if 'chrome' in value or 'chromium' in value:
        return 'chrome'
    return None


def _get_default_chromium_browser():
    if not _is_windows():
        return 'chrome'

    try:
        import winreg

        reg_path = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            prog_id, _ = winreg.QueryValueEx(key, 'ProgId')

        browser_type = _browser_type_from_prog_id(prog_id)
        if browser_type:
            return browser_type
    except Exception:
        pass

    return 'chrome'


def _detect_browser_roots():
    home = os.path.expanduser('~')
    roots = {
        'chrome': [
            os.path.join(home, 'Library', 'Application Support', 'Google', 'Chrome'),
            os.path.join(home, 'Library', 'Application Support', 'Google', 'Chrome Beta'),
            os.path.join(home, 'Library', 'Application Support', 'Chromium'),
            os.path.join(home, '.config', 'google-chrome'),
            os.path.join(home, '.config', 'google-chrome-beta'),
            os.path.join(home, '.config', 'chromium'),
            os.path.join(home, 'AppData', 'Local', 'Google', 'Chrome', 'User Data'),
            os.path.join(home, 'AppData', 'Local', 'Google', 'Chrome SxS', 'User Data'),
            os.path.join(home, 'AppData', 'Local', 'Chromium', 'User Data'),
        ],
        'edge': [
            os.path.join(home, 'Library', 'Application Support', 'Microsoft Edge'),
            os.path.join(home, 'Library', 'Application Support', 'Microsoft Edge Beta'),
            os.path.join(home, '.config', 'microsoft-edge'),
            os.path.join(home, '.config', 'microsoft-edge-beta'),
            os.path.join(home, 'AppData', 'Local', 'Microsoft', 'Edge', 'User Data'),
            os.path.join(home, 'AppData', 'Local', 'Microsoft', 'Edge Beta', 'User Data'),
        ],
    }
    return {
        browser_type: [path for path in paths if os.path.exists(path)]
        for browser_type, paths in roots.items()
    }


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
        info_cache = local_state.get('profile', {}).get('info_cache', {}).get(profile_name, {})
        if isinstance(info_cache, dict):
            local_profile_name = info_cache.get('name') or info_cache.get('shortcut_name') or local_profile_name
            if isinstance(local_profile_name, str):
                local_profile_name = local_profile_name.strip() or local_profile_name
            email = info_cache.get('user_name') or info_cache.get('email')
    except Exception:
        pass

    try:
        profile_data = data.get('profile', {})
        if isinstance(profile_data, dict):
            discovered_name = profile_data.get('name') or profile_data.get('display_name') or profile_data.get('gaia_name')
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
    if email and email not in label_parts:
        label_parts.append(email.strip())

    return ' - '.join(label_parts) if label_parts else profile_name


def _discover_browser_profiles():
    profiles = []
    roots_by_browser = _detect_browser_roots()
    for browser_type, roots in roots_by_browser.items():
        for root in roots:
            for item in sorted(os.listdir(root)):
                profile_dir = os.path.join(root, item)
                if not os.path.isdir(profile_dir):
                    continue
                if item == 'Default' or item.startswith('Profile'):
                    metadata = _get_profile_metadata(profile_dir, item)
                    label = metadata or item
                    profiles.append(
                        {
                            'browser_type': browser_type,
                            'root': root,
                            'name': item,
                            'path': profile_dir,
                            'display': label,
                            'exists': os.path.exists(os.path.join(profile_dir, 'Preferences')),
                        }
                    )

    profiles.sort(
        key=lambda p: (
            0 if p['browser_type'] == 'edge' else 1,
            0 if p['name'] == 'Default' else 1,
            p['name'].lower(),
        )
    )
    return profiles


def _profile_is_valid(root_path, profile_name, browser_type=None):
    if not root_path or not profile_name:
        return False

    roots_by_browser = _detect_browser_roots()
    if browser_type in _SUPPORTED_BROWSERS and root_path not in roots_by_browser.get(browser_type, []):
        return False

    profile_dir = os.path.join(root_path, profile_name)
    return os.path.isdir(root_path) and os.path.isdir(profile_dir)



def _has_profile_configured(settings):
    browser_type = (settings.browser_type or 'chrome').lower()

    has_profile = settings.chrome_profile_path is not None and settings.chrome_profile_name is not None
    if not has_profile:
        return False
    return _profile_is_valid(
        settings.chrome_profile_path,
        settings.chrome_profile_name,
        browser_type,
    )


@google_bp.route('/setup')
def setup():
    settings = Settings.get()
    default_browser_type = _get_default_chromium_browser()
    if (settings.browser_type or '').lower() not in _SUPPORTED_BROWSERS:
        settings.browser_type = default_browser_type
    if settings.browser_type == 'chrome' and _is_windows():
        settings.browser_type = 'edge'
    db.session.commit()

    available_profiles = _discover_browser_profiles()
    has_profile = _has_profile_configured(settings)
    return render_template(
        'google_setup.html',
        has_profile=has_profile,
        available_profiles=available_profiles,
        selected_root=settings.chrome_profile_path,
        selected_profile=settings.chrome_profile_name,
        selected_browser_type=(settings.browser_type or default_browser_type).lower(),
        default_browser_type=default_browser_type,
        browser_open=_login_browser_open,
    )


@google_bp.route('/launch-login', methods=['POST'])
def launch_login():
    global _login_browser_open
    if _login_browser_open:
        return jsonify({'error': 'Login browser already open. Log in and close it first.'}), 400

    settings = Settings.get()
    payload = request.get_json(silent=True) or {}
    root_path = (payload.get('root_path') or '').strip()
    profile_name = (payload.get('profile_name') or '').strip()
    browser_type = (payload.get('browser_type') or settings.browser_type or _get_default_chromium_browser()).strip().lower()

    if browser_type not in _SUPPORTED_BROWSERS:
        return jsonify({'error': 'Unsupported browser type. Choose Edge or Chrome.'}), 400

    launch_root_path = root_path
    launch_profile_name = profile_name

    if not root_path or not profile_name:
        return jsonify({'error': f'Please select a {browser_type.title()} profile first'}), 400

    if not _profile_is_valid(root_path, profile_name, browser_type):
        return jsonify({'error': f'Selected {browser_type.title()} profile is not valid'}), 400

    settings.browser_type = browser_type
    settings.chrome_profile_path = root_path
    settings.chrome_profile_name = profile_name
    db.session.commit()

    def open_browser():
        global _login_browser_open
        _login_browser_open = True
        driver = None
        try:
            from selenium import webdriver

            target_url = 'about:blank'

            if browser_type == 'edge':
                subprocess.run(['taskkill', '/IM', 'msedge.exe', '/F'], capture_output=True, check=False)
                time.sleep(1)
                options = webdriver.EdgeOptions()
                options.add_argument(f'--user-data-dir={launch_root_path}')
                options.add_argument(f'--profile-directory={launch_profile_name}')
                options.add_argument('--no-first-run')
                options.add_argument('--no-default-browser-check')
                options.add_argument('--disable-features=msEdgeSidebarV2')
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_experimental_option('excludeSwitches', ['enable-automation'])
                options.add_experimental_option('useAutomationExtension', False)
                driver = create_edge_driver(options)
            else:
                subprocess.run(['taskkill', '/IM', 'chrome.exe', '/F'], capture_output=True, check=False)
                time.sleep(1)
                options = webdriver.ChromeOptions()
                options.add_argument(f'--user-data-dir={launch_root_path}')
                options.add_argument(f'--profile-directory={launch_profile_name}')
                options.add_argument('--no-first-run')
                options.add_argument('--no-default-browser-check')
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_experimental_option('excludeSwitches', ['enable-automation'])
                options.add_experimental_option('useAutomationExtension', False)
                driver = webdriver.Chrome(options=options)

            driver.get(target_url)

            while True:
                try:
                    _ = driver.title
                    import time

                    time.sleep(1)
                except Exception:
                    break
        except Exception as exc:
            print(f'Browser launch error ({browser_type}): {exc}')
        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            _login_browser_open = False

    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    return jsonify({'success': True})


@google_bp.route('/status')
def status():
    settings = Settings.get()
    has_profile = _has_profile_configured(settings)
    return jsonify(
        {
            'configured': has_profile,
            'browser_open': _login_browser_open,
            'browser_type': (settings.browser_type or _get_default_chromium_browser()).lower(),
        }
    )
