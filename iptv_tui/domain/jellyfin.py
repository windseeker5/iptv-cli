"""Minimal Jellyfin API client: authenticate and trigger a library scan.

Only what the nightly recording pipeline needs. Credentials and the
resulting access token are never logged.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_CLIENT_HEADER = (
    'MediaBrowser Client="IPTV CLI", Device="iptv-cli", '
    'DeviceId="iptv-cli-automation", Version="1.0.0"'
)


def authenticate(base_url: str, username: str, password: str) -> str | None:
    """Return a Jellyfin access token, or None on failure."""
    url = base_url.rstrip("/") + "/Users/AuthenticateByName"
    headers = {
        "Authorization": _CLIENT_HEADER,
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            url,
            json={"Username": username, "Pw": password},
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.error(f"Jellyfin authentication request failed: {exc}")
        return None

    if response.status_code != 200:
        logger.error(f"Jellyfin authentication failed: HTTP {response.status_code}")
        return None

    token = response.json().get("AccessToken")
    if not token:
        logger.error("Jellyfin authentication response had no AccessToken")
        return None
    return token


def trigger_library_scan(base_url: str, token: str) -> bool:
    """Trigger a full library scan. Returns True on success."""
    url = base_url.rstrip("/") + "/Library/Refresh"
    headers = {
        "X-Emby-Token": token,
        "Authorization": f'MediaBrowser Token="{token}"',
    }
    try:
        response = requests.post(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        logger.error(f"Jellyfin library scan request failed: {exc}")
        return False

    if response.status_code not in (200, 204):
        logger.error(f"Jellyfin library scan failed: HTTP {response.status_code}")
        return False

    logger.info("Jellyfin library scan triggered")
    return True
