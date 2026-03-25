from urllib.parse import urlparse

SUPPORTED_PROVIDERS = {'meet', 'teams'}


def normalize_provider(provider):
    value = (provider or '').strip().lower()
    return value if value in SUPPORTED_PROVIDERS else None


def detect_provider_from_link(meeting_link):
    if not meeting_link:
        return None

    try:
        parsed = urlparse(meeting_link)
    except Exception:
        return None

    host = (parsed.netloc or '').lower()
    if host.startswith('meet.google.com'):
        return 'meet'

    teams_hosts = (
        'teams.microsoft.com',
        'teams.live.com',
        'teams.office.com',
    )
    if any(host == h or host.endswith(f'.{h}') for h in teams_hosts):
        return 'teams'

    return None


def resolve_provider(meeting_link, provider_override=None):
    override = normalize_provider(provider_override)
    if override:
        return override
    return detect_provider_from_link(meeting_link)


def validate_link_for_provider(meeting_link, provider):
    if not meeting_link or not provider:
        return False

    try:
        parsed = urlparse(meeting_link)
    except Exception:
        return False

    if parsed.scheme not in {'http', 'https'}:
        return False

    host = (parsed.netloc or '').lower()
    path = (parsed.path or '').lower()

    if provider == 'meet':
        if host != 'meet.google.com':
            return False
        parts = [p for p in path.split('/') if p]
        if not parts:
            return False
        code = parts[0]
        chunks = code.split('-')
        if len(chunks) < 3:
            return False
        return all(chunk.isalpha() for chunk in chunks[:3])

    if provider == 'teams':
        teams_hosts = (
            'teams.microsoft.com',
            'teams.live.com',
            'teams.office.com',
        )
        if not any(host == h or host.endswith(f'.{h}') for h in teams_hosts):
            return False
        valid_path_fragments = ('/l/meetup-join/', '/meetup-join/', '/meeting', '/meet/')
        return any(fragment in path for fragment in valid_path_fragments)

    return False
