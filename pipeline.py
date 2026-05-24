#!/usr/bin/env python3
"""
xai-personalize-dashboard — pipeline

Fetches the user's recent Twitter signal (bookmarks, likes, home feed, own posts),
extracts an interest signature, picks today's most relevant feed candidates,
then calls `claude --agent <DASHBOARD_AGENT>` to draft posts/replies/quotes in voice.

Output: data/dashboard_data.json
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
OUT_PATH = DATA_DIR / "dashboard_data.json"
RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)

ENV_PATH = Path.home() / ".agent-reach" / "env.sh"

# Configured via env vars (set in ~/.agent-reach/env.sh or your shell).
# Required:  TWITTER_HANDLE       — the X/Twitter handle (without leading @)
# Optional:  DASHBOARD_AGENT      — claude --agent <name> to use for drafting (default: "voice")
USERNAME = (os.environ.get("TWITTER_HANDLE") or "").lstrip("@").strip()
AGENT_NAME = (os.environ.get("DASHBOARD_AGENT") or "voice").strip()
if not USERNAME:
    # try to read from ~/.agent-reach/env.sh as a fallback before failing
    try:
        proc = subprocess.run(
            ["bash", "-c", f"source {ENV_PATH} 2>/dev/null; echo \"${{TWITTER_HANDLE:-}}\""],
            capture_output=True, text=True, check=False,
        )
        USERNAME = proc.stdout.strip().lstrip("@")
    except Exception:
        pass

STOPWORDS = {
    "the","a","an","and","or","but","if","of","to","in","is","it","you","i","my","me","we","our",
    "for","on","with","at","by","this","that","these","those","be","are","was","were","been","being",
    "have","has","had","do","does","did","not","no","so","just","like","im","ive","its","dont","cant",
    "all","one","two","get","got","go","going","want","need","new","now","from","up","out","about",
    "your","they","them","their","he","she","his","her","as","an","into","over","than","then","when",
    "what","why","how","who","which","more","most","some","any","can","will","would","should","could",
    "rt","u","amp","https","http","co","t","s","t.co","de","re","ll","ve","m",
}


def env_for_twitter() -> dict:
    """Source ~/.agent-reach/env.sh and return resulting env."""
    env = os.environ.copy()
    if not ENV_PATH.exists():
        return env
    proc = subprocess.run(
        ["bash", "-c", f"set -a; source {ENV_PATH}; env"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


TW_ENV = env_for_twitter()


def twitter_json(args: list[str], timeout: int = 60) -> list | dict | None:
    """Run a twitter CLI command and parse JSON output (full text, not -c compact)."""
    twitter_bin = shutil.which("twitter", path=TW_ENV.get("PATH"))
    if not twitter_bin:
        return None
    cmd = [twitter_bin, *args, "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=TW_ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        sys.stderr.write(f"[pipeline] twitter {' '.join(args)} failed: {proc.stderr.strip()[:200]}\n")
        return None
    txt = proc.stdout.strip()
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", txt, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
        return None


def fetch_all() -> dict:
    """Parallel fetch of all relevant Twitter signal."""
    tasks = {
        "bookmarks": ["bookmarks", "-n", "50"],
        "favorites": ["favorites", "-n", "50"],
        "feed":      ["feed", "-n", "150"],
        "mine":      ["user-posts", f"@{USERNAME}", "-n", "30"],
    }
    out = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(twitter_json, args): key for key, args in tasks.items()}
        for fut in futs:
            key = futs[fut]
            try:
                out[key] = fut.result() or []
            except Exception as e:
                sys.stderr.write(f"[pipeline] fetch {key} crashed: {e}\n")
                out[key] = []

    # save raw for debugging
    for k, v in out.items():
        try:
            (RAW_DIR / f"{k}.json").write_text(json.dumps(v, indent=2, ensure_ascii=False))
        except Exception:
            pass
    return out


def normalize_tweet(t: dict) -> dict:
    """Reduce a tweet record (full or compact CLI shape) to {id, author, text, likes, rts, replies, time}."""
    if not isinstance(t, dict):
        return {}
    text = t.get("text") or t.get("full_text") or ""

    raw_author = t.get("author") or t.get("user") or t.get("screen_name") or ""
    if isinstance(raw_author, dict):
        author = raw_author.get("screenName") or raw_author.get("screen_name") or raw_author.get("username") or ""
    else:
        author = raw_author
    if author and not author.startswith("@"):
        author = "@" + author.lstrip("@")

    metrics = t.get("metrics") if isinstance(t.get("metrics"), dict) else {}
    likes = metrics.get("likes") if metrics else (t.get("likes") or t.get("favorite_count") or 0)
    rts   = metrics.get("retweets") if metrics else (t.get("rts") or t.get("retweet_count") or 0)
    replies = metrics.get("replies") if metrics else (t.get("replies") or t.get("reply_count") or 0)

    time_str = t.get("createdAtLocal") or t.get("time") or t.get("created_at") or ""

    return {
        "id": str(t.get("id") or t.get("rest_id") or ""),
        "author": author,
        "text": text,
        "likes": likes or 0,
        "rts":   rts or 0,
        "replies": replies or 0,
        "time":  time_str,
    }


def normalize_list(data) -> list[dict]:
    if isinstance(data, dict):
        for k in ("tweets", "data", "items", "results"):
            if k in data and isinstance(data[k], list):
                data = data[k]; break
        else:
            data = []
    if not isinstance(data, list):
        return []
    out = []
    for raw in data:
        n = normalize_tweet(raw)
        if n and n.get("text"):
            out.append(n)
    return out


def tokenize(text: str) -> list[str]:
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-zA-Z@#0-9_\s]", " ", text)
    return [w.lower() for w in text.split() if w]


import math


def interest_signature(bookmarks: list[dict], favorites: list[dict]) -> tuple[dict, dict]:
    """
    Returns (public_sig, weights).
    public_sig — what we display in the sidebar and pass to claude.
    weights    — internal per-account + per-keyword scoring weights for score_feed.

    Bookmarks signal strongest (intentional save), then favorites (like), then
    @-mentions inside text.
    """
    bookmark_authors = Counter()
    fav_authors      = Counter()
    mentioned        = Counter()
    words            = Counter()
    hashtags         = Counter()

    def consume(pool: list[dict], authors_counter: Counter):
        for t in pool:
            a = t.get("author") or ""
            if a:
                authors_counter[a] += 1
            for w in tokenize(t["text"]):
                if w.startswith("@") and len(w) > 1:
                    mentioned[w.lower()] += 1
                elif w.startswith("#") and len(w) > 1:
                    hashtags[w.lower()] += 1
                elif len(w) >= 4 and w not in STOPWORDS and not w.isdigit():
                    words[w] += 1

    consume(bookmarks, bookmark_authors)
    consume(favorites, fav_authors)

    # Combined account weight — bookmark author is 5× a mention, like author 2×.
    accounts_w: Counter = Counter()
    for a, n in bookmark_authors.items(): accounts_w[a.lower()] += n * 5
    for a, n in fav_authors.items():      accounts_w[a.lower()] += n * 2
    for a, n in mentioned.items():        accounts_w[a] += n * 1

    public = {
        "top_keywords":       [w for w, _ in words.most_common(20)],
        "top_accounts":       [a for a, _ in accounts_w.most_common(15)],
        "bookmark_authors":   [a for a, _ in bookmark_authors.most_common(10)],
        "fav_authors":        [a for a, _ in fav_authors.most_common(10)],
        "top_hashtags":       [h for h, _ in hashtags.most_common(8)],
        "sample_size":        len(bookmarks) + len(favorites),
    }
    weights = {
        "accounts": dict(accounts_w),                                # lowercased
        "keywords": dict(words.most_common(60)),                     # raw frequencies
        "bookmark_authors_set": {a.lower() for a in bookmark_authors},
    }
    return public, weights


def score_feed(feed: list[dict], weights: dict, mine: list[dict]) -> list[dict]:
    """
    Score feed by overlap with weighted signature.

    Author signal dominates (×5 multiplier on the per-account weight).
    Keyword signal is additive on weighted hits.
    Engagement is a tiny log-scaled tiebreaker, capped so a 145k-like Elon
    tweet can't drown out a 50-like tweet from someone you actually bookmark.
    """
    acc_w = weights.get("accounts", {})
    kw_w  = weights.get("keywords", {})
    bookmarked_authors = weights.get("bookmark_authors_set", set())
    mine_ids = {t["id"] for t in mine if t.get("id")}

    scored = []
    for t in feed:
        if not t.get("text") or t["id"] in mine_ids:
            continue
        author = (t.get("author") or "").lower()
        toks = set(tokenize(t["text"]))

        author_score = acc_w.get(author, 0) * 5
        # extra bump if you've literally bookmarked this author before
        if author in bookmarked_authors:
            author_score += 25

        kw_score = sum(kw_w.get(w, 0) for w in toks if w in kw_w)

        likes = int(t.get("likes") or 0)
        eng = min(math.log10(likes + 1) * 0.3, 1.0)

        score = author_score + kw_score + eng

        # Threshold: need author match OR strong keyword overlap.
        # This kills the random viral-Elon-tweet problem.
        if author_score == 0 and kw_score < 4:
            continue
        scored.append((score, t, author_score, kw_score))

    scored.sort(key=lambda x: -x[0])
    out = []
    seen_authors = Counter()
    for s, t, a_s, k_s in scored:
        # cap any single author at 3 picks
        if seen_authors[t["author"]] >= 3:
            continue
        seen_authors[t["author"]] += 1
        out.append({**t, "score": round(s, 2), "score_author": round(a_s, 2), "score_kw": round(k_s, 2)})
        if len(out) >= 50:
            break
    return out


def trending_feed(feed: list[dict], curated: list[dict], mine: list[dict]) -> list[dict]:
    """
    Items outside your interest signature, ranked by raw engagement.
    Useful for spotting broader-zeitgeist conversations.
    """
    curated_ids = {t["id"] for t in curated}
    mine_ids    = {t["id"] for t in mine if t.get("id")}
    pool = []
    for t in feed:
        if not t.get("text") or t["id"] in curated_ids or t["id"] in mine_ids:
            continue
        likes = int(t.get("likes") or 0)
        rts   = int(t.get("rts") or 0)
        score = math.log10(likes + 1) * 1.0 + math.log10(rts + 1) * 1.4
        pool.append((score, t))
    pool.sort(key=lambda x: -x[0])
    out = []
    seen_authors = Counter()
    for s, t in pool:
        if seen_authors[t["author"]] >= 2:
            continue
        seen_authors[t["author"]] += 1
        out.append({**t, "trend_score": round(s, 2)})
        if len(out) >= 30:
            break
    return out


# ─────────────────────────  claude --agent yagyesh  ─────────────────────────


DRAFT_PROMPT = """You are running as the configured voice agent. Your persona, voice rules, and reach templates are already loaded.

