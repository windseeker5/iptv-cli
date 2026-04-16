#!/usr/bin/env python3

import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer


LOG_PATH = os.environ.get("HLS_LOG_PATH", "/logs/hls_access.log")
WINDOW_SECONDS = int(os.environ.get("VIEWER_WINDOW_SECONDS", "45"))
PORT = int(os.environ.get("PORT", "9090"))
MAX_READ_BYTES = 2 * 1024 * 1024


def _extract_ip(remote_ip: str, x_forwarded_for: str) -> str:
    if x_forwarded_for and x_forwarded_for != "-":
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            return first
    return remote_ip.strip()


def _count_active_viewers() -> int:
    if not os.path.exists(LOG_PATH):
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    active_ips = set()

    with open(LOG_PATH, "rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        file_size = log_file.tell()
        log_file.seek(max(0, file_size - MAX_READ_BYTES), os.SEEK_SET)
        data = log_file.read().decode("utf-8", errors="ignore")

    for line in data.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue

        time_str, remote_ip, xff, request_uri, status = parts

        if status != "200":
            continue

        if not request_uri.startswith("/hls/"):
            continue

        if not (request_uri.endswith(".m3u8") or request_uri.endswith(".ts")):
            continue

        try:
            ts = datetime.fromisoformat(time_str)
        except ValueError:
            continue

        if ts < cutoff:
            continue

        active_ips.add(_extract_ip(remote_ip, xff))

    return len(active_ips)


class ViewerCounterHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/api/viewers":
            self.send_response(404)
            self.end_headers()
            return

        count = _count_active_viewers()
        payload = {
            "active_viewers": count,
            "window_seconds": WINDOW_SECONDS,
            "source": "hls_access_log",
        }

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ViewerCounterHandler)
    server.serve_forever()
