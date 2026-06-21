#!/usr/bin/env python3
"""
xai-personalize-dashboard — pipeline

Fetches the user's recent Twitter signal (bookmarks, likes, home feed, own posts),
extracts an interest signature, picks today's most relevant feed candidates,
then calls `claude --agent <DASHBOARD_AGENT>` to draft posts/replies/quotes in voice.

Drafting is batched + run in parallel so we can produce ~100 of each kind per refresh
without blowing a single claude call's context/timeout.

Output: data/dashboard_data.json
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import voice_state
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
# Optional:  DASHBOARD_POSTS / DASHBOARD_REPLIES / DASHBOARD_QUOTES — how many of each to draft
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

# ── draft volume / batching ──────────────────────────────────────────────────
# How many drafts of each kind to aim for per refresh.
POSTS_TARGET   = int(os.environ.get("DASHBOARD_POSTS")   or 100)
REPLIES_TARGET = int(os.environ.get("DASHBOARD_REPLIES") or 300)
QUOTES_TARGET  = int(os.environ.get("DASHBOARD_QUOTES")  or 300)
# Per-claude-call batch size (keeps each generation focused + within timeout).
POST_BATCH  = 20
REPLY_BATCH = 20
QUOTE_BATCH = 20
# Concurrent claude processes. Each is heavy; keep this modest.
MAX_DRAFT_WORKERS = int(os.environ.get("DASHBOARD_DRAFT_WORKERS") or 5)

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
    # Larger pulls than the original 5-draft pipeline — we now want enough feed
    # candidates to seed ~100 replies + ~100 quotes against real target ids.
    tasks = {
        "bookmarks": ["bookmarks", "-n", "80"],
        "favorites": ["favorites", "-n", "80"],
        "feed":      ["feed", "-n", "500"],
        "mine":      ["user-posts", f"@{USERNAME}", "-n", "40"],
    }
    out = {}
    # The feed pull (-n 300) alone takes ~45-50s; run concurrently with the
    # other 3 calls it can exceed the default 60s timeout and silently drop to
    # zero candidates (no reply/quote targets). Give fetches a generous timeout.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(twitter_json, args, 150): key for key, args in tasks.items()}
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

    Returns up to ~140 candidates so there's a deep enough target pool to seed
    100 replies + 100 quotes against real ids.
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
        # cap any single author at 4 picks
        if seen_authors[t["author"]] >= 4:
            continue
        seen_authors[t["author"]] += 1
        out.append({**t, "score": round(s, 2), "score_author": round(a_s, 2), "score_kw": round(k_s, 2)})
        if len(out) >= 140:
            break
    return out


def trending_feed(feed: list[dict], curated: list[dict], mine: list[dict]) -> list[dict]:
    """
    Items outside your interest signature, ranked by raw engagement.
    Useful for spotting broader-zeitgeist conversations — and as extra reply/quote
    targets when the curated pool is thin.
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
        if len(out) >= 80:
            break
    return out


# ─────────────────────────  claude --agent <voice>  ─────────────────────────
#
# Drafting is split into per-kind, fixed-size batches that each become one
# `claude -p` call. Batches run in parallel. This (a) keeps any single call's
# output small enough to stay valid JSON within the timeout, and (b) lets us
# scale to ~100 of each kind without one mega-prompt.
#
# The voice rules + gold examples below were tuned against the user's REAL
# posted/scheduled tweets (data/posted.json, data/scheduled.json). The single
# biggest fix vs. the old prompt: stop the model from polishing. His real voice
# is lowercase, short, smiley-heavy, and a little sloppy on purpose.


