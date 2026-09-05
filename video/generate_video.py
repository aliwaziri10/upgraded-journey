"""
TechPulse - Video Generation Stage (LONG-FORM, RESUMABLE)
Rewritten 2026-08-05 to fix a real, confirmed production failure: the
previous version rendered ALL shots for a video (up to 63+ on a long-form
episode) sequentially inside a single pipeline.yml job, but Agnes AI
allows only 1 request/minute. That job can never finish a long-form
video's shots in one run, and every retry hits the identical wall - this
is exactly what happened to row 77b60f3d ("Bending Spoons to buy Airtable
for $1.28B"): it exhausted all 3 retries and was marked permanently
'failed' without ever getting past shot 1. That row has been reset back
to 'narrated' with shot_retry_count=0 so this version can pick it back up.

NEW DESIGN: this script does ONE shot per invocation, same resumable
pattern proven in Marius:
  1. Find the oldest 'narrated' row (or a row already mid-render, tracked
     via video_urls already having some entries).
  2. Render exactly the next not-yet-done shot (index = len(video_urls)).
  3. Upload that one clip, append its URL to video_urls, save.
  4. If that was the last shot, flip status to 'video_complete' (with
     the full video_urls array assemble.py already expects - unchanged).
  5. Exit. The next cron tick renders the next shot - whether that's
     shot 2 of this same video, or shot 1 of the next one once this one
     is done. A single Agnes rate-limit failure now costs one shot's
     worth of delay, not the whole video's retry budget.

Frame-count fix (num_frames must be 8n+1) and Agnes endpoints are carried
over unchanged from the 2026-08-05 schema-fix version of this file.

RELIABILITY FIX (2026-08-06): ported three fixes proven in production on
Marius's video_generation.py, none of which this file had yet:
1. RETRY-SAFE submit: submit_agnes_task now retries transient backend
   errors (429 rate limit, 500/502/503/504 server-side) with backoff
   before giving up, instead of letting a single flaky response burn one
   of only 3 total per-shot retries. This matters more here than in
   Marius specifically because Agnes's documented 1 req/min ceiling for
   this account means a 429 is an expected, routine event, not a rare
   edge case - it should never cost real retry budget.
2. POLL TIMEOUT: raised from 240s (4 min) to 900s (15 min).
   Marius's own production logs show real Agnes generations taking
   14-38 minutes under free-tier load; a 4-minute poll timeout was very
   likely aborting shots that were still legitimately rendering, forcing
   a full resubmission (and burning another shot_retry_count tick) for
   work that would have finished on its own.
3. CONTENT-POLICY-AWARE: submit_agnes_task now raises a distinct
   ContentPolicyRejection instead of a generic RuntimeError, and main()
   marks that row permanently 'failed' immediately (not after 3 retries)
   with the offending prompt logged - a content-policy rejection will
   never succeed on retry, so spending the per-shot retry budget on it
   only delays finding out.
4. CLIP VERIFICATION ON RESUME: before rendering the next shot, HEAD
   -checks every already-recorded video_urls entry once. A clip whose
   URL has gone stale/expired is silently dropped and re-rendered,
   instead of being built into the final assembly as a broken link -
   same fix Marius already has.

New Supabase column added for this: shot_retry_count (integer, default 0)
on video_pipeline - tracks retries for the CURRENT shot only, separate
from any whole-video retry_count used elsewhere.

MULTI-SHOT-PER-RUN FIX (2026-08-09): confirmed via live Actions log that
this was rendering exactly 1 shot per 5-minute cron tick, so a 63-shot
long-form video took ~5 hours of real time even though Agnes itself was
responding promptly - almost all of that time was the job spinning up,
doing one shot, and exiting, then sitting idle until the next cron tick.
main() now loops rendering shots within a SINGLE run instead of exiting
after one, pacing submissions AGNES_MIN_SECONDS_BETWEEN_SUBMITS (65s -
just over Agnes's documented 1 req/min cap for this account, matching
the existing rate limit exactly rather than batching shots the way
Marius does, which would 429-storm this account) apart. A run stops
looping when: the video (or all currently-ready videos) reaches
video_complete, MAX_SHOTS_PER_RUN is hit, or elapsed time approaches
RUN_TIME_BUDGET_SECONDS (leaving headroom under the job's 300-minute
timeout so a run always exits cleanly rather than getting killed
mid-shot). This does not change per-shot logic, retry behavior, or the
resumable one-shot-of-progress-per-save pattern at all - only wraps it
in a loop instead of running it once per process invocation.

FIX 1 + FIX 2 (2026-09-06): ported prompt-safety guards from Marius -
QUALITY_GUARD (waxy/plastic AI skin) and CROWD_ANATOMY_SAFETY_GUARD
(cloned/identical crowd faces), see video/prompt_guards.py for the guard
text and rationale. Previously this file sent Gemini's raw
scene_description/visual_description straight to Agnes with no safety
guard text appended at all - both guards are now appended to every
shot's prompt in render_one_shot(), right before submission.
"""
import json
import os
import time
import urllib.request
import urllib.error

