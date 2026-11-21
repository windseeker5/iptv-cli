"""A pretend IPTV provider (Xtream Codes API) for testing kdtv without a real one.

python fake_provider.py 18555   (login: son / secret; needs ffmpeg for the live channel)
Serves the account and lists, a guide with a show on now, a live channel made by
FFmpeg in real time, and a small movie (files/movie.mp4, made by smoke.ps1)."""
import http.server, json, os, subprocess, sys, time, urllib.parse
from datetime import datetime, timedelta, timezone

USER, PASS = "son", "secret"
HERE = os.path.dirname(os.path.abspath(__file__))
MOVIE = os.path.join(HERE, "files", "movie.mp4")

def api(q):
    if q.get("username") != USER or q.get("password") != PASS:
        return {"user_info": {"auth": 0}}
    a = q.get("action")
    if a is None:
        return {"user_info": {"auth": 1, "username": USER, "status": "Active", "exp_date": "1893456000", "max_connections": "1"}, "server_info": {}}
    return {
        "get_live_categories": [{"category_id": "1", "category_name": "Test TV", "parent_id": 0}],
        "get_live_streams": [{"stream_id": 101, "name": "Test Channel HD", "category_id": "1", "epg_channel_id": "test.ch"},
                             {"stream_id": 102, "name": "Other Channel", "category_id": "1", "epg_channel_id": ""}],
        "get_vod_categories": [{"category_id": "5", "category_name": "Test Movies", "parent_id": 0}],
        "get_vod_streams": [{"stream_id": 201, "name": "Test Movie", "category_id": "5", "container_extension": "mp4", "year": "2026", "rating": 7.5, "genre": "Test"}],
        "get_series_categories": [{"category_id": "9", "category_name": "Test Series"}],
        "get_series": [{"series_id": 301, "name": "Test Show", "category_id": "9"}],
    }.get(a, [])

def guide():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    fmt = lambda t: t.strftime("%Y%m%d%H%M%S +0000")
    shows = "".join(f'<programme start="{fmt(now + timedelta(hours=h))}" stop="{fmt(now + timedelta(hours=h+1))}" channel="test.ch"><title>Test Show {h}</title><desc>A test</desc></programme>' for h in range(-1, 6))
    return f'<?xml version="1.0"?><tv><channel id="test.ch"><display-name>Test</display-name></channel>{shows}</tv>'

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/player_api.php":
            return self.send(json.dumps(api(q)).encode(), "application/json")
        if u.path == "/xmltv.php":
            return self.send(guide().encode(), "application/xml")
        if u.path.startswith(f"/live/{USER}/{PASS}/"):
            # A live stream: FFmpeg makes it in real time, for as long as someone reads.
            self.send_response(200); self.send_header("Content-Type", "video/mp2t"); self.end_headers()
            p = subprocess.Popen([os.environ.get("FFMPEG", "ffmpeg"), "-loglevel", "quiet", "-re", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                                  "-f", "lavfi", "-i", "sine=frequency=440", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
                                  "-c:a", "aac", "-f", "mpegts", "pipe:1"], stdout=subprocess.PIPE)
            try:
                while chunk := p.stdout.read(65536):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                p.kill()
            return
        if u.path == f"/movie/{USER}/{PASS}/201.mp4":
            data = open(MOVIE, "rb").read()
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                a = int(a or 0); b = int(b) if b else len(data) - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                body = data[a:b + 1]
            else:
                self.send_response(200); body = data
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            # Slow, so a download can be watched and cancelled.
            for i in range(0, len(body), 32768):
                self.wfile.write(body[i:i + 32768]); time.sleep(0.05)
            return
        self.send_response(404); self.end_headers()
    def send(self, body, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