GOLD_EXAMPLES = """## GOLD EXAMPLES — real tweets he wrote/approved. Match THIS texture (lowercase, smileys, short, a little sloppy on purpose). Absorb the voice; do NOT copy them.

POSTS:
- "updating my agents.md, it's bloated right now, need to get it under 50-100 lines"
- "trimmed my agents.md from 200 lines to 100. try to keep it upto date on teh changes in the repo :)\\n\\nit's more important than I thought (kept ignoring it)"
- "storage view on mac is still as trash as ever, even on more info it dosen't give proper details"
- "composer 2.5 is pretty good!!!"
- "shipped my own x feed tool last wk. opened twitter 3 times in 4 days. lowkey the most productive stretch of the yr :)"
- "anyone actually moved off claude code to codex full-time? not — like sub cancelled, daily driver swapped. just curious"
- "how are ya'll actually orchestrating multiple agents rn? have tried couple of famous ones in OSS orgs but really not clicking with any good ones"
- "my read on the \\"solo $1B company\\" discourse: it's not abt one founder doing everything. it's one founder steering 8 agents that each do what used to need a team. the bottleneck moved from headcount to judgment and orchaestration"

REPLIES:
- "have to sit on this! some of the skills i've added are way too bloated"
- "my apps don't have as much bugs as claude code :(\\nhappier that way!"
- "skills are lowkey the best thing to happen to claude code. just added the /teach skill 5 mins back :)"
- "gonna try a FULL code refactor workflow on opus 4.8 high effort and letting it sit for a day and seeing what happens"
- "would watch every single one. the skill ecosystem is so under-documented rn, half the good ones are buried in random gists"

QUOTES:
- "this. the ppl shipping fastest right now aren't the ones who stopped reading code — they're the ones reading 10x more of it cuz the agent generates 10x more. taste is the bottleneck, not typing speed."
- "it moved up a layer to steering and orchestration. we're so back."
"""


VOICE_RULES = """Rules:
- These must read like a real person typed them fast on his phone — NOT polished marketing copy. If a draft sounds clean and quotable, it's wrong. Rough it up.
- lowercase-first, ALWAYS. Do not capitalize the first word. No Title Case hook lines (real exception rate <1 in 20).
- Keep it SHORT — most posts 10-40 words, one core thought. Go longer only for a genuine rambling aside with a caveat, never a structured build-up to a punchline.
- Use the text smileys ":)" and ":(" liberally and sincerely — they're his signature. Prefer them over emoji; use actual emoji rarely (<=1 in 5), never reaction-emoji spam.
- Leave casual imperfection IN: lowercase "i" mid-sentence, comma splices, the occasional misspelling (dosen't, teh, abt, ya'll, upto, wknd). Do NOT write grammatically perfect sentences — perfect grammar is the #1 tell it's not him.
- Use his abbreviations naturally: abt, rn, wk, wknd, yr, ppl, cuz, lmk, tbh, lowkey, ig, ngl.
- Multi-punctuation ("!!!"/"!!") only for real excitement about something he actually likes.
- Use "\\n\\n" to break a setup from a caveat or aside — not to stack one-line fragments for rhythm. Avoid stacked-fragment blog cadence in tweets.
- Go EASY on em-dashes — his default connectors are commas and "\\n\\n". At most 1-2 across a batch.
- No hashtags. No "Thoughts?"/"What do you think?" closers. No stacked hype ("we're so back"/"this is the way") unless it genuinely fits once.
- Anti-hype, but QUIETLY. State the honest take plainly and let it sit. Don't perform cynicism with big punchlines.
- Community questions stay plain and low-stakes ("anyone actually..."/"how are ya'll..."/"lmk"), not clever.
- Bracketed-label and Title-Case-aphorism formats are RARE — at most one of each per batch, usually zero. Most posts follow no template.
- Name tools specifically and lowercase: claude code, codex, opus 4.8, gemini 3, composer 2.5, agents.md, /skills, react native, figma. Stay in MY stack (React Native, AI agents, Claude Code, indie dev). Don't fabricate projects.
- Replies/quotes: even shorter and plainer than posts. Lead with the take in 1 line, optionally one line of context. No tidy thesis.
- Vary openers across the batch — don't start every item the same way.
- JSON only. Do not wrap in ```json. Output must start with `{` and end with `}`."""


POSTS_SHAPE = """Return JSON ONLY (no preamble, no fences) with this exact shape:
{
  "posts": [
    {"id":"p1","template":"<short tag: ship update | gripe | question | my read | aphorism | none>","text":"<the tweet>"}
  ]
}"""

REPLIES_SHAPE = """Return JSON ONLY (no preamble, no fences) with this exact shape:
{
  "replies": [
    {"id":"r1","target_id":"<feed item id>","target_author":"<@handle>","target_text":"<first 80 chars of target>","text":"<your reply, 1-2 short sentences>"}
  ]
}"""