Below is today's signal. Generate drafts I can post. Reply with **JSON ONLY** — no preamble, no markdown fences, no explanation.

## My interest signature (from my recent bookmarks + likes)
top keywords: {keywords}
top accounts: {accounts}

## My recent posts (avoid repeating these themes verbatim)
{mine_block}

## Today's curated feed (these are the explore items — base replies and quotes ONLY on these IDs)
{feed_block}

## Task — return JSON with this exact shape:

{{
  "posts": [
    {{"id":"p1","template":"<Two-line Aphorism | Bracketed Label | > Bullet Take | Wake up babe | Terse Ship Status | Community Question Hook | My read | Story-Time Lesson | Specific-Number Flex | X is not Y>","text":"<the tweet>"}}
  ],
  "replies": [
    {{"id":"r1","target_id":"<feed item id>","target_author":"<@handle>","target_text":"<first 80 chars of target>","text":"<your reply, 1-2 short sentences>"}}
  ],
  "quotes": [
    {{"id":"q1","target_id":"<feed item id>","target_author":"<@handle>","target_text":"<first 80 chars of target>","text":"<your quote-tweet commentary that adds an angle>"}}
  ]
}}

Rules:
- Exactly 5 posts, 4 replies, 3 quotes.
- VARY templates across the 5 posts. Don't do 5 Two-line Aphorisms.
- Use lowercase-first tweets (except Title Case aphorism hook lines).
- No hashtags. No "Thoughts?" / "What do you think?" generic closers.
- Replies must reference real `target_id` from the feed above.
- Quotes must reference real `target_id` from the feed above.
- Stay grounded in MY stack (React Native, AI agents, Claude Code, indie dev). Don't fabricate projects.
- JSON only. Do not wrap in ```json. Output must start with `{{` and end with `}}`.
"""


def build_prompt(sig: dict, mine: list[dict], curated: list[dict]) -> str:
    mine_block = "\n".join(f"- {t['text'][:160]}" for t in mine[:8]) or "(none)"
    feed_block = "\n".join(
        f"[{t['id']}] {t['author']}: {t['text'][:200]}" for t in curated
    ) or "(empty)"
    return DRAFT_PROMPT.format(
        keywords=", ".join(sig["top_keywords"][:15]) or "(none)",
        accounts=", ".join(sig["top_accounts"][:10]) or "(none)",
        mine_block=mine_block,
        feed_block=feed_block,
    )


def extract_json(blob: str) -> dict | None:
    blob = blob.strip()
    # strip code fences if claude wraps despite instruction
    blob = re.sub(r"^```(?:json)?\s*", "", blob)
    blob = re.sub(r"\s*```$", "", blob)
    # find outermost {...}
    start = blob.find("{")
    end = blob.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(blob[start:end+1])
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[pipeline] JSON parse error: {e}\n")
        return None


