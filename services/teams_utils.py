import hashlib
import re
from urllib.parse import urlparse

from services.teams_selectors import TEAMS_AUTH_HOST_HINTS, TEAMS_LOBBY_TEXT_HINTS


def normalize_text(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', ' ', str(value or '').lower())).strip()


def contains_detection_term(text, detection_terms):
    normalized_text = normalize_text(text)
    for term in detection_terms:
        normalized_term = normalize_text(term)
        if not normalized_term:
            continue
        if re.search(rf'(^|\W){re.escape(normalized_term)}(\W|$)', normalized_text):
            return True, normalized_term, normalized_text
    return False, None, normalized_text


def is_auth_redirect_url(url):
    try:
        parsed = urlparse(url or '')
    except Exception:
        return False

    host = (parsed.netloc or '').lower()
    return any(hint in host for hint in TEAMS_AUTH_HOST_HINTS)


def is_waiting_lobby_text(text):
    normalized = normalize_text(text)
    return any(normalize_text(hint) in normalized for hint in TEAMS_LOBBY_TEXT_HINTS)


def hash_dom_snippet(html, max_chars=4000):
    snippet = (html or '')[:max_chars]
    digest = hashlib.sha256(snippet.encode('utf-8', errors='ignore')).hexdigest()
    return digest, snippet