QUOTES_SHAPE = """Return JSON ONLY (no preamble, no fences) with this exact shape:
{
  "quotes": [
    {"id":"q1","target_id":"<feed item id>","target_author":"<@handle>","target_text":"<first 80 chars of target>","text":"<your quote-tweet commentary that adds an angle>"}
  ]
}"""


def _learned_state() -> str:
    """Formatted learned voice blocks from data/voice_state.json (or '')."""
    try:
        return voice_state.format_for_prompt(voice_state.load_state())
    except Exception:
        return ""


def _voice_header(sig: dict, mine: list[dict]) -> str:
    mine_block = "\n".join(f"- {t['text'][:160]}" for t in mine[:8]) or "(none)"
    return (
        "You are running as the configured voice agent. Your persona is already loaded.\n"
        "Below is today's signal. Draft tweets I can post. Reply with JSON ONLY.\n\n"
        "## My interest signature (from recent bookmarks + likes)\n"
        f"top keywords: {', '.join(sig['top_keywords'][:15]) or '(none)'}\n"
        f"top accounts: {', '.join(sig['top_accounts'][:10]) or '(none)'}\n\n"
        "## My recent posts (do NOT repeat these themes verbatim)\n"
        f"{mine_block}\n\n"
        f"{GOLD_EXAMPLES}\n"
        + (("\n" + _learned_state()) if _learned_state() else "")
    )


def _posts_prompt(sig: dict, mine: list[dict], inspo: list[dict], count: int,
                  lane: str | None = None, keywords: list[str] | None = None) -> str:
    inspo_block = "\n".join(f"- {t['author']}: {t['text'][:160]}" for t in inspo) or "(none)"
    kw = keywords if keywords is not None else sig.get("top_keywords", [])
    lane_block = (f"## This batch's angle (bias toward this; don't make every post fit it)\n{lane}\n\n"
                  if lane else "")
    return (
        _voice_header(sig, mine)
        + lane_block
        + f"## Themes to draw from for THIS batch (stay close to these — other batches cover the rest)\n"
        + (", ".join(kw) or "(none)") + "\n\n"
        + "## What's in my world today (anchor posts in DIFFERENT items below — don't all riff the same one)\n"
        + inspo_block + "\n\n"
        + f"## Task\nGenerate exactly {count} original posts in my voice, each on a DISTINCT topic — "
          "no two posts should be reworded versions of the same thought. "
          "The gold examples show my VOICE, not my topics: do NOT reuse their topics "
          "(agents.md, trimming skills, the feed tool) more than once across the batch. "
          "Vary openers — don't start more than one post with the same two words.\n\n"
        + POSTS_SHAPE + "\n\n" + VOICE_RULES
    )


def _replies_prompt(sig: dict, mine: list[dict], chunk: list[dict]) -> str:
    feed_block = "\n".join(f"[{t['id']}] {t['author']}: {t['text'][:200]}" for t in chunk)
    return (
        _voice_header(sig, mine)
        + "## Feed items to reply to (write ONE reply per item, using its EXACT id)\n"
        + feed_block + "\n\n"
        + f"## Task\nWrite a reply for EVERY one of the {len(chunk)} items above — aim for all {len(chunk)}. "
          "Only skip an item if it's an ad/spam/non-English or has literally nothing worth engaging.\n\n"
        + REPLIES_SHAPE + "\n\n" + VOICE_RULES
    )


