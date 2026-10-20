"""Offline end-to-end test: Google and Gemini are stubbed. Run: python tests/test_flow.py"""
import io
import os
import re
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.update(DATA_DIR=tempfile.mkdtemp(), APP_PASSWORD="pw", DISABLE_SCHEDULER="1",
                  CRON_KEY="k", GEMINI_API_KEY="x", TZ_NAME="Asia/Kolkata",
                  SUPABASE_URL="", SUPABASE_KEY="")

import app as A  # noqa: E402
import db  # noqa: E402
import gemini  # noqa: E402
import youtube  # noqa: E402

calls = []
gemini.generate = lambda t, n, d, p: {"description": f"desc day {d} #a #b #c", "tags": ["alpha", "beta"]}
youtube.get_creds = lambda: object()
youtube.upload = lambda creds, path, title, desc, tags, privacy, cat="22": (
    calls.append((title, desc, tags, privacy)) or {"id": f"yt{len(calls)}", "privacy": "private"})

c = A.app.test_client()


def next_day():
    db.put("last_post_date", (A.now() - timedelta(days=1)).date().isoformat())
    db.delete("attempt_date")


def csrf(path="/"):
    html = c.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def post(path, **data):
    return c.post(path, data={"csrf": csrf("/login" if not data.pop("_authed", True) else "/"), **data},
                  follow_redirects=True)


# auth
assert c.get("/").status_code == 302
assert c.get("/privacy-policy").status_code == 200
assert "Google Gemini" in c.get("/privacy-policy").get_data(as_text=True)
assert c.get("/terms-of-service").status_code == 200
assert "Authorization and publishing" in c.get("/terms-of-service").get_data(as_text=True)
r = c.post("/login", data={"csrf": csrf("/login"), "password": "pw"}, follow_redirects=True)
assert "Daily Poster" in r.get_data(as_text=True)

# upload video 1
def upload(title):
    return c.post("/upload", data={"csrf": csrf(), "title": title, "notes": "about x",
                  "video": (io.BytesIO(b"\x00" * 2048), "a.mp4")}, content_type="multipart/form-data",
                  follow_redirects=True)
assert "counter restarted" in upload("Video One").get_data(as_text=True)
assert "Video One" in c.get("/").get_data(as_text=True)
assert c.get("/video/1").status_code == 200

# not connected -> tick idles
assert A.tick() == "idle"
db.put("google_token", "{}")
db.put("channel_title", "My Channel")
assert "My Channel" in c.get("/").get_data(as_text=True)

# schedule in the past so it is due; first tick posts
db.put("post_time", "00:00")
assert A.tick().startswith("Posted day 1"), A.tick()
assert db.one("SELECT days_posted FROM videos WHERE id=1")["days_posted"] == 1
assert A.tick() == "already posted today"
assert "Day 1" in c.get("/").get_data(as_text=True)
assert "forced to private" in c.get("/").get_data(as_text=True) or "YouTube set it to private" in c.get("/").get_data(as_text=True)

# simulate next day twice -> counter 3, descriptions vary by day
for expected in (2, 3):
    next_day()
    assert A.tick().startswith(f"Posted day {expected}")
assert calls[-1][1].startswith("desc day 3")

# new video restarts counter, old one preserved
upload("Video Two")
assert db.get("active_video_id") == "2"
next_day()
assert A.tick().startswith("Posted day 1")
html = post("/video/1/resume").get_data(as_text=True)
assert db.get("active_video_id") == "1" and "day 3" in html
next_day()
assert A.tick().startswith("Posted day 4")

# delete: active refused, inactive ok
post("/video/1/delete"); assert db.one("SELECT 1 FROM videos WHERE id=1")
post("/video/2/delete"); assert not db.one("SELECT 1 FROM videos WHERE id=2")

# failure handling: retry gap + max attempts
def boom(*a, **k):
    raise RuntimeError("quota")
youtube.upload = boom
next_day()
assert A.tick().startswith("Failed")
assert A.tick() == "waiting before retry"
db.put("last_attempt_ts", 0); assert A.tick().startswith("Failed")
db.put("last_attempt_ts", 0); assert A.tick().startswith("Failed")
db.put("last_attempt_ts", 0); assert A.tick() == "max attempts reached today"
assert "quota" in c.get("/").get_data(as_text=True)

# settings + csrf + cron
post("/settings", post_time="07:30", privacy="unlisted", enabled="on")
assert db.get("post_time") == "07:30" and db.get("privacy") == "unlisted"
assert c.post("/settings", data={"post_time": "07:30"}).status_code == 400
assert c.get("/cron/tick?key=bad").status_code == 404 and c.get("/cron/tick?key=k").status_code == 200

# gemini helpers
assert gemini._clean_tags(["#Foo", "foo", "a", "<x>y", "z" * 50]) == ["Foo", "xy", "z" * 30]
fb = gemini.fallback("Cool Robot Dance", "a robot dancing")
assert fb["tags"] and "#" in fb["description"]
print("ALL OK")
