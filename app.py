import hmac
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,  # noqa: E402
                   send_file, session, url_for)

from werkzeug.middleware.proxy_fix import ProxyFix
import db  # noqa: E402
import gemini  # noqa: E402
import youtube  # noqa: E402

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Kolkata"))
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
MAX_ATTEMPTS_PER_DAY = 3
RETRY_GAP_MIN = 30

db.init()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


def _secret():
    s = os.getenv("SECRET_KEY")
    if s:
        return s
    saved = db.get("flask_secret_key")
    if saved:
        return saved
    p = db.DATA / ".secret_key"
    if p.exists():
        key = p.read_text().strip()
    else:
        key = secrets.token_hex(32)
        try:
            p.write_text(key)
        except Exception:
            pass
    try:
        db.put("flask_secret_key", key)
    except Exception:
        pass
    return key


app.config.update(
    SECRET_KEY=_secret(),
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "512")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=youtube.base_url().startswith("https"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# ---------------------------------------------------------------- helpers
def now():
    return datetime.now(TZ)


def today():
    return now().date().isoformat()


def stamp():
    return now().strftime("%Y-%m-%d %H:%M")


def active_video():
    vid = db.get("active_video_id")
    return db.get_video(vid) if vid else None


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


@app.before_request
def _csrf():
    session.setdefault("csrf", secrets.token_hex(16))
    if request.method == "POST":
        tok = request.form.get("csrf", "")
        if not tok or not hmac.compare_digest(tok, session["csrf"]):
            abort(400, "Bad CSRF token, reload the page.")


@app.context_processor
def _inject():
    return {"csrf": session.get("csrf", "")}


# ---------------------------------------------------------------- posting logic
_run_lock = threading.Lock()


def _previous_descriptions(video_id, n=3):
    return db.previous_descriptions(video_id, n)


def run_post():
    """Generate metadata, upload the active video, update counters. Returns a short message."""
    if not _run_lock.acquire(blocking=False):
        return "Another post is already running."
    v = None
    try:
        v = active_video()
        if not v:
            return "No active video."
        path = db.ensure_video_file(v["filename"])
        if not path or not path.exists():
            raise RuntimeError("Video file is missing on the server (was the disk reset?). Upload it again.")
        day_no = (v["days_posted"] or 0) + 1
        notes_out = []
        notes_val = v["notes"] if "notes" in v and v["notes"] else ""
        try:
            meta = gemini.generate(v["title"], notes_val, day_no, _previous_descriptions(v["id"]))
        except Exception as e:  # post anyway with plain metadata
            meta = gemini.fallback(v["title"], notes_val)
            notes_out.append(f"Gemini unavailable, used plain description ({e})")
        creds = youtube.get_creds()
        privacy = db.get("privacy", "public")
        res = youtube.upload(creds, str(path), v["title"], meta["description"], meta["tags"], privacy,
                             os.getenv("YOUTUBE_CATEGORY_ID", "22"))
        if res["privacy"] != privacy:
            notes_out.append(f"YouTube set it to {res['privacy']} (asked for {privacy}). "
                             "Unaudited API projects are forced to private, see README.")
        db.increment_video_days(v["id"], today())
        db.create_post(
            video_id=v["id"],
            day_no=day_no,
            posted_at=stamp(),
            status="ok",
            youtube_id=res["id"],
            privacy=res["privacy"],
            message=" ".join(notes_out),
            description=meta["description"],
            tags=", ".join(meta["tags"])
        )
        db.put("last_post_date", today())
        return f"Posted day {day_no}: https://youtu.be/{res['id']}"
    except Exception as e:
        db.create_post(
            video_id=v["id"] if v else None,
            day_no=((v["days_posted"] or 0) + 1) if v else None,
            posted_at=stamp(),
            status="error",
            message=str(e)[:500]
        )
        return f"Failed: {e}"
    finally:
        _run_lock.release()


def tick():
    """Idempotent: posts once per day when the scheduled time has passed."""
    if db.get("enabled", "1") != "1" or not db.get("google_token") or not active_video():
        return "idle"
    n = now()
    if db.get("last_post_date") == today():
        return "already posted today"
    hh, mm = db.get("post_time", "09:00").split(":")
    if n < n.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0):
        return "not due yet"
    if db.get("attempt_date") == today():
        if int(db.get("attempt_count", "0")) >= MAX_ATTEMPTS_PER_DAY:
            return "max attempts reached today"
        if time.time() - float(db.get("last_attempt_ts", "0")) < RETRY_GAP_MIN * 60:
            return "waiting before retry"
        db.put("attempt_count", int(db.get("attempt_count", "0")) + 1)
    else:
        db.put("attempt_date", today())
        db.put("attempt_count", 1)
    db.put("last_attempt_ts", time.time())
    return run_post()


def _scheduler_loop():
    while True:
        try:
            tick()
        except Exception as e:  # never let the loop die
            print("tick error:", e, flush=True)
        time.sleep(60)


if os.getenv("DISABLE_SCHEDULER") != "1":
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()


# ---------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    pw = os.getenv("APP_PASSWORD", "")
    if not pw:
        return "Set APP_PASSWORD in your environment first.", 500
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), pw):
            session.permanent = True
            session["auth"] = True
            return redirect(url_for("index"))
        time.sleep(1)
        flash("Wrong password.", "err")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


@app.get("/terms-of-service")
def terms_of_service():
    return render_template("terms_of_service.html")


@app.get("/")
def home():
    return render_template("home.html")


