import os
import sqlite3
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", "data")).resolve()
VIDEOS = DATA / "videos"
DATA.mkdir(parents=True, exist_ok=True)
VIDEOS.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "app.db"


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


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


def init():
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


def get(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def put(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) "
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, None if value is None else str(value)))


def delete(key):
    x("DELETE FROM settings WHERE key=?", (key,))
