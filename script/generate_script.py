"""
ClipStorm (formerly TechPulse) - Script Stage (LONG-FORM)

PIVOT (2026-09-10): PROMPT_TEMPLATE rewritten for true-crime/unsolved-
mystery documentary narration instead of tech/AI news, per Zia's decision
to pivot this channel to ClipStorm. Everything else in this file
(call_gemini retry/backoff logic, Supabase insert, JSON schema) is
UNCHANGED from the previous version - narration/generate_narration.py,
video/generate_video.py, assembly/assemble.py, and publish/youtube_upload.py
all consume the same {"narration", "has_recurring_person", "shots"} shape
regardless of content, so this stage is a prompt swap, not a schema change.

Cases are sourced by research/sources.py from Wikipedia (public record) -
the "title"/"summary"/"link" fields it produces map directly into this
prompt in place of the old headline/summary pair.
"""
import json
import os
import time
import urllib.request
import urllib.error

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_KEY}"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
}

TARGET_WORD_COUNT = "900-1100"   # ~6-7 minutes spoken
TARGET_SHOT_COUNT = 45           # roughly one shot per 8-9 seconds of narration

MAX_RETRIES = 4
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

PROMPT_TEMPLATE = """You are writing a long-form YouTube true-crime documentary video (6-7 minutes) about a real, well-documented unsolved or cold case. Your job is to hold attention for the full length with a tense, atmospheric documentary-style narrative, not a quick hook-and-CTA format.
Case: {title}
Background (public record, from Wikipedia): {summary}

Write THREE separate things:

1. NARRATION: A full {word_count} word documentary-style spoken script covering this case in depth - the setting and victims/people involved, the timeline of what happened, the investigation and its dead ends, the leading theories, and why the case remains unsolved or unresolved to this day. Structure it with a strong cold-open hook (a striking detail or the moment the case begins), build through the investigation's key developments and twists, and close with the case's current status and its lasting mystery. Write it as continuous flowing narration, not headers or bullet points.
   STAY STRICTLY FACTUAL: use ONLY details present in the background text above or widely and reliably documented public facts about this specific case. NEVER invent a name, date, confession, forensic detail, or theory that isn't real - fabricated specifics in true crime content are both misleading and a legal liability. If a detail is genuinely unknown or disputed, say so explicitly ("investigators were never able to determine...") rather than inventing an answer.
   NEVER accuse or name any living, unconvicted person as the perpetrator - refer to unidentified or unconvicted suspects only by whatever public designation is already used (e.g. "the Zodiac," "an unidentified male," a police-assigned label), never assert guilt as fact.

2. HAS_RECURRING_PERSON: true only if the case centers on one specific, named victim or figure whose identity is central and appropriate to depict (e.g. a named victim). false if the case is about a location, an unidentified body, or an event without one clear central figure.

3. SHOTS: An array of exactly {shot_count} shot objects, in order, that TOGETHER cover the entire narration from start to finish with no gaps and no overlaps. Each shot object has exactly two fields:
   - "narration_excerpt": the exact, word-for-word slice of the NARRATION text (from field 1) that plays during this shot. Copy this text verbatim from the narration you wrote - do not paraphrase it. Concatenating every shot's narration_excerpt in order must reconstruct the full narration exactly.
   - "visual_description": a short cinematic scene description for an AI video generator / archival-footage search, covering camera angle, lighting, and setting for this moment. Vary the shots (don't repeat the same framing back to back).

CRITICAL consistency and tone rules for visual_description:
- Favor atmospheric, tasteful visuals appropriate to true crime: period-accurate settings, streets, houses, documents, evidence photos/maps/newspaper clippings, investigators reviewing files, foggy/night exteriors, archival-style footage. NEVER depict graphic violence, gore, or an explicit crime-in-progress - suggest tension and unease through atmosphere and setting, not graphic content.
- Pick ONE real-world setting (the actual city/region where the case took place) strictly from what the background text describes. Do not invent or drift to an unrelated location.
- If HAS_RECURRING_PERSON is true: invent ONE fixed physical description consistent with any real details given (approximate age, general appearance) the first time, and repeat that EXACT description word-for-word in every shot they appear in. Never let age, gender, or appearance drift between shots. Depict them respectfully, never in a violent or graphic pose.
- If HAS_RECURRING_PERSON is false: do NOT invent any central person. Build every shot from setting, evidence objects, maps, documents, archival photos, exteriors of real buildings/streets - whatever fits the case.
- ZOOM DISCIPLINE: at most 1-in-4 shots may be push_in/crash_zoom/extreme_close_up. At least 1-in-4 shots must be wide/establishing. Never place two zoom-in-family shots back to back.
- Lighting should support mood - dim/overcast/nighttime is appropriate and expected for true crime atmosphere, but state the lighting explicitly in each shot so it's consistent.
- No anachronisms: only include objects/technology/clothing that plausibly belong to the actual time period and setting of the case.

Output strict JSON only, no other text:
{{"narration": "...", "has_recurring_person": true, "shots": [{{"narration_excerpt": "...", "visual_description": "..."}}]}}"""


