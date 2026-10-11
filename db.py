import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA = Path(os.getenv("DATA_DIR", "data")).resolve()
VIDEOS = DATA / "videos"
DATA.mkdir(parents=True, exist_ok=True)
VIDEOS.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "app.db"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "videos").strip()

_sb = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Warning: Failed to initialize Supabase client ({e}). Falling back to SQLite.", flush=True)
        _sb = None


def is_supabase() -> bool:
    return _sb is not None


# ---------------------------------------------------------------- SQLite helpers
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- init & health
def init():
    if is_supabase():
        # Ensure bucket exists
        try:
            _sb.storage.get_bucket(SUPABASE_BUCKET)
        except Exception:
            try:
                _sb.storage.create_bucket(SUPABASE_BUCKET, options={"public": False})
            except Exception as e:
                print(f"Note: Could not auto-create bucket '{SUPABASE_BUCKET}' ({e}). Ensure it exists in Supabase Dashboard.", flush=True)
        # Test table connectivity
        try:
            _sb.table("settings").select("key").limit(1).execute()
        except Exception as e:
            print(f"Warning: Supabase table check failed: {e}. Run supabase_schema.sql in Supabase SQL Editor.", flush=True)
    else:
        with _conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_name TEXT,
                title TEXT NOT NULL,
                notes TEXT DEFAULT '',
                uploaded_at TEXT,
                size_bytes INTEGER DEFAULT 0,
                days_posted INTEGER DEFAULT 0,
                first_post_date TEXT
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER,
                day_no INTEGER,
                posted_at TEXT,
                status TEXT,
                youtube_id TEXT,
                privacy TEXT,
                message TEXT,
                description TEXT,
                tags TEXT
            );
            """)


def ping() -> bool:
    """Performs a lightweight query to verify connectivity and keep Supabase free tier active."""
    if is_supabase():
        try:
            _sb.table("settings").select("key").limit(1).execute()
            return True
        except Exception as e:
            print(f"Supabase ping failed: {e}", flush=True)
            return False
    else:
        try:
            with _conn() as c:
                c.execute("SELECT 1").fetchall()
            return True
        except Exception:
            return False


# ---------------------------------------------------------------- settings
def get(key, default=None):
    if is_supabase():
        try:
            res = _sb.table("settings").select("value").eq("key", str(key)).execute()
            return res.data[0]["value"] if res.data else default
        except Exception as e:
            print(f"Supabase get error for {key}: {e}", flush=True)
            return default
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def put(key, value):
    val_str = None if value is None else str(value)
    if is_supabase():
        try:
            _sb.table("settings").upsert({"key": str(key), "value": val_str}).execute()
            return
        except Exception as e:
            print(f"Supabase put error for {key}: {e}", flush=True)
            raise
    x("INSERT INTO settings(key,value) VALUES(?,?) "
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, val_str))


def delete(key):
    if is_supabase():
        try:
            _sb.table("settings").delete().eq("key", str(key)).execute()
            return
        except Exception as e:
            print(f"Supabase delete error for {key}: {e}", flush=True)
            raise
    x("DELETE FROM settings WHERE key=?", (key,))


# ---------------------------------------------------------------- videos
def _to_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return val


def create_video(filename: str, original_name: str, title: str, notes: str, uploaded_at: str, size_bytes: int) -> int:
    if is_supabase():
        row = {
            "filename": filename,
            "original_name": original_name,
            "title": title,
            "notes": notes,
            "uploaded_at": uploaded_at,
            "size_bytes": size_bytes,
            "days_posted": 0,
        }
        res = _sb.table("videos").insert(row).execute()
        return res.data[0]["id"]
    return x("INSERT INTO videos(filename,original_name,title,notes,uploaded_at,size_bytes) VALUES(?,?,?,?,?,?)",
             (filename, original_name, title, notes, uploaded_at, size_bytes))


def get_video(vid) -> Optional[Dict[str, Any]]:
    if not vid:
        return None
    if is_supabase():
        res = _sb.table("videos").select("*").eq("id", _to_int(vid)).execute()
        return res.data[0] if res.data else None
    row = one("SELECT * FROM videos WHERE id=?", (vid,))
    return dict(row) if row else None


def list_videos(exclude_id=None) -> List[Dict[str, Any]]:
    if is_supabase():
        q_builder = _sb.table("videos").select("*").order("id", desc=True)
        if exclude_id is not None:
            q_builder = q_builder.neq("id", _to_int(exclude_id))
        res = q_builder.execute()
        return res.data
    if exclude_id is not None:
        rows = q("SELECT * FROM videos WHERE id != ? ORDER BY id DESC", (exclude_id,))
    else:
        rows = q("SELECT * FROM videos ORDER BY id DESC")
    return [dict(r) for r in rows]


def increment_video_days(vid, today_str):
    if is_supabase():
        v = get_video(vid)
        if v:
            new_days = (v.get("days_posted") or 0) + 1
            first_date = v.get("first_post_date") or today_str
            _sb.table("videos").update({
                "days_posted": new_days,
                "first_post_date": first_date
            }).eq("id", _to_int(vid)).execute()
        return
    x("UPDATE videos SET days_posted=days_posted+1, first_post_date=COALESCE(first_post_date,?) WHERE id=?",
      (today_str, vid))


def delete_video(vid):
    if is_supabase():
        _sb.table("videos").delete().eq("id", _to_int(vid)).execute()
        return
    x("DELETE FROM videos WHERE id=?", (vid,))


# ---------------------------------------------------------------- posts
def create_post(video_id, day_no, posted_at, status, youtube_id="", privacy="",
                message="", description="", tags=""):
    if is_supabase():
        row = {
            "video_id": _to_int(video_id) if video_id else None,
            "day_no": day_no,
            "posted_at": posted_at,
            "status": status,
            "youtube_id": youtube_id,
            "privacy": privacy,
            "message": message,
            "description": description,
            "tags": tags,
        }
        res = _sb.table("posts").insert(row).execute()
        return res.data[0]["id"] if res.data else None
    return x("INSERT INTO posts(video_id,day_no,posted_at,status,youtube_id,privacy,message,description,tags) "
             "VALUES(?,?,?,?,?,?,?,?,?)",
             (video_id, day_no, posted_at, status, youtube_id, privacy, message, description, tags))


def list_posts(limit=8) -> List[Dict[str, Any]]:
    if is_supabase():
        res = _sb.table("posts").select("*").order("id", desc=True).limit(limit).execute()
        return res.data
    return [dict(r) for r in q("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,))]


def last_successful_post() -> Optional[Dict[str, Any]]:
    if is_supabase():
        res = _sb.table("posts").select("posted_at").eq("status", "ok").order("id", desc=True).limit(1).execute()
        return res.data[0] if res.data else None
    row = one("SELECT posted_at FROM posts WHERE status='ok' ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def previous_descriptions(video_id, limit=3) -> List[str]:
    if is_supabase():
        res = _sb.table("posts").select("description").eq("video_id", _to_int(video_id)).eq("status", "ok").order("id", desc=True).limit(limit).execute()
        return [r["description"] for r in res.data if r.get("description")]
    rows = q("SELECT description FROM posts WHERE video_id=? AND status='ok' ORDER BY id DESC LIMIT ?",
             (video_id, limit))
    return [r["description"] for r in rows]


# ---------------------------------------------------------------- storage (files)
def save_video_file(filename: str, local_path: Path):
    if not is_supabase():
        return
    ext = local_path.suffix.lower()
    types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }
    content_type = types.get(ext, "application/octet-stream")
    try:
        with open(local_path, "rb") as f:
            _sb.storage.from_(SUPABASE_BUCKET).upload(
                path=filename,
                file=f,
                file_options={"upsert": "true", "content-type": content_type}
            )
    except Exception as e:
        err_msg = str(e)
        if "413" in err_msg or "maximum allowed size" in err_msg.lower():
            raise RuntimeError("Video exceeds Supabase Free Tier file size limit (50 MB). Upload a smaller video or upgrade your Supabase plan.") from e
        raise RuntimeError(f"Failed to upload video to Supabase Storage: {e}") from e


def ensure_video_file(filename: str) -> Optional[Path]:
    """Ensures the video file exists locally. If running on an ephemeral host after restart,
    it automatically downloads the file from Supabase Storage."""
    local_path = VIDEOS / filename
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path
    if is_supabase():
        try:
            VIDEOS.mkdir(parents=True, exist_ok=True)
            data = _sb.storage.from_(SUPABASE_BUCKET).download(filename)
            local_path.write_bytes(data)
            return local_path
        except Exception as e:
            print(f"Failed to fetch video {filename} from Supabase: {e}", flush=True)
            return None
    return local_path if local_path.exists() else None


def delete_video_file(filename: str):
    local_path = VIDEOS / filename
    try:
        if local_path.exists():
            local_path.unlink()
    except Exception:
        pass
    if is_supabase():
        try:
            _sb.storage.from_(SUPABASE_BUCKET).remove([filename])
        except Exception as e:
            print(f"Failed to delete video from Supabase storage: {e}", flush=True)


# ---------------------------------------------------------------- backward compatibility
def q(sql, args=()):
    with _conn() as c:
        return c.execute(sql, args).fetchall()


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    c = _conn()
    try:
        with c:
            cur = c.execute(sql, args)
            return cur.lastrowid
    finally:
        c.close()