def _quotes_prompt(sig: dict, mine: list[dict], chunk: list[dict]) -> str:
    feed_block = "\n".join(f"[{t['id']}] {t['author']}: {t['text'][:200]}" for t in chunk)
    return (
        _voice_header(sig, mine)
        + "## Feed items to quote-tweet (add MY angle; write ONE quote per item, using its EXACT id)\n"
        + feed_block + "\n\n"
        + f"## Task\nWrite a quote-tweet for EVERY one of the {len(chunk)} items above — aim for all {len(chunk)}. "
          "Only skip an item if it's an ad/spam/non-English or has literally nothing worth adding to.\n\n"
        + QUOTES_SHAPE + "\n\n" + VOICE_RULES
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


# Fallback path when `claude` isn't resolvable on PATH (e.g. the pipeline was
# spawned by a server/daemon whose PATH omits the cmux bin dir, or the
# ~/.local/bin/claude version symlink is mid-rotation during an update).
# Mirrors the cmux_bin() pattern in linkedin_cli.py.
_DEFAULT_CLAUDE = "/Applications/cmux.app/Contents/Resources/bin/claude"


def claude_bin() -> str | None:
    b = shutil.which("claude")
    if b:
        return b
    if os.path.exists(_DEFAULT_CLAUDE):
        return _DEFAULT_CLAUDE
    return None


def _claude_json(prompt: str, timeout: int = 300, label: str = "batch",
                 retries: int = 1) -> dict | None:
    """One `claude -p` call → parsed JSON dict (or None on failure).

    Logs returncode + a stderr/stdout snippet on failure so transient/env
    failures are diagnosable, and retries once before giving up (cheap
    insurance against a flaky single call collapsing the whole dashboard).
    """
    cb = claude_bin()
    if not cb:
        sys.stderr.write(
            f"[pipeline] {label}: claude CLI not found "
            f"(PATH lookup failed and {_DEFAULT_CLAUDE} missing)\n"
        )
        return None
    cmd = [cb, "-p", prompt, "--agent", AGENT_NAME, "--effort", "medium"]
    last_err = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            last_err = f"timed out after {timeout}s"
            sys.stderr.write(f"[pipeline] {label}: {last_err} (attempt {attempt + 1})\n")
            continue
        if proc.returncode != 0:
            last_err = (proc.stderr or proc.stdout or "").strip()[:200]
            sys.stderr.write(
                f"[pipeline] {label}: claude rc={proc.returncode} "
                f"(attempt {attempt + 1}): {last_err}\n"
            )
            continue
        parsed = extract_json(proc.stdout)
        if parsed is not None:
            return parsed
        last_err = "unparseable output"
        sys.stderr.write(
            f"[pipeline] {label}: rc=0 but JSON unparseable "
            f"(attempt {attempt + 1}); stdout[:200]={proc.stdout.strip()[:200]!r}\n"
        )
    sys.stderr.write(f"[pipeline] {label}: gave up after {retries + 1} attempts ({last_err})\n")
    return None


def _run_batches(prompts: list[str], labels: list[str] | None = None) -> list[dict]:
    """Run many claude calls in parallel; return the parsed dicts that succeeded."""
    out: list[dict] = []
    if not prompts:
        return out
    labels = labels or [f"batch {i + 1}/{len(prompts)}" for i in range(len(prompts))]
    with ThreadPoolExecutor(max_workers=MAX_DRAFT_WORKERS) as pool:
        futs = [pool.submit(_claude_json, p, 300, lbl)
                for p, lbl in zip(prompts, labels)]
        for fut in futs:
            try:
                d = fut.result()
            except Exception as e:
                sys.stderr.write(f"[pipeline] draft batch crashed: {e}\n")
                d = None
            if d:
                out.append(d)
    ok, total = len(out), len(prompts)
    if ok < total:
        sys.stderr.write(f"[pipeline] draft batches: {ok}/{total} succeeded\n")
    return out


def _chunk(lst: list, size: int) -> list[list]:
    return [lst[i:i + size] for i in range(0, len(lst), size)] if size > 0 else []


# ── variety: near-duplicate detection, history, target diversification ───────
# The old pipeline produced ~100 of each kind but they clustered hard: the same
# ~8 themes reworded, the same openers ("how are ya'll" / "anyone actually"),
# replies+quotes seeded from the identical top-of-pool, and nothing checked
# against what was already posted/scheduled. The helpers below force spread.

# Per-batch angle lanes. Each post batch is assigned a different lane so the N
# parallel batches explore different territory instead of all collapsing onto
# the gold examples' topics. Bias only — the voice rules still apply.
POST_LANES = [
    "ship/build update — something concrete you actually shipped, fixed, or are mid-building. specific, not abstract.",
    "honest gripe — one small real frustration with a tool or workflow. dry, no big punchline.",
    "my read — an honest take on a current AI-dev discourse. state it plainly and let it sit.",
    "noticing — a small thing you've noticed about how you work now with agents. reflective, not advice.",
    "tool signal — react to / compare specific tools in your stack, named lowercase.",
    "community question — ONE genuine low-stakes question to other devs. plain, not clever.",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _word_set(text: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
                     if w not in STOPWORDS and len(w) > 2)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _opener(text: str, n: int = 2) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower())[:n])


