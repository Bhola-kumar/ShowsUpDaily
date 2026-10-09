"""Google OAuth (web flow) + YouTube upload."""
import json
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import db

# Google may return scopes in a different order / with extras; don't fail on that.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class AuthError(Exception):
    pass


def base_url():
    return os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")


def redirect_uri():
    return base_url() + "/oauth/callback"


def configured():
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def _flow(state=None):
    cfg = {"web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri()],
    }}
    f = Flow.from_client_config(cfg, scopes=SCOPES, state=state, autogenerate_code_verifier=False)
    f.redirect_uri = redirect_uri()
    return f


def auth_url():
    url, state = _flow().authorization_url(access_type="offline", prompt="consent",
                                           include_granted_scopes="true")
    return url, state


def _save(creds):
    db.put("google_token", creds.to_json())


def finish(authorization_response, state):
    f = _flow(state=state)
    f.fetch_token(authorization_response=authorization_response)
    creds = f.credentials
    if not creds.refresh_token:
        raise AuthError("Google did not return a refresh token. Remove the app from "
                        "myaccount.google.com/permissions and connect again.")
    _save(creds)
    info = channel_info(creds)
    db.put("channel_title", info["title"])
    db.put("channel_id", info["id"])
    return info


def get_creds():
    raw = db.get("google_token")
    if not raw:
        raise AuthError("YouTube is not connected.")
    creds = Credentials.from_authorized_user_info(json.loads(raw))
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f"Google rejected the saved login ({e}). Reconnect YouTube.") from e
        _save(creds)
    return creds


def _yt(creds):
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_info(creds):
    r = _yt(creds).channels().list(part="snippet", mine=True).execute()
    items = r.get("items", [])
    if not items:
        raise AuthError("This Google account has no YouTube channel.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def disconnect():
    for k in ("google_token", "channel_title", "channel_id"):
        db.delete(k)


def upload(creds, path, title, description, tags, privacy, category="22"):
    body = {
        "snippet": {"title": title[:100], "description": description, "tags": tags, "categoryId": category},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True)
    req = _yt(creds).videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=3)
    return {"id": resp["id"], "privacy": resp.get("status", {}).get("privacyStatus", privacy)}