from prompt_guards import QUALITY_GUARD, CROWD_ANATOMY_SAFETY_GUARD

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
AGNES_API_KEY = os.environ["AGNES_API_KEY"]

AGNES_SUBMIT_URL = "https://apihub.agnes-ai.com/v1/videos"
AGNES_POLL_URL = "https://apihub.agnes-ai.com/agnesapi"
AGNES_MODEL = "agnes-video-v2.0"
AGNES_HEADERS = {
    "Authorization": f"Bearer {AGNES_API_KEY}",
    "Content-Type": "application/json",
}

WIDTH, HEIGHT = 1920, 1080  # RESOLUTION UPGRADE (2026-08-29): Agnes supports up to 1080p, was requesting the 720p tier for no reason
FRAME_RATE = 24

# RELIABILITY FIX (2026-08-06): 240s (4 min) was frequently shorter than
# real Agnes render time under free-tier load (confirmed 14-38 min in
# Marius's production logs), causing shots to be abandoned mid-render.
POLL_MAX_WAIT = 900
POLL_INTERVAL = 10

# RELIABILITY FIX (2026-08-06): ported from Marius - retry transient
# backend errors instead of burning per-shot retry budget on them. Agnes's
# documented 1 req/min ceiling for this account means a 429 here is
# routine, not exceptional.
AGNES_RETRYABLE_CODES = {429, 500, 502, 503, 504}
AGNES_MAX_RETRIES = 4

VIDEO_CLIPS_BUCKET = "video_clips"
RETRY_LIMIT = 3  # per-SHOT retries now, not per-video

CLIP_VERIFY_TIMEOUT = 15

# MULTI-SHOT-PER-RUN FIX (2026-08-09): see file header. 65s keeps every
# Agnes submission just over 1 per minute, matching this account's
# documented cap - intentionally NOT batching multiple shots close
# together the way Marius does, since Marius's account has no such cap.
AGNES_MIN_SECONDS_BETWEEN_SUBMITS = 65
MAX_SHOTS_PER_RUN = 30
# Job timeout is 300 minutes; stop starting new shots once we're within
# this many seconds of that, so a run always exits cleanly (progress
# already saved after every shot) instead of getting hard-killed by
# GitHub mid-render.
RUN_TIME_BUDGET_SECONDS = 260 * 60


class ContentPolicyRejection(Exception):
    pass