def load_history(mine: list[dict] | None = None) -> dict:
    """What I've already posted / scheduled — so we never re-surface it.

    Returns normalized post texts (to drop near-duplicate post drafts) and the
    target_ids already replied-to / quoted (to drop those reply/quote targets).
    """
    post_texts: list[frozenset[str]] = []
    reply_target_ids: set[str] = set()
    quote_target_ids: set[str] = set()
    for t in (mine or []):
        ws = _word_set(t.get("text", ""))
        if ws:
            post_texts.append(ws)
    for fn in ("posted.json", "scheduled.json"):
        path = DATA_DIR / fn
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        for it in items if isinstance(items, list) else []:
            kind = it.get("kind")
            tid = str(it.get("target_id") or "")
            if kind == "post":
                ws = _word_set(it.get("text", ""))
                if ws:
                    post_texts.append(ws)
            elif kind == "reply" and tid:
                reply_target_ids.add(tid)
            elif kind == "quote" and tid:
                quote_target_ids.add(tid)
    return {"post_texts": post_texts,
            "reply_target_ids": reply_target_ids,
            "quote_target_ids": quote_target_ids}


def _diversify_pool(items: list[dict], per_author_cap: int = 2) -> list[dict]:
    """Cap how many feed items per author survive so one loud account can't
    dominate the reply/quote targets. Preserves the incoming (score) order."""
    counts: Counter = Counter()
    out: list[dict] = []
    for t in items:
        a = (t.get("author") or "").lower()
        if a and counts[a] >= per_author_cap:
            continue
        counts[a] += 1
        out.append(t)
    return out


def _dedupe_posts(posts: list[dict], history: list[frozenset[str]] | None = None,
                  sim_threshold: float = 0.55, opener_cap: int = 2) -> list[dict]:
    """Drop near-duplicate posts: exact repeats, high token overlap with an
    already-kept post or anything in history, and more than `opener_cap` posts
    sharing the same opening words."""
    history = history or []
    seen_exact: set[str] = set()
    kept_sets: list[frozenset[str]] = []
    opener_counts: Counter = Counter()
    out: list[dict] = []
    for p in posts:
        text = p.get("text") or ""
        key = _norm(text)
        if not key or key in seen_exact:
            continue
        ws = _word_set(text)
        if any(_jaccard(ws, h) >= sim_threshold for h in history):
            continue
        if any(_jaccard(ws, k) >= sim_threshold for k in kept_sets):
            continue
        op = _opener(text)
        if op and opener_counts[op] >= opener_cap:
            continue
        seen_exact.add(key)
        kept_sets.append(ws)
        opener_counts[op] += 1
        out.append(p)
    return out


