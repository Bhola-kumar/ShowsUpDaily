"""Daily description + tags from the Gemini free tier (one request per post)."""
import json
import os
import re
import time

import requests


def _clean_tags(tags, limit=450):
    out, used, seen = [], 0, set()
    for t in tags if isinstance(tags, list) else []:
        t = re.sub(r"[<>#\"]", "", str(t)).strip()[:30]
        if len(t) < 2 or t.lower() in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if used + cost > limit:
            break
        seen.add(t.lower())
        out.append(t)
        used += cost
    return out


def _clean_text(s, limit=4800):
    return re.sub(r"[<>]", "", str(s or "")).strip()[:limit]


def fallback(title, notes):
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", f"{title} {notes}")]
    tags = _clean_tags(list(dict.fromkeys(w.lower() for w in words))[:12])
    desc = _clean_text((notes or title) + "\n\n" + " ".join("#" + t.replace(" ", "") for t in tags[:3]))
    return {"description": desc, "tags": tags}


def generate(title, notes, day_no, previous):
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    lang = os.getenv("CONTENT_LANGUAGE", "English")
    prev = "\n---\n".join(p[:600] for p in previous if p) or "(none yet)"
    prompt = f"""You write YouTube metadata for one video that the channel re-posts daily.
Video title: {title}
What the video is about: {notes or '(not provided)'}
Today is day {day_no} of posting this video.
Earlier descriptions (say the same truthful things in clearly different wording):
{prev}

Write in {lang}. Return ONLY JSON: {{"description": "...", "tags": ["..."]}}
Rules: description 80-180 words, natural and accurate to the title/notes, no invented claims, no links,
end with exactly 3 relevant hashtags. 10-15 tags, each under 30 characters, no # symbol."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"}}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=60)
            if r.status_code == 200:
                data = r.json()
                text = "".join(p.get("text", "") for p in
                               (data.get("candidates") or [{}])[0].get("content", {}).get("parts", []))
                text = re.sub(r"^\s*```(?:json)?|```\s*$", "", text).strip()
                parsed = json.loads(text)
                desc = _clean_text(parsed.get("description"))
                tags = _clean_tags(parsed.get("tags"))
                if not desc:
                    raise ValueError("empty description")
                return {"description": desc, "tags": tags}
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Gemini failed: {last}")
