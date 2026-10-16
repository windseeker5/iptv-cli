"""Discord messages for KD CLI jobs. Never raises; never logs the webhook."""

import logging
import os

logger = logging.getLogger(__name__)


def discord(message: str) -> bool:
    """Post to DISCORD_WEBHOOK_URL (loaded from .env by config). True when delivered."""
    from iptv_tui.domain import config  # noqa: F401  (loads .env into the environment)

    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning("Discord message skipped: DISCORD_WEBHOOK_URL is not set")
        return False
    try:
        import requests

        requests.post(url, json={"content": message[:1900]}, timeout=20).raise_for_status()
        return True
    except Exception as exc:
        # The exception text can include the webhook URL, which is a secret.
        logger.error(f"Discord message failed: {type(exc).__name__}")
        return False