def generate_drafts(sig: dict, mine: list[dict], curated: list[dict],
                    trending: list[dict], history: dict | None = None) -> dict:
    """
    Batched, parallel drafting. Aims for POSTS_TARGET / REPLIES_TARGET / QUOTES_TARGET
    of each kind. Replies + quotes are seeded from a deduped pool of (curated +
    trending) feed items so every one references a real target id.

    Variety is forced three ways: post batches get distinct angle lanes +
    keyword partitions so they don't converge; the reply/quote pool is capped
    per-author so one loud account can't dominate; and anything we've already
    posted/scheduled (per `history`) is excluded.
    """
    history = history or {}
    hist_post_texts = history.get("post_texts", [])
    hist_reply_ids = history.get("reply_target_ids", set())
    hist_quote_ids = history.get("quote_target_ids", set())

    # target pool for replies/quotes — curated first, trending as backfill
    pool: list[dict] = []
    seen: set[str] = set()
    for t in (curated + trending):
        tid = t.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        pool.append(t)

    prompts: list[str] = []
    labels: list[str] = []

    # posts — each batch gets a distinct angle lane + a disjoint keyword slice
    # + a disjoint inspiration slice, so the parallel batches spread out.
    n_post_batches = max(1, math.ceil(POSTS_TARGET / POST_BATCH))
    all_kw = sig.get("top_keywords", []) or []
    for i in range(n_post_batches):
        count = min(POST_BATCH, POSTS_TARGET - i * POST_BATCH)
        if count <= 0:
            break
        lane = POST_LANES[i % len(POST_LANES)]
        # round-robin keyword partition: batch i gets every n-th keyword
        kw = all_kw[i::n_post_batches] or all_kw[:6]
        inspo = pool[i * 8:i * 8 + 8]
        prompts.append(_posts_prompt(sig, mine, inspo, count, lane=lane, keywords=kw))
        labels.append(f"posts[{i + 1}]")

    # replies + quotes — driven by feed targets (one draft per item).
    # Diversify by author cap so targets aren't dominated by 1-2 accounts, drop
    # anything already replied-to / quoted, then give replies and quotes
    # DIFFERENT orderings so they don't mirror each other's source posts.
    diverse = _diversify_pool(pool, per_author_cap=2)
    reply_pool = [t for t in diverse if str(t.get("id")) not in hist_reply_ids]
    quote_pool = [t for t in diverse if str(t.get("id")) not in hist_quote_ids]
    quote_pool = list(reversed(quote_pool))  # quotes lead with different posts than replies
    # Over-provision: the model skips some items, so request ~1.5x targets and
    # trim the aggregated results back down to the target count below.
    reply_targets = reply_pool[:min(len(reply_pool), math.ceil(REPLIES_TARGET * 1.5))]
    quote_targets = quote_pool[:min(len(quote_pool), math.ceil(QUOTES_TARGET * 1.5))]
    for i, ch in enumerate(_chunk(reply_targets, REPLY_BATCH)):
        prompts.append(_replies_prompt(sig, mine, ch))
        labels.append(f"replies[{i + 1}]")
    for i, ch in enumerate(_chunk(quote_targets, QUOTE_BATCH)):
        prompts.append(_quotes_prompt(sig, mine, ch))
        labels.append(f"quotes[{i + 1}]")

    print(f"[pipeline] dispatching {len(prompts)} draft batches "
          f"({MAX_DRAFT_WORKERS} at a time)...", flush=True)
    results = _run_batches(prompts, labels)

    posts: list[dict] = []
    replies: list[dict] = []
    quotes: list[dict] = []
    valid_ids = {t["id"] for t in pool}
    for d in results:
        posts.extend(d.get("posts") or [])
        for r in (d.get("replies") or []):
            if str(r.get("target_id") or "") in valid_ids:
                replies.append(r)
        for q in (d.get("quotes") or []):
            if str(q.get("target_id") or "") in valid_ids:
                quotes.append(q)

    posts = _dedupe_posts(posts, history=hist_post_texts)[:POSTS_TARGET]
    # one reply / one quote per target id
    def _dedupe_by_target(items: list[dict]) -> list[dict]:
        seen_t: set[str] = set()
        out = []
        for it in items:
            tid = str(it.get("target_id") or "")
            if tid in seen_t:
                continue
            seen_t.add(tid)
            out.append(it)
        return out
    replies = _dedupe_by_target(replies)[:REPLIES_TARGET]
    quotes  = _dedupe_by_target(quotes)[:QUOTES_TARGET]

    return {"posts": posts, "replies": replies, "quotes": quotes}


def fallback_drafts(curated: list[dict]) -> dict:
    """Used only if every claude batch fails — keeps the dashboard non-empty."""
    return {
        "posts": [
            {"id": "p1", "template": "ship update",
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

    print(f"[pipeline] drafting via claude --agent {AGENT_NAME} "
          f"(targets: {POSTS_TARGET} posts / {REPLIES_TARGET} replies / {QUOTES_TARGET} quotes)...",
          flush=True)
    history = load_history(mine)
    print(f"[pipeline] history: {len(history['post_texts'])} prior posts, "
          f"{len(history['reply_target_ids'])} replied + "
          f"{len(history['quote_target_ids'])} quoted targets to skip", flush=True)
    drafts = generate_drafts(sig, mine, curated, trending, history=history)
    if not (drafts.get("posts") or drafts.get("replies") or drafts.get("quotes")):
        drafts = fallback_drafts(curated)

    # tag drafts with stable, unique ids (override any model-supplied ids)
    for i, p in enumerate(drafts.get("posts", [])):    p["id"] = f"p{i+1}"
    for i, r in enumerate(drafts.get("replies", [])):  r["id"] = f"r{i+1}"
    for i, q in enumerate(drafts.get("quotes", [])):   q["id"] = f"q{i+1}"

    print(f"[pipeline] drafted posts={len(drafts.get('posts', []))} "
          f"replies={len(drafts.get('replies', []))} "
          f"quotes={len(drafts.get('quotes', []))}", flush=True)

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
