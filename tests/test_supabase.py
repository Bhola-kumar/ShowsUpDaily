"""Test Supabase persistence and storage flows with mocked Supabase client."""
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set env before importing app/db
os.environ.update(
    DATA_DIR=tempfile.mkdtemp(),
    APP_PASSWORD="pw",
    SECRET_KEY="test_secret_key_1234567890",
    DISABLE_SCHEDULER="1",
    CRON_KEY="k",
    GEMINI_API_KEY="x",
    TZ_NAME="Asia/Kolkata",
    SUPABASE_URL="https://mock-project.supabase.co",
    SUPABASE_KEY="mock-service-role-key",
    SUPABASE_BUCKET="videos",
)

# Mock supabase client before db loads it
mock_storage_bucket = MagicMock()
mock_storage_client = MagicMock()
mock_storage_client.from_.return_value = mock_storage_bucket

mock_settings_table = MagicMock()
mock_videos_table = MagicMock()
mock_posts_table = MagicMock()

def mock_table(table_name):
    if table_name == "settings":
        return mock_settings_table
    elif table_name == "videos":
        return mock_videos_table
    elif table_name == "posts":
        return mock_posts_table
    return MagicMock()

mock_client = MagicMock()
mock_client.storage = mock_storage_client
mock_client.table.side_effect = mock_table

with patch("supabase.create_client", return_value=mock_client):
    import db
    import app as A

# Test 1: Verify Supabase mode is active
assert db.is_supabase() is True, "Supabase should be detected and active"

# Test 2: Verify settings get, put, delete
mock_settings_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"value": "123"}])
val = db.get("active_video_id")
assert val == "123", f"Expected '123', got {val}"

db.put("post_time", "10:00")
mock_settings_table.upsert.assert_called_with({"key": "post_time", "value": "10:00"})

db.delete("post_time")
mock_settings_table.delete.return_value.eq.assert_called_with("key", "post_time")

# Test 3: Verify video operations
mock_videos_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": 42}])
vid = db.create_video("test.mp4", "orig.mp4", "Title", "Notes", "2026-10-11", 1024)
assert vid == 42, f"Expected video ID 42, got {vid}"

mock_videos_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
    data=[{"id": 42, "filename": "test.mp4", "title": "Title", "days_posted": 2}]
)
v = db.get_video(42)
assert v["id"] == 42 and v["title"] == "Title"

# Test 4: Verify storage - save video
sample_video_path = db.VIDEOS / "test.mp4"
sample_video_path.write_bytes(b"dummy video data")
db.save_video_file("test.mp4", sample_video_path)
mock_storage_bucket.upload.assert_called()

# Test 5: Verify ephemeral restart - ensure_video_file downloads missing file from Supabase Storage
sample_video_path.unlink()
assert not sample_video_path.exists()
mock_storage_bucket.download.return_value = b"restored video data"

restored_path = db.ensure_video_file("test.mp4")
assert restored_path is not None
assert restored_path.exists()
assert restored_path.read_bytes() == b"restored video data"

# Test 6: Verify healthz endpoint reports Supabase storage
c = A.app.test_client()
res = c.get("/healthz")
assert res.status_code == 200
data = res.get_json()
assert data["status"] == "ok"
assert data["storage"] == "supabase"

print("ALL SUPABASE TESTS PASSED!")