def call_gemini(prompt):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }).encode()

    last_error = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(
            GEMINI_URL,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status = resp.status
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 15
                error_body = e.read().decode(errors="replace")[:300]
                print(f"Gemini HTTP {e.code} (retryable), waiting {wait}s before retry {attempt + 2}/{MAX_RETRIES}: {error_body}")
                last_error = f"HTTP {e.code}: {error_body}"
                time.sleep(wait)
                continue
            error_body = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"HTTP {e.code} from Gemini after {attempt + 1} attempt(s). Body: {error_body}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 15
                print(f"Gemini network error ({e.__class__.__name__}: {e}), waiting {wait}s before retry {attempt + 2}/{MAX_RETRIES}...")
                last_error = f"{e.__class__.__name__}: {e}"
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini network error after {attempt + 1} attempt(s): {e}") from e

        if not raw_bytes.strip():
            if attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 15
                print(f"Gemini returned an EMPTY body (status={status}), waiting {wait}s before retry {attempt + 2}/{MAX_RETRIES}...")
                last_error = f"empty body, status={status}"
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini returned an EMPTY body after {attempt + 1} attempt(s). Status={status}")

        try:
            result = json.loads(raw_bytes)
        except json.JSONDecodeError as e:
            preview = raw_bytes[:500].decode(errors="replace")
            raise RuntimeError(f"Gemini response wasn't valid JSON. Status={status}. Body preview: {preview}") from e
        try:
            content = result["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected response shape from Gemini: {json.dumps(result)[:500]}") from e
        if not content or not content.strip():
            if attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 15
                print(f"Gemini returned an empty completion, waiting {wait}s before retry {attempt + 2}/{MAX_RETRIES}...")
                last_error = "empty completion"
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini returned an empty completion after {attempt + 1} attempt(s).")

        print("=== RAW GEMINI OUTPUT (truncated to 1000 chars) ===")
        print(content.strip()[:1000])
        print("=== END ===")
        return content.strip()

    raise RuntimeError(f"Gemini still failing after {MAX_RETRIES} attempts. Last error: {last_error}")


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


def insert_pipeline_row(headline, narration, has_recurring_person, shot_list):
    row = {
        "title": headline["title"],
        "link": headline.get("link", ""),
        "source": headline.get("source", ""),
        "script": narration,
        "shot_list": shot_list,
        "status": "scripted",
    }
    result = _supabase_request("POST", "video_pipeline", row)
    return result[0]["id"]


def generate_scripts(headlines_path="research/latest_headlines.json"):
    with open(headlines_path) as f:
        headlines = json.load(f)
    for h in headlines:
        prompt = PROMPT_TEMPLATE.format(
            title=h["title"], summary=h["summary"],
            word_count=TARGET_WORD_COUNT, shot_count=TARGET_SHOT_COUNT,
        )
        try:
            raw = call_gemini(prompt)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw)
            shot_list = parsed["shots"]
            pipeline_id = insert_pipeline_row(
                h, parsed["narration"], bool(parsed.get("has_recurring_person", False)), shot_list
            )
            print(f"Created pipeline row {pipeline_id} for '{h['title']}' with {len(shot_list)} shots, status=scripted.")
        except Exception as e:
            print(f"Failed on {h['title']}: {e}")


if __name__ == "__main__":
    generate_scripts()