def _supabase_request(method, path, body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw.strip() else None


def get_next_row_to_render():
    """Oldest 'narrated' row (whether fresh or already partway through
    rendering - progress is tracked via how many entries video_urls has,
    not via a separate status)."""
    rows = _supabase_request(
        "GET",
        "video_pipeline?status=eq.narrated&order=created_at.asc&limit=1&select=*",
    )
    return rows[0] if rows else None


def frames_for_duration(duration_seconds, frame_rate=FRAME_RATE):
    """Round to the nearest frame count Agnes accepts (must be 8*n + 1)."""
    raw = duration_seconds * frame_rate
    n = max(round((raw - 1) / 8), 1)
    return 8 * n + 1


def submit_agnes_task(prompt, duration_seconds):
    """RELIABILITY FIX (2026-08-06): retries 429/5xx with backoff instead
    of surfacing the first transient error straight to main(), where it
    would burn one of only RETRY_LIMIT=3 total per-shot attempts on
    something that had nothing to do with the prompt itself. A genuine
    content-policy rejection (400) is raised immediately as
    ContentPolicyRejection since retrying that can never succeed."""
    body = json.dumps({
        "model": AGNES_MODEL,
        "prompt": prompt,
        "height": HEIGHT,
        "width": WIDTH,
        "num_frames": frames_for_duration(duration_seconds),
        "frame_rate": FRAME_RATE,
    }).encode()

    last_error_text = None
    for attempt in range(AGNES_MAX_RETRIES):
        req = urllib.request.Request(AGNES_SUBMIT_URL, data=body, method="POST", headers=AGNES_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode(errors="replace")[:500]
            if e.code == 400 and "content_policy_violation" in error_body:
                raise ContentPolicyRejection(error_body)
            if e.code in AGNES_RETRYABLE_CODES:
                last_error_text = f"HTTP {e.code}: {error_body}"
                wait = 20 * (attempt + 1)
                print(f"Agnes submit transient error {e.code} (attempt {attempt + 1}/{AGNES_MAX_RETRIES}): {error_body}")
                print(f"Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Agnes submit HTTP {e.code}: {error_body}") from e

        video_id = data.get("video_id") or data.get("id")
        if not video_id:
            raise RuntimeError(f"Agnes submit response had no video_id: {data}")
        return video_id

    raise RuntimeError(f"Agnes submit still failing after {AGNES_MAX_RETRIES} attempts: {last_error_text}")


def poll_agnes_task(video_id):
    waited = 0
    url = f"{AGNES_POLL_URL}?video_id={video_id}&model_name={AGNES_MODEL}"
    while waited < POLL_MAX_WAIT:
        req = urllib.request.Request(url, headers=AGNES_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode(errors="replace")[:500]
            if e.code == 400 and "content_policy_violation" in error_body:
                raise ContentPolicyRejection(error_body)
            if e.code in AGNES_RETRYABLE_CODES:
                print(f"Agnes poll transient error {e.code}, will retry within the same poll loop: {error_body}")
                time.sleep(POLL_INTERVAL)
                waited += POLL_INTERVAL
                continue
            raise RuntimeError(f"Agnes poll HTTP {e.code}: {error_body}") from e
        status = data.get("status")
        if status == "completed":
            for key in ("video_url", "url"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
            raise RuntimeError(f"Agnes completed but no video URL found: {data}")
        if status == "failed":
            raise RuntimeError(f"Agnes generation failed: {data}")
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
    raise RuntimeError(f"Agnes generation timed out after {POLL_MAX_WAIT}s for video_id {video_id}")


def download_file(url, out_path):
    urllib.request.urlretrieve(url, out_path)
    return out_path


def verify_clip_url(url):
    """RELIABILITY FIX (2026-08-06): ported from Marius - HEAD-check an
    already-recorded clip URL before trusting it. A stale/expired storage
    URL silently built into the final assembly produces a broken video;
    catching it here means we just re-render that one shot instead."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=CLIP_VERIFY_TIMEOUT) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Clip verification failed for {url}: {e}")
        return False


def upload_clip(row_id, index, local_path):
    dest_name = f"{row_id}/shot_{index:03d}.mp4"
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/storage/v1/object/{VIDEO_CLIPS_BUCKET}/{dest_name}",
        data=file_bytes,
        method="POST",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Content-Type": "video/mp4",
            "x-upsert": "true",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Clip upload failed ({resp.status})")
    return f"{SUPABASE_URL}/storage/v1/object/public/{VIDEO_CLIPS_BUCKET}/{dest_name}"


def save_progress(row_id, video_urls, shot_retry_count):
    """Persist progress after every shot - whether it succeeded (new URL
    appended) or failed (just the retry counter bumped). Status stays
    'narrated' until every shot is done."""
    _supabase_request("PATCH", f"video_pipeline?id=eq.{row_id}", {
        "video_urls": video_urls,
        "shot_retry_count": shot_retry_count,
    })


def mark_video_complete(row_id, video_urls):
    _supabase_request("PATCH", f"video_pipeline?id=eq.{row_id}", {
        "status": "video_complete",
        "video_urls": video_urls,
    })


def mark_row_permanently_failed(row_id, reason):
    print(f"Row {row_id} permanently failed: {reason}")
    _supabase_request("PATCH", f"video_pipeline?id=eq.{row_id}", {"status": "failed"})


def render_one_shot():
    """Renders exactly the next not-yet-done shot on the oldest 'narrated'
    row. Returns True if a shot was successfully rendered (progress was
    made, whether or not that shot completed the video), False if there
    was nothing to do (no ready row) or the row hit a stopping condition
    (content-policy fail, permanent fail, or already fully done)."""
    row = get_next_row_to_render()
    if not row:
        print("No 'narrated' rows found. Nothing to do.")
        return False

    row_id = row["id"]
    shot_list = row.get("shot_list")
    if isinstance(shot_list, str):
        shot_list = json.loads(shot_list)
    shot_durations = row.get("shot_durations")
    if isinstance(shot_durations, str):
        shot_durations = json.loads(shot_durations)
    video_urls = row.get("video_urls") or []
    if isinstance(video_urls, str):
        video_urls = json.loads(video_urls) if video_urls else []
    shot_retry_count = row.get("shot_retry_count") or 0

    if not shot_list:
        mark_row_permanently_failed(row_id, "no shot_list on a 'narrated' row")
        return False
    if not shot_durations or len(shot_durations) != len(shot_list):
        mark_row_permanently_failed(
            row_id,
            f"shot_durations missing or length mismatch ({len(shot_durations or [])} vs {len(shot_list)} shots)",
        )
        return False

    if video_urls:
        verified_urls = []
        for i, url in enumerate(video_urls):
            if verify_clip_url(url):
                verified_urls.append(url)
            else:
                print(f"Row {row_id}: clip {i} failed verification, will regenerate from here.")
                break
        if len(verified_urls) != len(video_urls):
            video_urls = verified_urls
            save_progress(row_id, video_urls, 0)
            print(f"Row {row_id}: corrected progress after verification - {len(video_urls)}/{len(shot_list)} shots actually confirmed done")

    next_index = len(video_urls)
    total_shots = len(shot_list)

    if next_index >= total_shots:
        print(f"Row {row_id}: all {total_shots} shots already done, finalizing.")
        mark_video_complete(row_id, video_urls)
        return False

    shot = shot_list[next_index]
    prompt = shot.get("scene_description") or shot.get("visual_description", "")
    prompt = prompt.strip()
    # FIX 1 + FIX 2 (2026-09-06): append prompt-safety guards ported from
    # Marius - skin-realism (waxy/plastic AI skin) and crowd-anatomy
    # (cloned/identical crowd faces). Previously Gemini's raw
    # scene_description/visual_description went straight to Agnes with
    # no safety guard text at all.
    prompt = f"{prompt}, {QUALITY_GUARD}, {CROWD_ANATOMY_SAFETY_GUARD}"
    duration = shot_durations[next_index]

    print(f"Row {row_id}: rendering shot {next_index + 1}/{total_shots} (~{duration:.1f}s): {prompt[:80]!r}")
    try:
        video_id = submit_agnes_task(prompt, duration)
        video_url = poll_agnes_task(video_id)
        local_path = f"/tmp/shot_{row_id}_{next_index:03d}.mp4"
        download_file(video_url, local_path)
        clip_url = upload_clip(row_id, next_index, local_path)
        os.remove(local_path)

        video_urls.append(clip_url)
        if len(video_urls) >= total_shots:
            mark_video_complete(row_id, video_urls)
            print(f"Row {row_id}: all {total_shots} shots done this run. Status -> video_complete.")
        else:
            save_progress(row_id, video_urls, 0)
            print(f"Row {row_id}: shot {next_index + 1}/{total_shots} done. "
                  f"{total_shots - len(video_urls)} remaining, will continue this run if time/budget allow.")
        return True
    except ContentPolicyRejection as e:
        mark_row_permanently_failed(
            row_id,
            f"shot {next_index + 1}/{total_shots} rejected on content-policy grounds: {e}. "
            f"Prompt was: {prompt!r}. Reword shot_list[{next_index}] and reset status to 'narrated' to resume.",
        )
        return False
    except Exception as e:
        next_retry = shot_retry_count + 1
        if next_retry >= RETRY_LIMIT:
            mark_row_permanently_failed(row_id, f"shot {next_index + 1}/{total_shots} failed {RETRY_LIMIT}x: {e}")
            return False
        else:
            print(f"Row {row_id}: shot {next_index + 1}/{total_shots} failed "
                  f"(attempt {next_retry}/{RETRY_LIMIT}): {e}. Will retry next run.")
            save_progress(row_id, video_urls, next_retry)
            return True


def main():
    run_start = time.monotonic()
    shots_this_run = 0

    while shots_this_run < MAX_SHOTS_PER_RUN:
        elapsed = time.monotonic() - run_start
        if elapsed >= RUN_TIME_BUDGET_SECONDS:
            print(f"Run time budget ({RUN_TIME_BUDGET_SECONDS}s) reached after {shots_this_run} shot(s) - "
                  f"stopping cleanly, next scheduled run continues.")
            break

        shot_start = time.monotonic()
        made_progress = render_one_shot()
        if not made_progress:
            break
        shots_this_run += 1

        time_spent_on_shot = time.monotonic() - shot_start
        remaining_pace = AGNES_MIN_SECONDS_BETWEEN_SUBMITS - time_spent_on_shot
        if remaining_pace > 0 and shots_this_run < MAX_SHOTS_PER_RUN:
            elapsed = time.monotonic() - run_start
            if elapsed + remaining_pace >= RUN_TIME_BUDGET_SECONDS:
                print(f"Pacing sleep would exceed the run time budget - stopping after {shots_this_run} shot(s) instead.")
                break
            print(f"Pacing {remaining_pace:.0f}s before next Agnes submission (staying under 1 req/min)...")
            time.sleep(remaining_pace)

    print(f"Run finished: {shots_this_run} shot(s) rendered this run.")


if __name__ == "__main__":
    main()
