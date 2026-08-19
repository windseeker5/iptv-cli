"""Application configuration loaded from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv


def _ensure_loaded() -> None:
    """Load .env once if not already loaded."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path)


_ensure_loaded()


class Config:
    """Centralized access to environment variables."""

    # Required
    IPTV_SERVER_URL: str = os.getenv("IPTV_SERVER_URL", "")
    IPTV_USERNAME: str = os.getenv("IPTV_USERNAME", "")
    IPTV_PASSWORD: str = os.getenv("IPTV_PASSWORD", "")

    # Optional overrides
    EPG_SERVER_URL: str = os.getenv("EPG_SERVER_URL", "")
    INJECT_SERVER_URL: str = os.getenv("INJECT_SERVER_URL", "")

    # NGINX / Docker ports
    NGINX_RTMP_PORT: str = os.getenv("NGINX_RTMP_PORT", "1935")
    NGINX_HTTP_PORT: str = os.getenv("NGINX_HTTP_PORT", "8080")
    NGINX_ADMIN_PORT: str = os.getenv("NGINX_ADMIN_PORT", "8081")

    # Jellyfin
    JELLYFIN_PUBLISHED_SERVER_URL: str = os.getenv(
        "JELLYFIN_PUBLISHED_SERVER_URL", ""
    )
    JELLYFIN_DOMAIN: str = os.getenv("JELLYFIN_DOMAIN", "tv.dresdell.com")
    JELLYFIN_HTTP_PORT: str = os.getenv("JELLYFIN_HTTP_PORT", "8096")
    JELLYFIN_HTTPS_PORT: str = os.getenv("JELLYFIN_HTTPS_PORT", "8920")

    # USB / media paths
    USB_RECORDS_PATH: str = os.getenv("USB_RECORDS_PATH", "./nginx/recordings")
    USB_MOVIES_PATH: str = os.getenv("USB_MOVIES_PATH", "./media/movies")
    USB_MUSIC_PATH: str = os.getenv("USB_MUSIC_PATH", "./media/music")
    USB_PHOTOS_PATH: str = os.getenv("USB_PHOTOS_PATH", "./media/photos")
    JELLYFIN_MEDIA_PATH: str = os.getenv("JELLYFIN_MEDIA_PATH", "./media")

    # Samba
    SAMBA_NETBIOS_PORT: str = os.getenv("SAMBA_NETBIOS_PORT", "137")
    SAMBA_NETBIOS_DGM_PORT: str = os.getenv("SAMBA_NETBIOS_DGM_PORT", "138")
    SAMBA_NETBIOS_SSN_PORT: str = os.getenv("SAMBA_NETBIOS_SSN_PORT", "139")
    SAMBA_SMB_PORT: str = os.getenv("SAMBA_SMB_PORT", "445")

    # System
    TZ: str = os.getenv("TZ", "UTC")
    EDITOR: str = os.environ.get("EDITOR", "nano")

    @classmethod
    def credentials_ok(cls) -> bool:
        """Return True if all required IPTV credentials are set."""
        return all([cls.IPTV_SERVER_URL, cls.IPTV_USERNAME, cls.IPTV_PASSWORD])

    @classmethod
    def missing_credentials(cls) -> list[str]:
        """Return list of missing required credential variable names."""
        missing = []
        if not cls.IPTV_SERVER_URL:
            missing.append("IPTV_SERVER_URL")
        if not cls.IPTV_USERNAME:
            missing.append("IPTV_USERNAME")
        if not cls.IPTV_PASSWORD:
            missing.append("IPTV_PASSWORD")
        return missing