def call_yagyesh(prompt: str) -> dict | None:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        sys.stderr.write("[pipeline] claude CLI not found on PATH\n")
        return None
    cmd = [claude_bin, "-p", prompt, "--agent", AGENT_NAME, "--effort", "medium"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        sys.stderr.write("[pipeline] claude call timed out\n")
        return None
    if proc.returncode != 0:
        sys.stderr.write(f"[pipeline] claude failed: {proc.stderr[:300]}\n")
        return None
    return extract_json(proc.stdout)


def fallback_drafts(curated: list[dict]) -> dict:
    """Used only if the claude call fails — keeps the dashboard non-empty."""
    return {
        "posts": [
            {"id": "p1", "template": "Terse Ship Status",
             "text": "claude call failed during pipeline — drafting offline. fix it and refresh."}
        ],
        "replies": [],
        "quotes":  [],
    }


def main():
    if not USERNAME:
        sys.stderr.write(
            "[pipeline] TWITTER_HANDLE is not set.\n"
            "  Add `export TWITTER_HANDLE=\"<your_handle>\"` to ~/.agent-reach/env.sh\n"
            "  (without the leading @) and re-run.\n"
        )
        sys.exit(1)
    t0 = time.time()
    print(f"[pipeline] fetching signal for @{USERNAME}...", flush=True)
    raw = fetch_all()

    bookmarks = normalize_list(raw["bookmarks"])
    favorites = normalize_list(raw["favorites"])
    feed      = normalize_list(raw["feed"])
    mine      = normalize_list(raw["mine"])

    print(f"[pipeline] bookmarks={len(bookmarks)} favorites={len(favorites)} "
          f"feed={len(feed)} mine={len(mine)}", flush=True)

    sig, weights = interest_signature(bookmarks, favorites)
    curated = score_feed(feed, weights, mine)
    trending = trending_feed(feed, curated, mine)
    print(f"[pipeline] curated={len(curated)} trending={len(trending)} "
          f"bookmark_authors={sig['bookmark_authors'][:5]} "
          f"keywords={sig['top_keywords'][:6]}", flush=True)

    print(f"[pipeline] calling claude --agent {AGENT_NAME} ...", flush=True)
    drafts = call_yagyesh(build_prompt(sig, mine, curated)) or fallback_drafts(curated)

    # tag drafts with stable ids if claude omitted them
    for i, p in enumerate(drafts.get("posts", [])):    p.setdefault("id", f"p{i+1}")
    for i, r in enumerate(drafts.get("replies", [])):  r.setdefault("id", f"r{i+1}")
    for i, q in enumerate(drafts.get("quotes", [])):   q.setdefault("id", f"q{i+1}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user": f"@{USERNAME}",
        "interest_signature": sig,
        "explore": curated,
        "trending": trending,
        "drafts": drafts,
        "counts": {
            "bookmarks": len(bookmarks),
            "favorites": len(favorites),
            "feed": len(feed),
            "mine": len(mine),
        },
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[pipeline] done in {payload['elapsed_seconds']}s → {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
