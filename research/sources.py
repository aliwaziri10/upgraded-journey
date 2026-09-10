"""
ClipStorm (formerly TechPulse) - Research Stage
PIVOT (2026-09-10): switched from tech/AI RSS headlines to true-crime /
unsolved-mystery case research, per Zia's decision to pivot this channel
to ClipStorm. Downstream stages (script/narration/video/assembly/publish)
are untouched - they only care about the {title, link, source, summary}
shape this stage produces, which is preserved exactly, so this is a
content-source swap, not a schema change.

Cases come from a curated seed list of well-documented unsolved/cold
cases (public record, safe to narrate - no ongoing live investigation
sensitivities, no naming of unconvicted living suspects). For each case,
this stage fetches its Wikipedia summary via Wikipedia's public REST API
(no key required) to use as the "summary" field the script stage expands
into a full narration - same role _clean_summary'd RSS text used to play
before.

Same dedup logic as before: checks Supabase video_pipeline for an
existing row with the same link (canonical Wikipedia URL) or exact title
before selecting a case, so the same case is never produced twice.
"""
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

WIKIPEDIA_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MAX_SUMMARY_CHARS = 900

# Curated seed list of well-documented, public-record unsolved/cold true
# crime cases - safe to narrate (no naming of unconvicted living suspects,
# no active/sensitive ongoing investigations). Wikipedia page titles,
# exactly as they appear in the URL.
CASE_SEED_LIST = [
    "Zodiac_Killer",
    "Tamam_Shud_case",
    "Hinterkaifeck_murders",
    "Murder_of_Elizabeth_Short",
    "Disappearance_of_the_Beaumont_children",
    "Isdal_Woman",
    "Watcher_(Cairo,_Illinois)",
    "Boy_in_the_Box",
    "Murder_of_JonBenét_Ramsey",
    "Disappearance_of_Maura_Murray",
    "Springfield_Three",
    "Death_of_Elisa_Lam",
    "Villisca_axe_murders",
    "Lead_Masks_Case",
    "Somerton_Man",
    "Circleville_letters",
    "Axeman_of_New_Orleans",
    "Zodiac_Killer_ciphers",
    "Disappearance_of_D._B._Cooper",
    "Servant_Girl_Annihilator",
]


def _clean_extract(raw_extract):
    if not raw_extract:
        return ""
    text = re.sub(r"\s+", " ", raw_extract).strip()
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."
    return text


def _query_supabase_exists(filter_clause):
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/video_pipeline?{filter_clause}&select=id"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read())
            return len(rows) > 0
    except Exception as e:
        print(f"Warning: could not reach Supabase for a dedup check ({e}). Proceeding without it for this check.")
        return False


def _case_already_processed(link, title):
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        print("Warning: SUPABASE_URL/SUPABASE_ANON_KEY not set - skipping duplicate check.")
        return False
    if link:
        encoded_link = urllib.parse.quote(link, safe="")
        if _query_supabase_exists(f"link=eq.{encoded_link}"):
            return True
    if title:
        encoded_title = urllib.parse.quote(title, safe="")
        if _query_supabase_exists(f"title=eq.{encoded_title}"):
            return True
    return False


def _fetch_wikipedia_summary(page_title):
    url = WIKIPEDIA_SUMMARY_API + urllib.parse.quote(page_title)
    req = urllib.request.Request(url, headers={"User-Agent": "ClipStorm-Research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    display_title = data.get("title", page_title.replace("_", " "))
    extract = _clean_extract(data.get("extract", ""))
    canonical_url = data.get("content_urls", {}).get("desktop", {}).get("page",
                        f"https://en.wikipedia.org/wiki/{urllib.parse.quote(page_title)}")
    return display_title, extract, canonical_url


def fetch_case_candidates():
    """Fetches Wikipedia summaries for every case in the seed list. A case
    whose Wikipedia fetch fails (renamed page, network hiccup) is skipped
    for this run - future runs will retry it since the seed list itself
    doesn't track failures."""
    results = []
    for page_title in CASE_SEED_LIST:
        try:
            display_title, extract, canonical_url = _fetch_wikipedia_summary(page_title)
        except Exception as e:
            print(f"Skipping {page_title!r} this run - Wikipedia fetch failed: {e}")
            continue
        if not extract:
            print(f"Skipping {page_title!r} - empty extract from Wikipedia.")
            continue
        results.append({
            "source": "wikipedia_true_crime",
            "title": display_title,
            "summary": extract,
            "link": canonical_url,
            "published": "",
            "fetched_at": datetime.utcnow().isoformat(),
        })
    return results


def select_top_headline(candidates):
    """Pick the first case in seed-list order that hasn't already been
    processed. (Seed list order acts as priority; unlike the RSS version
    there's no publish-date freshness signal to sort by.)"""
    if not candidates:
        return []
    for candidate in candidates:
        link = candidate.get("link", "")
        title = candidate.get("title", "")
        if _case_already_processed(link, title):
            print(f"Skipping already-processed case: {title}")
            continue
        return [candidate]
    print("All candidate cases this run were already processed - nothing new to publish. "
          "Add more titles to CASE_SEED_LIST to keep the pipeline fed.")
    return []


def save_headlines(headlines, path="research/latest_headlines.json"):
    with open(path, "w") as f:
        json.dump(headlines, f, indent=2)
    print(f"Saved {len(headlines)} case(s) to {path}")


if __name__ == "__main__":
    all_candidates = fetch_case_candidates()
    selected = select_top_headline(all_candidates)
    save_headlines(selected)
