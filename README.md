# Daily Poster

A small Flask app that re-posts one video to your YouTube channel every day. Each day Gemini writes a fresh description and tags.

- Upload a video, see the preview, and the day counter starts at 0.
- Upload a different video and the counter restarts. Older videos are kept, and **Resume** brings one back with its old count.
- Connect your channel with Google sign-in. The dashboard shows the channel name, the streak (Day N since date), the next post time and the last post.
- Set the daily time, visibility, and pause or enable posting.
- Everything is protected by a password (`APP_PASSWORD`), because the app controls your channel.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill it in
OAUTHLIB_INSECURE_TRANSPORT=1 python app.py   # http://localhost:5000
```

## Get the keys

**Gemini (free):** aistudio.google.com → create API key → `GEMINI_API_KEY`. One request per post, so the free tier is plenty.

**Google OAuth (to connect your channel):**
1. console.cloud.google.com → new project → enable **YouTube Data API v3**.
2. OAuth consent screen: User type External, add your Google account as a test user.
3. Credentials → Create OAuth client ID → **Web application**. Add the authorized redirect URI `BASE_URL/oauth/callback` (for local: `http://localhost:5000/oauth/callback`).
4. Copy the client ID and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. Before publishing, deploy the app at a stable public HTTPS URL, set `BASE_URL` to that URL, and replace the contact placeholders in the legal pages with a real contact address. The public pages are `BASE_URL/privacy-policy` and `BASE_URL/terms-of-service`; they are accessible without signing in. Add these URLs to your OAuth consent-screen app details, along with the app homepage and any authorized domains Google requires.
6. **Important:** on the consent screen, click **Publish app** (move it from "Testing" to "In production") when you're ready. In Testing mode Google expires the login after 7 days and daily posting stops. You'll see an "unverified app" warning when signing in, which is fine for your own account. Public release and verification requirements depend on the permissions and Google's current review process; these pages alone do not complete that process.

## Deploy on a free host & Persistent Storage

Free hosts (like Render, Railway, Heroku, etc.) usually wipe their local disk on every restart and new deployment. To ensure your **uploaded videos, YouTube tokens, streak counts, and settings are never lost**, you can connect a free Supabase instance.

### Option A: Persistent Cloud Storage with Supabase (Recommended)

1. Create a free project at [supabase.com](https://supabase.com).
2. Go to **SQL Editor** -> **New Query**, paste the contents of `supabase_schema.sql`, and click **Run**.
3. In your host's environment variables (or `.env`), set:
   - `SUPABASE_URL`: `https://YOUR-PROJECT-REF.supabase.co`
   - `SUPABASE_KEY`: Your project's `service_role` secret key (**Project Settings** -> **API** -> **Project API keys** -> `service_role`).
   - `SUPABASE_BUCKET`: `videos` (created automatically by the SQL script).
4. That's it! Your video files are stored in Supabase Storage, and all database state lives in Supabase PostgreSQL. On every redeploy or container restart, the app automatically restores files from Supabase.

#### Supabase Free Tier Notes:
- **Inactivity / Auto-pause:** Supabase free projects pause if they receive no database queries for 7 days. Because Daily Poster runs every day (and any ping to `/healthz` or `/cron/tick` performs a database keep-alive check), your project **stays active automatically**.
- **File size limit:** Supabase Free tier allows up to **50 MB per upload** (1 GB total). Perfect for daily YouTube Shorts, reels, and clips.

### Option B: Local Disk (Only if your host provides persistent volumes)

If your host supports persistent storage volumes (e.g. Render Persistent Disk, Railway Volume), point `DATA_DIR` to your mounted volume and leave `SUPABASE_URL` blank.

### Keeping the app awake

- **Always-on process:** start with `gunicorn app:app --workers 1 --threads 4 --timeout 900` (see `Procfile`). Keep it at **one worker**, since the scheduler runs inside it.
- **Hosts that sleep when idle** (free web tiers): set `CRON_KEY` and have a free pinger such as [cron-job.org](https://cron-job.org) or UptimeRobot call `https://YOUR-APP/cron/tick?key=YOUR_CRON_KEY` every 10–15 minutes. The ping wakes the app, resets Supabase's inactivity timer, and posts once per day when the scheduled time has passed.
- **Hosts with only a daily scheduled task** (e.g. PythonAnywhere): schedule `python run_daily.py`.

Set `BASE_URL` to your public https address and add it to the Google redirect URI list (`BASE_URL/oauth/callback`). Set `TZ_NAME` for your time zone.

## Behavior to know

- It posts once per day after the set time. If the app was down at that time, it posts when it comes back that day. Missed days are not made up.
- A failed upload is retried after 30 minutes, up to 3 attempts a day. Failures show in Activity.
- If Gemini is unavailable, the post still goes out with a plain description built from your title and notes.
- **Visibility:** YouTube locks videos uploaded through new, unaudited API projects to private, whatever you choose. The Activity log tells you when this happens. To post publicly through the API you need to pass Google's YouTube API compliance audit, or you can switch videos to public in Studio.
- **Quota:** the default allows about 6 uploads a day, so one daily post is well within it.
- Posting the identical video every day may be treated as repetitive content by YouTube, which can limit reach or monetization. Varied descriptions help only a little.

## Tests

`python tests/test_flow.py` runs an offline end-to-end check (counter, resume, retries, security) with Google and Gemini stubbed.
