import glob
import os

from selenium import webdriver
from selenium.webdriver.edge.service import Service


def _candidate_edge_driver_paths():
    paths = []

    env_path = (os.environ.get('EDGE_DRIVER_PATH') or '').strip()
    if env_path:
        paths.append(env_path)

    local_app_data = os.environ.get('LOCALAPPDATA') or ''
    if local_app_data:
        winget_glob = os.path.join(
            local_app_data,
            'Microsoft',
            'WinGet',
            'Packages',
            'Microsoft.EdgeDriver_*',
            'msedgedriver.exe',
        )
        paths.extend(sorted(glob.glob(winget_glob), reverse=True))

    unique_paths = []
    seen = set()
    for path in paths:
        norm = os.path.normpath(path)
        if norm in seen:
            continue
        seen.add(norm)
        unique_paths.append(path)
    return unique_paths


def create_edge_driver(options):
    last_error = None

    for candidate in _candidate_edge_driver_paths():
        if not os.path.isfile(candidate):
            continue
        try:
            return webdriver.Edge(service=Service(candidate), options=options)
        except Exception as exc:
            last_error = exc

    try:
        return webdriver.Edge(options=options)
    except Exception as exc:
        if 'Unable to obtain driver for MicrosoftEdge' not in str(exc):
            raise
        last_error = exc

    try:
        from webdriver_manager.microsoft import EdgeChromiumDriverManager

        service = Service(EdgeChromiumDriverManager().install())
        return webdriver.Edge(service=service, options=options)
    except Exception as fallback_exc:
        if last_error:
            raise RuntimeError(
                f'Unable to obtain Edge WebDriver. Base error: {last_error}; fallback error: {fallback_exc}'
            ) from last_error
        raise RuntimeError(f'Unable to obtain Edge WebDriver: {fallback_exc}') from fallback_exc