# ---------------------------------------------------------------- dashboard
def _next_run_label():
    if db.get("enabled", "1") != "1":
        return "Paused"
    t = db.get("post_time", "09:00")
    if db.get("last_post_date") == today():
        return f"Tomorrow {t}"
    hh, mm = t.split(":")
    due = now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    return f"Today {t}" if now() < due else "Due now"


@app.get("/dashboard")
@login_required
def index():
    v = active_video()
    others = db.list_videos(exclude_id=v["id"] if v else None)
    posts = db.list_posts(limit=8)
    last_ok = db.last_successful_post()
    return render_template(
        "index.html", v=v, others=others, posts=posts,
        connected=bool(db.get("google_token")), oauth_ready=youtube.configured(),
        channel=db.get("channel_title"), post_time=db.get("post_time", "09:00"),
        enabled=db.get("enabled", "1") == "1", privacy=db.get("privacy", "public"),
        next_run=_next_run_label(), last_post=last_ok["posted_at"] if last_ok else None,
        tz=str(TZ), gemini_ready=bool(os.getenv("GEMINI_API_KEY")),
        running=_run_lock.locked(),
        supabase_ready=db.is_supabase())


@app.post("/settings")
@login_required
def settings():
    t = request.form.get("post_time", "09:00")
    try:
        datetime.strptime(t, "%H:%M")
    except ValueError:
        flash("Invalid time.", "err")
        return redirect(url_for("index"))
    privacy = request.form.get("privacy", "public")
    db.put("post_time", t)
    db.put("privacy", privacy if privacy in ("public", "unlisted", "private") else "public")
    db.put("enabled", "1" if request.form.get("enabled") else "0")
    flash("Schedule saved.", "ok")
    return redirect(url_for("index"))


@app.post("/run-now")
@login_required
def run_now():
    try:
        if not db.get("google_token"):
            flash("Connect YouTube first.", "err")
        elif not active_video():
            flash("Upload a video first.", "err")
        else:
            threading.Thread(target=run_post, daemon=True).start()
            flash("Posting started. Refresh in a minute to see the result.", "ok")
    except Exception as e:
        flash(f"Could not trigger posting: {e}", "err")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- videos
@app.post("/upload")
@login_required
def upload():
    f = request.files.get("video")
    title = request.form.get("title", "").strip()[:100]
    notes = request.form.get("notes", "").strip()[:1500]
    if not f or not f.filename:
        flash("Choose a video file.", "err")
        return redirect(url_for("index"))
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        flash("Unsupported file type. Use mp4, mov, m4v, mkv, webm or avi.", "err")
        return redirect(url_for("index"))
    if not title:
        flash("Add a title.", "err")
        return redirect(url_for("index"))
    name = uuid.uuid4().hex + ext
    path = db.VIDEOS / name
    f.save(path)
    try:
        db.save_video_file(name, path)
    except Exception as e:
        try:
            path.unlink()
        except Exception:
            pass
        flash(f"Upload failed: {e}", "err")
        return redirect(url_for("index"))
    vid = db.create_video(name, f.filename, title, notes, stamp(), path.stat().st_size)
    db.put("active_video_id", vid)
    flash("New video is active. Day counter restarted; it posts at the next scheduled time.", "ok")
    return redirect(url_for("index"))


@app.get("/video/<int:vid>")
@login_required
def video_file(vid):
    v = db.get_video(vid)
    if not v:
        abort(404)
    path = db.ensure_video_file(v["filename"])
    if not path or not path.exists():
        abort(404)
    return send_file(path, conditional=True)


@app.post("/video/<int:vid>/resume")
@login_required
def resume(vid):
    v = db.get_video(vid)
    if not v:
        abort(404)
    db.put("active_video_id", vid)
    flash(f"Resumed “{v['title']}” at day {v['days_posted']}.", "ok")
    return redirect(url_for("index"))


@app.post("/video/<int:vid>/delete")
@login_required
def delete_video(vid):
    v = db.get_video(vid)
    if not v or str(vid) == str(db.get("active_video_id")):
        flash("Can't delete the active video.", "err")
        return redirect(url_for("index"))
    db.delete_video_file(v["filename"])
    db.delete_video(vid)
    flash("Deleted.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- youtube connection
@app.get("/connect")
@login_required
def connect():
    if not youtube.configured():
        flash("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your environment first.", "err")
        return redirect(url_for("index"))
    url, state = youtube.auth_url()
    session["oauth_state"] = state
    return redirect(url)


@app.get("/oauth/callback")
@login_required
def oauth_callback():
    state = session.pop("oauth_state", None)
    if not state or request.args.get("state") != state:
        abort(400, "OAuth state mismatch")
    if request.args.get("error"):
        flash("Google sign-in was cancelled.", "err")
        return redirect(url_for("index"))
    try:
        info = youtube.finish(youtube.base_url() + "/oauth/callback?" + request.query_string.decode(), state)
        flash(f"Connected: {info['title']}", "ok")
    except Exception as e:
        flash(f"Could not connect: {e}", "err")
    return redirect(url_for("index"))


@app.post("/disconnect")
@login_required
def disconnect():
    youtube.disconnect()
    flash("YouTube disconnected.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- external trigger (free hosts that sleep)
@app.get("/cron/tick")
def cron_tick():
    key = os.getenv("CRON_KEY", "")
    if not key or not hmac.compare_digest(request.args.get("key", ""), key):
        abort(404)
    db.ping()
    threading.Thread(target=tick, daemon=True).start()
    return jsonify(ok=True)


@app.get("/healthz")
def healthz():
    db_ok = db.ping()
    return jsonify(
        status="ok" if db_ok else "db_warning",
        storage="supabase" if db.is_supabase() else "local"
    )


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
