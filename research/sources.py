"""
Vera Lane Finance (formerly ClipStorm/TechPulse) - Research Stage

PIVOT (2026-09-10): switched from true-crime cases to personal-finance /
credit-card-optimization explainer topics, per Zia's decision to pivot
this channel again - modeled on Graham Stephan's format (concrete
number/headline hook, data-driven walkthrough, clear takeaway) but for a
fixed AI host persona (Vera Lane) rather than a real person. Downstream
stages are untouched - same {title, link, source, summary} shape as
every prior version of this file, so this is a third content-source
swap on the same schema, not a new pipeline.

Topics come from a curated seed list of well-documented, factual personal
finance / credit mechanics subjects (how compound interest works, how
credit utilization affects your score, how balance transfers work, etc.)
- deliberately mechanics/explainer topics, NOT specific card or stock
recommendations, to stay in "financial education" territory rather than
"financial advice" (see PROMPT_TEMPLATE in generate_script.py for the
same constraint applied to the actual script). Each topic's Wikipedia
summary is fetched the same way the true-crime version fetched case
summaries, and used as factual grounding for the script stage.

Same dedup logic as before: checks Supabase video_pipeline for an
existing row with the same link (canonical Wikipedia URL) or exact title
before selecting a topic, so the same topic is never produced twice.
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

# Curated seed list of factual personal-finance / credit mechanics topics -
# explainer subjects, not specific product/stock recommendations. Wikipedia
# page titles, exactly as they appear in the URL.
TOPIC_SEED_LIST = [
    "Compound_interest",
    "Credit_score",
    "Credit_card",
    "Annual_percentage_rate",
    "Balance_transfer",
    "Roth_IRA",
    "401(k)",
    "Emergency_fund",
    "Debt_avalanche_method",
    "Debt_snowball_method",
    "Credit_utilization_ratio",
    "High-yield_savings_account",
    "Index_fund",
    "Fixed-rate_mortgage",
    "Adjustable-rate_mortgage",
    "Tax_bracket",
    "FICO_score",
    "Credit_card_rewards_program",
    "Cash_back",
    "Sinking_fund_(finance)",
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


def _topic_already_processed(link, title):
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
    req = urllib.request.Request(url, headers={"User-Agent": "VeraLaneFinance-Research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    display_title = data.get("title", page_title.replace("_", " "))
    extract = _clean_extract(data.get("extract", ""))
    canonical_url = data.get("content_urls", {}).get("desktop", {}).get("page",
                        f"https://en.wikipedia.org/wiki/{urllib.parse.quote(page_title)}")
    return display_title, extract, canonical_url


def fetch_topic_candidates():
    """Fetches Wikipedia summaries for every topic in the seed list. A
    topic whose Wikipedia fetch fails (renamed page, network hiccup) is
    skipped for this run - future runs will retry it."""
    results = []
    for page_title in TOPIC_SEED_LIST:
        try:
            display_title, extract, canonical_url = _fetch_wikipedia_summary(page_title)
        except Exception as e:
            print(f"Skipping {page_title!r} this run - Wikipedia fetch failed: {e}")
            continue
        if not extract:
            print(f"Skipping {page_title!r} - empty extract from Wikipedia.")
            continue
        results.append({
            "source": "wikipedia_personal_finance",
            "title": display_title,
            "summary": extract,
            "link": canonical_url,
            "published": "",
            "fetched_at": datetime.utcnow().isoformat(),
        })
    return results


def select_top_headline(candidates):
    """Pick the first topic in seed-list order that hasn't already been
    processed."""
    if not candidates:
        return []
    for candidate in candidates:
        link = candidate.get("link", "")
        title = candidate.get("title", "")
        if _topic_already_processed(link, title):
            print(f"Skipping already-processed topic: {title}")
            continue
        return [candidate]
    print("All candidate topics this run were already processed - nothing new to publish. "
          "Add more titles to TOPIC_SEED_LIST to keep the pipeline fed.")
    return []


def save_headlines(headlines, path="research/latest_headlines.json"):
    with open(path, "w") as f:
        json.dump(headlines, f, indent=2)
    print(f"Saved {len(headlines)} topic(s) to {path}")


if __name__ == "__main__":
    all_candidates = fetch_topic_candidates()
    selected = select_top_headline(all_candidates)
    save_headlines(selected)
