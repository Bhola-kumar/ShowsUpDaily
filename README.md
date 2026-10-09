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
5. **Important:** on the consent screen, click **Publish app** (move it from "Testing" to "In production"). In Testing mode Google expires the login after 7 days and daily posting stops. You'll see an "unverified app" warning when signing in, which is fine for your own account.

## Deploy on a free host

Free hosts differ, and two things matter: the **disk must persist** (your video, the login token and the counter live in `DATA_DIR`) and the **app must be awake** at post time.

- **Always-on process with a persistent disk:** start with `gunicorn app:app --workers 1 --threads 4 --timeout 900` (see `Procfile`). Keep it at **one worker**, since the scheduler runs inside it. Point `DATA_DIR` at the persistent volume.
- **Hosts that sleep when idle** (many free web tiers): set `CRON_KEY` and have a free pinger such as cron-job.org call `https://YOUR-APP/cron/tick?key=YOUR_CRON_KEY` every 10–15 minutes. The ping wakes the app, and it posts once per day when the scheduled time has passed. Without a persistent disk on these hosts, a restart erases the uploaded video and login, so you'd have to re-upload and reconnect.
- **Hosts with only a daily scheduled task** (e.g. PythonAnywhere): schedule `python run_daily.py` and it will post if it's due. Check that the host allows outbound calls to Google's APIs on the free plan.

Set `BASE_URL` to the public https address and add it to the Google redirect URI list. Set `TZ_NAME` for your time zone.

## Behavior to know

- It posts once per day after the set time. If the app was down at that time, it posts when it comes back that day. Missed days are not made up.
- A failed upload is retried after 30 minutes, up to 3 attempts a day. Failures show in Activity.
- If Gemini is unavailable, the post still goes out with a plain description built from your title and notes.
- **Visibility:** YouTube locks videos uploaded through new, unaudited API projects to private, whatever you choose. The Activity log tells you when this happens. To post publicly through the API you need to pass Google's YouTube API compliance audit, or you can switch videos to public in Studio.
- **Quota:** the default allows about 6 uploads a day, so one daily post is well within it.
- Posting the identical video every day may be treated as repetitive content by YouTube, which can limit reach or monetization. Varied descriptions help only a little.

## Tests

`python tests/test_flow.py` runs an offline end-to-end check (counter, resume, retries, security) with Google and Gemini stubbed.
