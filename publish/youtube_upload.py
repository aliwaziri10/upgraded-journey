"""
TechPulse - Publish Stage (Supabase-native, matched by row id)
Pulls the next Supabase video_pipeline row with status='video_generated',
downloads its video_url, uploads it to YouTube, and updates that SAME
row (by id) with status="published", youtube_video_id, youtube_url, and
published_at.

FIXED (2026-08-04): the old version matched rows back together by
title + source TEXT after the fact, which is exactly the kind of match
that silently breaks on whitespace/truncation differences - the likely
cause of the previous publish/tracking discrepancy (YOUTUBE_READY=true
for weeks with zero published rows despite at least one real upload).
Now that every row has had a single stable id since the script stage
created it, there is no text matching involved at all - this stage reads
the row's own id and writes back to that exact id, full stop.

RETRY LOGIC (2026-08-05): previously any failure (download error, YouTube
API error) permanently marked the row status='failed' with no automatic
retry. Now failures leave the row's status untouched (it's already
'video_generated' with youtube_video_id still null, so the next run's
query naturally picks it up again) and just increment retry_count, up
to RETRY_LIMIT times, before finally marking it permanently failed so a
human notices.

FIXED (2026-08-05): YouTube rejects any video title over 100 characters
with "invalid or empty video title" - a confusing error for what's
actually a length problem, not an empty-string problem. The headline
"Google nixes its Earth AI feature one day after launch, amid criticism
it would spread misinformation" is 101 characters and hit exactly this.
Titles are now truncated to 100 characters at the last whole word before
being sent to YouTube - the full original headline is unaffected anywhere
else (Supabase title column, narration, etc.), only what's sent to the
YouTube API is shortened.

STORAGE CLEANUP (2026-09-10): once a row is confirmed published (mark_published
has succeeded), its source video file no longer needs to sit in Supabase
Storage. delete_video_from_storage() parses the bucket/object path out of
the row's own video_url and issues a Storage API delete - best-effort,
never raises, same non-fatal contract as every other cleanup step in this
pipeline. Placed after mark_published so a cleanup failure can never be
mistaken for a publish failure (the row already reflects reality by the
time this could fail).
"""
import os
import re
from datetime import datetime, timezone

import requests
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIENT_ID = os.environ["YT_CLIENT_ID"]
CLIENT_SECRET = os.environ["YT_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["YT_REFRESH_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
}

TMP_DIR = "publish/tmp"

RETRY_LIMIT = 3
YOUTUBE_TITLE_MAX_CHARS = 100

# Matches both public and signed Supabase Storage object URLs, e.g.
# https://<project>.supabase.co/storage/v1/object/public/<bucket>/<path>
# https://<project>.supabase.co/storage/v1/object/sign/<bucket>/<path>?token=...
STORAGE_URL_PATTERN = re.compile(r"/storage/v1/object/(?:public|sign|authenticated)/([^/]+)/([^?]+)")


def get_next_video_generated_row():
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/video_pipeline"
        f"?status=eq.video_generated&youtube_video_id=is.null"
        f"&order=created_at.asc&limit=1",
        headers=HEADERS, timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def mark_failed(row_id, reason, retry_count):
    if retry_count < RETRY_LIMIT:
        next_count = retry_count + 1
        print(f"  Row {row_id} failed (attempt {next_count}/{RETRY_LIMIT}): {reason}. Will retry next run.")
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/video_pipeline?id=eq.{row_id}",
            headers=HEADERS, json={"retry_count": next_count}, timeout=30,
        )
    else:
        print(f"  Row {row_id} failed permanently after {RETRY_LIMIT} attempts: {reason}")
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/video_pipeline?id=eq.{row_id}",
            headers=HEADERS, json={"status": "failed"}, timeout=30,
        )


def download(url, out_path):
    resp = requests.get(url, headers={"User-Agent": "TechPulse/1.0"}, timeout=300)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)


def delete_video_from_storage(video_url):
    """STORAGE CLEANUP (2026-09-10): parses the bucket + object path out of
    a Supabase Storage URL and deletes it via the Storage REST API. Never
    raises - a failed cleanup should never affect publish status, which is
    already recorded in Supabase by the time this runs. Silently no-ops if
    video_url isn't a recognizable Supabase Storage URL (e.g. already
    migrated to a different host)."""
    if not video_url:
        return
    match = STORAGE_URL_PATTERN.search(video_url)
    if not match:
        print(f"  Storage cleanup skipped: could not parse a Supabase Storage bucket/path from {video_url}")
        return
    bucket, object_path = match.group(1), match.group(2)
    try:
        resp = requests.delete(
            f"{SUPABASE_URL}/storage/v1/object/{bucket}/{object_path}",
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            print(f"  WARNING: storage cleanup failed for {bucket}/{object_path} ({resp.status_code}): {resp.text[:300]}")
        else:
            print(f"  Deleted source video from Supabase Storage: {bucket}/{object_path}")
    except requests.RequestException as e:
        print(f"  WARNING: storage cleanup failed for {bucket}/{object_path} (network error): {e}")


def get_youtube_client():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    creds.refresh(google.auth.transport.requests.Request())
    return build("youtube", "v3", credentials=creds)


def build_youtube_title(title):
    """YouTube hard-rejects titles over 100 chars. Truncate at the last
    whole word within the limit rather than cutting mid-word."""
    if len(title) <= YOUTUBE_TITLE_MAX_CHARS:
        return title
    truncated = title[:YOUTUBE_TITLE_MAX_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(" ,.-") 


def upload_to_youtube(youtube, title, description, local_path):
    body = {
        "snippet": {
            "title": build_youtube_title(title),
            "description": (description or "")[:4900],
            "categoryId": "28",
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(local_path, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    return response["id"]


def mark_published(row_id, youtube_video_id):
    youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/video_pipeline?id=eq.{row_id}",
        headers=HEADERS,
        json={
            "status": "published",
            "youtube_video_id": youtube_video_id,
            "youtube_url": youtube_url,
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        print(f"  WARNING: could not write publish status back to Supabase for row {row_id} "
              f"({resp.status_code}): {resp.text} - youtube_video_id={youtube_video_id} "
              f"(the YouTube upload itself still succeeded).")
    else:
        print(f"  Row {row_id} marked published in Supabase.")


def main():
    row = get_next_video_generated_row()
    if not row:
        print("No 'video_generated' rows ready to publish. Nothing to do.")
        return

    row_id = row["id"]
    retry_count = row.get("retry_count") or 0
    title = row.get("title", "untitled")
    narration_text = row.get("script", "")
    video_url = row.get("video_url")

    if not video_url:
        mark_failed(row_id, "video_generated row has no video_url", retry_count)
        return

    os.makedirs(TMP_DIR, exist_ok=True)
    local_path = f"{TMP_DIR}/{row_id}.mp4"

    try:
        print(f"Downloading final video for '{title}'...")
        download(video_url, local_path)

        youtube = get_youtube_client()
        print(f"Uploading '{title}' to YouTube...")
        youtube_video_id = upload_to_youtube(youtube, title, narration_text, local_path)
        print(f"Uploaded: {title} -> {youtube_video_id}")

        mark_published(row_id, youtube_video_id)

        # STORAGE CLEANUP (2026-09-10): only runs after mark_published, and
        # never raises - see delete_video_from_storage's docstring.
        delete_video_from_storage(video_url)

    except Exception as e:
        print(f"ERROR publishing '{title}' (row {row_id}): {e}")
        mark_failed(row_id, str(e), retry_count)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


if __name__ == "__main__":
    main()
