# X Post Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `13 analytics` screen that analyzes which X posts/quotes are working — by engagement rate, format, timing, and keywords — refreshable manually and daily.

**Architecture:** A new `analytics.py` engine mirrors the existing `eval_engine.py` pattern: it snapshots per-post metrics daily into a gitignored history file, computes deterministic breakdowns, layers an LLM narrative on top, and writes a single report JSON the UI reads. Wired in via two server routes, a daily-guarded pipeline hook, and a new front-end screen.

**Tech Stack:** Python 3.10+ stdlib only (no third-party deps), the `twitter` CLI (via `pipeline.twitter_json`), `claude` CLI (via `pipeline._claude_json`), vanilla JS front-end (`static/app.js` + `index.html`).

## Global Constraints

- Python **stdlib only** — no new dependencies.
- All persistent state lives under `data/` (gitignored). Never commit `data/*.json`.
- Use **atomic writes** for JSON (`tempfile.mkstemp` + `os.replace`), matching `eval_engine._atomic_write_json`.
- Engine functions must accept injectable `fetcher` / `caller` (and `now`) so tests run with **no network and no LLM**, matching `tests/test_eval_engine.py`.
- Traction metric is **engagement rate** = `(likes+retweets+replies+quotes+bookmarks) / max(views, 1)`.
- Scope v1: **original posts + quote-tweets only**; replies excluded. Retweets (`isRetweet`) skipped.
- Window: rolling **30 days**.
- Keyword **min support ≥ 3**; top/bottom ranking **min views floor = 50**.
- `user-posts` fetch depth: **`-n 150`**.
- Tests are runnable both via `pytest` and via `python3 tests/test_analytics.py` (the file's `__main__` runner), matching the existing test convention.

---

### Task 1: `analytics.py` scaffolding + pure helpers

**Files:**
- Create: `analytics.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `eng_rate(metrics: dict) -> float`
  - `classify_length(text: str) -> str` → one of `"short"|"medium"|"long"`
  - `has_link(text: str, urls: list | None) -> bool`
  - `tokenize(text: str) -> list[str]`
  - `local_hour(created_local: str) -> int`
  - `local_weekday(created_local: str) -> int` (0=Mon … 6=Sun)
  - module constants: `ROOT`, `REPORT_PATH`, `HISTORY_PATH`, `POSTED_PATH`, `WINDOW_DAYS=30`, `MIN_SUPPORT=3`, `MIN_VIEWS=50`, `FETCH_N=150`, `CADENCE_HOURS=24`, `STOPWORDS`
  - `_atomic_write_json(path, obj)` (copied from `eval_engine`)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for analytics.py.

Run directly (no pytest): python3 tests/test_analytics.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics as AN  # noqa: E402


def test_eng_rate_basic():
    m = {"likes": 5, "retweets": 2, "replies": 1, "quotes": 1, "bookmarks": 1, "views": 100}
    assert AN.eng_rate(m) == 0.1   # 10 / 100


def test_eng_rate_guards_zero_views():
    m = {"likes": 3, "retweets": 0, "replies": 0, "quotes": 0, "bookmarks": 0, "views": 0}
    assert AN.eng_rate(m) == 3.0   # 3 / max(0,1)


def test_classify_length():
    assert AN.classify_length("a" * 50) == "short"
    assert AN.classify_length("a" * 150) == "medium"
    assert AN.classify_length("a" * 250) == "long"


def test_has_link():
    assert AN.has_link("check https://x.com/foo", None) is True
    assert AN.has_link("no link here", []) is False
    assert AN.has_link("plain", ["https://t.co/x"]) is True


def test_tokenize_strips_stopwords_urls_handles():
    toks = AN.tokenize("Building an AI agent with @claude https://t.co/x the best")
    assert "building" in toks
    assert "agent" in toks
    assert "claude" not in toks       # @handle stripped
    assert "the" not in toks          # stopword
    assert "https" not in toks        # url stripped


def test_local_hour_and_weekday():
    assert AN.local_hour("2026-06-23 19:58") == 19
    # 2026-06-23 is a Tuesday → weekday() == 1
    assert AN.local_weekday("2026-06-23 19:58") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_analytics.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics'`

- [ ] **Step 3: Write minimal implementation**

```python
"""X post analytics: daily metric snapshots + deterministic breakdowns + an
LLM narrative. Mirrors eval_engine.py. All state lives in data/ (gitignored).
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "data" / "analytics.json"
HISTORY_PATH = ROOT / "data" / "analytics_history.json"
POSTED_PATH = ROOT / "data" / "posted.json"

WINDOW_DAYS = 30
MIN_SUPPORT = 3
MIN_VIEWS = 50
FETCH_N = 150
CADENCE_HOURS = 24

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "is", "are", "was",
    "were", "be", "been", "to", "of", "in", "on", "for", "with", "at", "by",
    "from", "it", "this", "that", "these", "those", "i", "you", "he", "she",
    "we", "they", "my", "your", "its", "as", "so", "just", "not", "no", "do",
    "does", "did", "have", "has", "had", "will", "would", "can", "could", "all",
}


def _atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def eng_rate(metrics: dict) -> float:
    m = metrics or {}
    engaged = (m.get("likes", 0) + m.get("retweets", 0) + m.get("replies", 0)
               + m.get("quotes", 0) + m.get("bookmarks", 0))
    return engaged / max(m.get("views", 0) or 0, 1)


def classify_length(text: str) -> str:
    n = len(text or "")
    if n < 100:
        return "short"
    if n <= 200:
        return "medium"
    return "long"


def has_link(text: str, urls) -> bool:
    if urls:
        return True
    return "http://" in (text or "") or "https://" in (text or "")


def tokenize(text: str) -> list:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[@#]\w+", " ", cleaned)
    words = re.findall(r"[a-z][a-z'-]+", cleaned.lower())
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


def local_hour(created_local: str) -> int:
    return int(created_local[11:13])


def local_weekday(created_local: str) -> int:
    return datetime.strptime(created_local, "%Y-%m-%d %H:%M").weekday()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_analytics.py`
Expected: all `ok` lines, exit 0.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): pure metric/format/keyword helpers"
```

---

### Task 2: `snapshot_metrics` — daily history upsert

**Files:**
- Modify: `analytics.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Consumes: helpers from Task 1; `data/posted.json` (read-only).
- Produces:
  - `load_history() -> dict` / `save_history(hist: dict)`
  - `_kind_map() -> dict[str, str]` (tweet_id → `"post"|"reply"|"quote"`)
  - `snapshot_metrics(now: datetime, fetcher=None) -> dict` — upserts one snapshot/day per in-window, non-retweet, non-reply post; returns the updated history dict.
  - History entry shape:
    ```json
    {"<id>": {"created_at": "<ISO>", "created_local": "YYYY-MM-DD HH:MM",
              "kind": "post|quote", "source": "dashboard|manual|unknown",
              "text": "...", "has_media": true, "has_link": false, "lang": "en",
              "snapshots": [{"ts": "<ISO>", "likes": 0, "retweets": 0,
                             "replies": 0, "quotes": 0, "views": 0, "bookmarks": 0}]}}
    ```
- A `fetcher()` returns a list of raw post dicts shaped like `twitter user-posts --json`'s `data[]` items (`id`, `text`, `metrics`, `createdAtISO`, `createdAtLocal`, `media`, `urls`, `lang`, `isRetweet`).

- [ ] **Step 1: Write the failing test**

```python
def _post(pid, text, views, likes, created_iso, created_local,
          media=None, urls=None, retweet=False, lang="en"):
    return {
        "id": pid, "text": text, "isRetweet": retweet, "lang": lang,
        "createdAtISO": created_iso, "createdAtLocal": created_local,
        "media": media or [], "urls": urls or [],
        "metrics": {"likes": likes, "retweets": 0, "replies": 0,
                    "quotes": 0, "views": views, "bookmarks": 0},
    }


def _wire(tmp):
    AN.REPORT_PATH = tmp / "analytics.json"
    AN.HISTORY_PATH = tmp / "analytics_history.json"
    AN.POSTED_PATH = tmp / "posted.json"


def test_snapshot_upserts_and_skips_retweets():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [
        _post("1", "hello world", 100, 5, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30"),
        _post("2", "a retweet", 9, 0, "2026-06-24T09:00:00+00:00", "2026-06-24 14:30", retweet=True),
    ]
    hist = AN.snapshot_metrics(now, fetcher=lambda: posts)
    assert "1" in hist and "2" not in hist          # retweet skipped
    assert len(hist["1"]["snapshots"]) == 1
    assert hist["1"]["snapshots"][0]["views"] == 100


def test_snapshot_dedupes_same_day_appends_next_day():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    posts = [_post("1", "hi", 100, 5, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30")]
    AN.snapshot_metrics(datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc), fetcher=lambda: posts)
    AN.snapshot_metrics(datetime(2026, 6, 24, 20, 0, tzinfo=timezone.utc), fetcher=lambda: posts)
    hist = AN.load_history()
    assert len(hist["1"]["snapshots"]) == 1          # same day → no second snapshot
    posts2 = [_post("1", "hi", 250, 9, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30")]
    hist = AN.snapshot_metrics(datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc), fetcher=lambda: posts2)
    assert len(hist["1"]["snapshots"]) == 2          # next day → appended
    assert hist["1"]["snapshots"][-1]["views"] == 250


def test_snapshot_drops_out_of_window_posts():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [_post("old", "ancient", 100, 5, "2026-04-01T10:00:00+00:00", "2026-04-01 15:30")]
    hist = AN.snapshot_metrics(now, fetcher=lambda: posts)
    assert hist == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_analytics.py`
Expected: FAIL — `AttributeError: module 'analytics' has no attribute 'snapshot_metrics'`

- [ ] **Step 3: Write minimal implementation**

Append to `analytics.py`:

```python
def load_history() -> dict:
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(hist: dict) -> None:
    _atomic_write_json(HISTORY_PATH, hist)


def _kind_map() -> dict:
    """tweet_id -> kind/source from posted.json (best-effort attribution)."""
    try:
        posted = json.loads(POSTED_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for p in posted:
        tid = p.get("tweet_id")
        if tid:
            out[tid] = {"kind": p.get("kind", "post"), "source": p.get("source", "unknown")}
    return out


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def snapshot_metrics(now: datetime, fetcher=None) -> dict:
    fetcher = fetcher or _default_fetcher
    hist = load_history()
    kinds = _kind_map()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    today = now.date().isoformat()
    for raw in (fetcher() or []):
        if raw.get("isRetweet"):
            continue
        tid = raw.get("id")
        created = _parse_iso(raw.get("createdAtISO"))
        if not tid or not created or created < cutoff:
            continue
        attrib = kinds.get(tid, {})
        kind = attrib.get("kind", "post")
        if kind == "reply":            # v1 excludes replies
            continue
        text = raw.get("text", "")
        entry = hist.setdefault(tid, {"snapshots": []})
        entry.update({
            "created_at": raw.get("createdAtISO"),
            "created_local": raw.get("createdAtLocal"),
            "kind": kind,
            "source": attrib.get("source", "unknown"),
            "text": text,
            "has_media": bool(raw.get("media")),
            "has_link": has_link(text, raw.get("urls")),
            "lang": raw.get("lang"),
        })
        snaps = entry["snapshots"]
        if snaps and (_parse_iso(snaps[-1]["ts"]) or now).date().isoformat() == today:
            continue                   # already snapshotted today
        m = raw.get("metrics", {})
        snaps.append({"ts": now.isoformat(), "likes": m.get("likes", 0),
                      "retweets": m.get("retweets", 0), "replies": m.get("replies", 0),
                      "quotes": m.get("quotes", 0), "views": m.get("views", 0),
                      "bookmarks": m.get("bookmarks", 0)})
    save_history(hist)
    return hist


def _default_fetcher():
    import pipeline
    res = pipeline.twitter_json(["user-posts", f"@{pipeline.USERNAME}", "-n", str(FETCH_N)], timeout=150)
    if isinstance(res, dict):
        return res.get("data") or []
    return res or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_analytics.py`
Expected: all `ok`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): daily per-post metric snapshots with dedupe"
```

---

### Task 3: `compute_report` — deterministic breakdowns

**Files:**
- Modify: `analytics.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Consumes: helpers (Task 1), history shape (Task 2).
- Produces:
  - `compute_report(history: dict, now: datetime, window_days=WINDOW_DAYS) -> dict` returning:
    ```python
    {
      "n_posts": int,
      "metric": "engagement_rate",
      "overall": {"avg_eng_rate": float, "avg_views": float},
      "breakdowns": {
        "type":   {key: {"avg_eng_rate", "avg_views", "count"}},
        "media":  {...},   # keys "with_media" / "text_only"
        "link":   {...},   # keys "with_link" / "no_link"
        "length": {...},   # keys "short"/"medium"/"long"
        "hour":   {str(hour): {...}},
        "weekday":{str(0..6): {...}},
      },
      "keywords": [{"token": str, "lift": float, "avg_eng_rate": float, "support": int}],
      "top":    [{"id","text","kind","eng_rate","views","created_local"}],
      "bottom": [{...}],
    }
    ```

- [ ] **Step 1: Write the failing test**

```python
def _hist_entry(text, views, likes, created_iso, created_local,
                kind="post", media=False, link=False, ts="2026-06-24T12:00:00+00:00"):
    return {
        "created_at": created_iso, "created_local": created_local, "kind": kind,
        "source": "manual", "text": text, "has_media": media, "has_link": link,
        "lang": "en",
        "snapshots": [{"ts": ts, "likes": likes, "retweets": 0, "replies": 0,
                       "quotes": 0, "views": views, "bookmarks": 0}],
    }


def test_compute_report_breakdowns_and_ranking():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {
        "hi": _hist_entry("agent agent agent winner", 100, 30, "2026-06-23T10:00:00+00:00", "2026-06-23 15:30"),
        "lo": _hist_entry("agent dull loser", 100, 1, "2026-06-22T10:00:00+00:00", "2026-06-22 09:30"),
        "mid": _hist_entry("agent middle road", 100, 10, "2026-06-21T10:00:00+00:00", "2026-06-21 18:30"),
    }
    rep = AN.compute_report(hist, now)
    assert rep["n_posts"] == 3
    assert rep["top"][0]["id"] == "hi"
    assert rep["bottom"][0]["id"] == "lo"
    # "agent" appears in all 3 (support 3) → present in keywords
    assert any(k["token"] == "agent" and k["support"] == 3 for k in rep["keywords"])
    assert "post" in rep["breakdowns"]["type"]
    assert rep["breakdowns"]["type"]["post"]["count"] == 3


def test_compute_report_drops_low_view_posts_from_ranking():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {
        "tiny": _hist_entry("agent low reach high rate", 5, 5, "2026-06-23T10:00:00+00:00", "2026-06-23 15:30"),
        "real": _hist_entry("agent real reach", 200, 20, "2026-06-23T11:00:00+00:00", "2026-06-23 16:30"),
    }
    rep = AN.compute_report(hist, now)
    ids = [p["id"] for p in rep["top"]]
    assert "tiny" not in ids            # below MIN_VIEWS floor
    assert "real" in ids


def test_compute_report_window_excludes_old():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {"old": _hist_entry("agent ancient", 100, 5, "2026-04-01T10:00:00+00:00", "2026-04-01 15:30")}
    rep = AN.compute_report(hist, now)
    assert rep["n_posts"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_analytics.py`
Expected: FAIL — `AttributeError: module 'analytics' has no attribute 'compute_report'`

- [ ] **Step 3: Write minimal implementation**

Append to `analytics.py`:

```python
def _avg(nums):
    nums = list(nums)
    return sum(nums) / len(nums) if nums else 0.0


def _group(recs, keyfn):
    out = {}
    for r in recs:
        k = keyfn(r)
        out.setdefault(k, []).append(r)
    return {str(k): {"avg_eng_rate": round(_avg(x["eng_rate"] for x in v), 4),
                     "avg_views": round(_avg(x["views"] for x in v), 1),
                     "count": len(v)}
            for k, v in out.items()}


def compute_report(history: dict, now, window_days=WINDOW_DAYS) -> dict:
    cutoff = now - timedelta(days=window_days)
    recs = []
    for tid, e in history.items():
        if not e.get("snapshots"):
            continue
        created = _parse_iso(e.get("created_at"))
        if not created or created < cutoff:
            continue
        snap = e["snapshots"][-1]
        recs.append({
            "id": tid, "text": e.get("text", ""), "kind": e.get("kind", "post"),
            "has_media": e.get("has_media", False), "has_link": e.get("has_link", False),
            "created_local": e.get("created_local", ""),
            "views": snap.get("views", 0), "eng_rate": eng_rate(snap),
        })

    overall_rate = _avg(r["eng_rate"] for r in recs)
    breakdowns = {
        "type":    _group(recs, lambda r: r["kind"]),
        "media":   _group(recs, lambda r: "with_media" if r["has_media"] else "text_only"),
        "link":    _group(recs, lambda r: "with_link" if r["has_link"] else "no_link"),
        "length":  _group(recs, lambda r: classify_length(r["text"])),
        "hour":    _group(recs, lambda r: local_hour(r["created_local"]) if r["created_local"] else -1),
        "weekday": _group(recs, lambda r: local_weekday(r["created_local"]) if r["created_local"] else -1),
    }

    # keyword lift
    tok_rates = {}
    for r in recs:
        for tok in set(tokenize(r["text"])):
            tok_rates.setdefault(tok, []).append(r["eng_rate"])
    keywords = []
    for tok, rates in tok_rates.items():
        if len(rates) < MIN_SUPPORT:
            continue
        avg = _avg(rates)
        keywords.append({"token": tok, "support": len(rates),
                         "avg_eng_rate": round(avg, 4),
                         "lift": round(avg / overall_rate, 2) if overall_rate else 0.0})
    keywords.sort(key=lambda k: k["lift"], reverse=True)

    ranked = sorted((r for r in recs if r["views"] >= MIN_VIEWS),
                    key=lambda r: r["eng_rate"], reverse=True)

    def card(r):
        return {"id": r["id"], "text": r["text"], "kind": r["kind"],
                "eng_rate": round(r["eng_rate"], 4), "views": r["views"],
                "created_local": r["created_local"]}

    return {
        "n_posts": len(recs),
        "metric": "engagement_rate",
        "overall": {"avg_eng_rate": round(overall_rate, 4),
                    "avg_views": round(_avg(r["views"] for r in recs), 1)},
        "breakdowns": breakdowns,
        "keywords": keywords[:20],
        "top": [card(r) for r in ranked[:5]],
        "bottom": [card(r) for r in ranked[-5:][::-1]],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_analytics.py`
Expected: all `ok`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): deterministic breakdowns, keyword lift, top/bottom"
```

---

### Task 4: `run_analytics` orchestration + LLM insights + report I/O

**Files:**
- Modify: `analytics.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Consumes: `snapshot_metrics`, `compute_report`.
- Produces:
  - `build_insight_prompt(report: dict) -> str`
  - `_default_caller(prompt: str) -> dict | None`
  - `_should_run(now: datetime) -> tuple[bool, str]` (cadence guard off `REPORT_PATH`'s `generated_at`)
  - `run_analytics(force=False, now=None, fetcher=None, caller=None) -> dict` — snapshot → compute → (LLM) insights → write `analytics.json`; returns the report or `{"skipped": reason}`.
  - `load_report() -> dict` / `overview() -> dict` (the GET payload).
  - Report adds `{"ts", "generated_at", "window_days", "insights"}` to the `compute_report` dict. `insights` is `null` when the LLM call fails.

- [ ] **Step 1: Write the failing test**

```python
FAKE_INSIGHT = {"themes_working": ["agent tooling"], "themes_flat": ["memes"],
                "timing_insight": "evenings win", "format_insight": "short text wins",
                "recommendations": ["post more agent content in the evening"]}


def test_run_analytics_writes_report_with_insights():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [_post("1", "agent tooling wins", 200, 30, "2026-06-23T10:00:00+00:00", "2026-06-23 19:30"),
             _post("2", "random meme", 200, 1, "2026-06-22T10:00:00+00:00", "2026-06-22 09:30")]
    rep = AN.run_analytics(force=True, now=now, fetcher=lambda: posts, caller=lambda p: FAKE_INSIGHT)
    assert "skipped" not in rep
    assert rep["n_posts"] == 2
    assert rep["insights"]["timing_insight"] == "evenings win"
    assert AN.load_report()["insights"]["themes_working"] == ["agent tooling"]


def test_run_analytics_survives_llm_failure():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [_post("1", "agent tooling", 200, 30, "2026-06-23T10:00:00+00:00", "2026-06-23 19:30")]
    rep = AN.run_analytics(force=True, now=now, fetcher=lambda: posts, caller=lambda p: None)
    assert rep["insights"] is None
    assert rep["n_posts"] == 1            # deterministic report still shipped


def test_run_analytics_cadence_guard():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    posts = [_post("1", "agent tooling", 200, 30, "2026-06-23T10:00:00+00:00", "2026-06-23 19:30")]
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    AN.run_analytics(force=True, now=now, fetcher=lambda: posts, caller=lambda p: FAKE_INSIGHT)
    res = AN.run_analytics(now=now + timedelta(hours=1), fetcher=lambda: posts, caller=lambda p: FAKE_INSIGHT)
    assert res == {"skipped": "cadence"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_analytics.py`
Expected: FAIL — `AttributeError: module 'analytics' has no attribute 'run_analytics'`

- [ ] **Step 3: Write minimal implementation**

Append to `analytics.py`:

```python
def build_insight_prompt(report: dict) -> str:
    def cards(items):
        return "\n".join(
            f"- [{c['kind']}] eng_rate={c['eng_rate']} views={c['views']} :: {c['text']}"
            for c in items) or "(none)"
    kw = ", ".join(f"{k['token']}(x{k['lift']})" for k in report["keywords"][:12]) or "(none)"
    return (
        "You analyze what's working on an X (Twitter) account. Engagement rate = "
        "(likes+rts+replies+quotes+bookmarks)/views.\n\n"
        f"## Best performers\n{cards(report['top'])}\n\n"
        f"## Worst performers\n{cards(report['bottom'])}\n\n"
        f"## High-lift keywords\n{kw}\n\n"
        f"## Format/timing breakdowns (avg_eng_rate, count)\n"
        f"{json.dumps(report['breakdowns'], ensure_ascii=False)}\n\n"
        "## Task\nReturn JSON ONLY (no fences, start with `{` end with `}`):\n"
        "{\n"
        '  "themes_working": ["<themes/topics that resonate; 0-5>"],\n'
        '  "themes_flat": ["<themes that fall flat; 0-5>"],\n'
        '  "timing_insight": "<1-2 sentences on best posting times>",\n'
        '  "format_insight": "<1-2 sentences on post type/length/media/link>",\n'
        '  "recommendations": ["<concrete next-post suggestions; 2-5>"]\n'
        "}"
    )


def _default_caller(prompt: str):
    import pipeline
    return pipeline._claude_json(prompt, timeout=300, label="analytics")


def load_report() -> dict:
    try:
        return json.loads(REPORT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _should_run(now: datetime):
    rep = load_report()
    last = _parse_iso(rep.get("generated_at")) if rep else None
    if last and (now - last) < timedelta(hours=CADENCE_HOURS):
        return False, "cadence"
    return True, ""


def run_analytics(force=False, now=None, fetcher=None, caller=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not force:
        ok, reason = _should_run(now)
        if not ok:
            return {"skipped": reason}
    caller = caller or _default_caller
    hist = snapshot_metrics(now, fetcher=fetcher)
    report = compute_report(hist, now)
    try:
        report["insights"] = caller(build_insight_prompt(report)) or None
    except Exception:
        report["insights"] = None
    report["ts"] = now.isoformat()
    report["generated_at"] = now.isoformat()
    report["window_days"] = WINDOW_DAYS
    _atomic_write_json(REPORT_PATH, report)
    return report


def overview() -> dict:
    return load_report()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_analytics.py`
Expected: all `ok`, exit 0. Also run the full suite: `python3 -m pytest tests/test_analytics.py -q` → passes.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): run_analytics orchestration + LLM insights + report I/O"
```

---

### Task 5: Server routes `/analytics` and `/analytics/run`

**Files:**
- Modify: `server.py` (add `import analytics`; add a GET branch near the `/evals` branch ~line 741; add a POST branch near `/evals/revert` ~line 914)
- Test: manual (curl) — the server has no unit-test harness.

**Interfaces:**
- Consumes: `analytics.overview()`, `analytics.run_analytics(force=True)`.
- Produces: `GET /analytics` → latest report JSON; `POST /analytics/run` → fresh report JSON.

- [ ] **Step 1: Add the import**

At the top of `server.py`, alongside the existing `import eval_engine`:

```python
import analytics
```

(Place it next to the other local-module imports — find `import eval_engine` and add `import analytics` directly below it.)

- [ ] **Step 2: Add the GET route**

In `do_GET`, immediately after the `if path == "/evals":` block (ends ~line 745), add:

```python
        if path == "/analytics":
            try:
                return self._send_json(200, analytics.overview())
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
```

- [ ] **Step 3: Add the POST route**

In `do_POST`, immediately after the `if path == "/evals/revert":` block (~line 914), add:

```python
        if path == "/analytics/run":
            try:
                return self._send_json(200, analytics.run_analytics(force=True))
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
```

- [ ] **Step 4: Verify routes respond**

Run:
```bash
~/.claude/skills/yagyesh-dashboard/run.sh &   # or rely on the always-on daemon
sleep 2
curl -s -X POST http://127.0.0.1:7873/analytics/run | python3 -m json.tool | head -20
curl -s http://127.0.0.1:7873/analytics | python3 -m json.tool | head -20
```
Expected: `/analytics/run` returns a report with `n_posts`, `breakdowns`, `top`, `insights`; `/analytics` returns the same persisted report. (If `n_posts` is 0 on first run that's fine — it builds history over days; re-running tomorrow accumulates snapshots.)

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(analytics): /analytics + /analytics/run routes"
```

---

### Task 6: Daily pipeline hook

**Files:**
- Modify: `pipeline.py` (in `main()`, next to the `import eval_engine; ev = eval_engine.run_eval()` block ~lines 872-883)

**Interfaces:**
- Consumes: `analytics.run_analytics()` (daily-guarded, non-fatal).
- Produces: a refreshed `data/analytics.json` on each pipeline run (at most one snapshot/day).

- [ ] **Step 1: Add the hook**

Immediately after the existing eval `try/except` block in `pipeline.py` `main()`, add:

```python
    try:
        import analytics
        an = analytics.run_analytics()
        if an.get("skipped"):
            print(f"[pipeline] analytics skipped ({an['skipped']})", flush=True)
        else:
            print(f"[pipeline] analytics ran (n_posts={an.get('n_posts')})", flush=True)
    except Exception as e:
        sys.stderr.write(f"[pipeline] analytics failed (non-fatal): {e}\n")
```

- [ ] **Step 2: Verify the pipeline still runs**

Run: `python3 -c "import pipeline, analytics; print('imports ok')"`
Expected: `imports ok` (no circular-import error — `analytics` imports `pipeline` lazily inside functions).

- [ ] **Step 3: Commit**

```bash
git add pipeline.py
git commit -m "feat(analytics): daily-guarded analytics run in pipeline"
```

---

### Task 7: `13 analytics` front-end screen

**Files:**
- Modify: `static/index.html` (add nav item after the `12 evals` item ~line 69-72; add a `<section id="analytics">` after the evals section)
- Modify: `static/app.js` (add `analytics` to the screen-title map ~line 60; add a `loadAnalytics()` call in the nav switch ~line 207; implement `loadAnalytics()` near `loadEvals()` ~line 1115)
- Test: manual (browser).

**Interfaces:**
- Consumes: `GET /analytics`, `POST /analytics/run`.
- Produces: the rendered analytics screen.

- [ ] **Step 1: Add the nav item**

In `static/index.html`, after the `12 evals` nav anchor (~line 72), add:

```html
        <a href="#analytics" class="nav-item" data-section="analytics">
          <span class="nav-key">13</span><span class="nav-name">analytics</span>
          <span class="nav-count" data-count="analytics">—</span>
        </a>
```

- [ ] **Step 2: Add the section container**

In `static/index.html`, after the evals `<section>` closes, add:

```html
      <section id="analytics" class="screen" hidden>
        <div class="screen-head">
          <button id="analytics-run" class="btn">run analysis now</button>
          <span id="analytics-meta" class="muted"></span>
        </div>
        <div id="analytics-body"></div>
      </section>
```

(Match the exact `class`/markup conventions of the neighboring `#evals` section — open `index.html`, copy its wrapper structure, and adapt the ids above.)

- [ ] **Step 3: Register the screen title + loader**

In `static/app.js`, add to the screen-title map (next to the `evals:` entry ~line 60):

```javascript
  analytics: { title: "analytics", sub: "what's working on X — by engagement rate, format, timing, keywords" },
```

In the nav switch (next to `if (name === "evals") loadEvals();` ~line 207):

```javascript
  if (name === "analytics") loadAnalytics();
```

- [ ] **Step 4: Implement `loadAnalytics()`**

In `static/app.js`, near `loadEvals()` (~line 1115), add (uses the existing `$`, `el` helpers already used by `loadEvals`):

```javascript
async function loadAnalytics() {
  const meta = $("#analytics-meta");
  const body = $("#analytics-body");
  body.textContent = "loading…";
  let d;
  try { d = await (await fetch("/analytics")).json(); }
  catch { body.textContent = "failed to load analytics."; return; }

  if (!d || !d.generated_at) {
    body.textContent = "no analysis yet — click “run analysis now”. (History builds up daily.)";
    meta.textContent = "";
    return;
  }
  meta.textContent = `${d.n_posts} posts · ${d.window_days}d window · updated ${new Date(d.generated_at).toLocaleString()} · v1 excludes replies`;
  body.innerHTML = "";

  const ins = d.insights;
  if (ins) {
    const box = el("div", { class: "evals-state" });
    box.appendChild(el("h4", { class: "evals-h" }, "what's working"));
    if (ins.themes_working?.length) box.appendChild(el("p", {}, "✅ themes working: " + ins.themes_working.join(", ")));
    if (ins.themes_flat?.length)    box.appendChild(el("p", {}, "⬇️ falls flat: " + ins.themes_flat.join(", ")));
    if (ins.timing_insight) box.appendChild(el("p", {}, "🕒 " + ins.timing_insight));
    if (ins.format_insight) box.appendChild(el("p", {}, "✍️ " + ins.format_insight));
    (ins.recommendations || []).forEach(r => box.appendChild(el("p", {}, "→ " + r)));
    body.appendChild(box);
  }

  const bd = (title, obj) => {
    const box = el("div", { class: "evals-state" });
    box.appendChild(el("h4", { class: "evals-h" }, title));
    Object.entries(obj).sort((a, b) => b[1].avg_eng_rate - a[1].avg_eng_rate)
      .forEach(([k, v]) => box.appendChild(
        el("p", {}, `${k}: ${(v.avg_eng_rate * 100).toFixed(1)}% eng · ${Math.round(v.avg_views)} views · n=${v.count}`)));
    body.appendChild(box);
  };
  bd("by type", d.breakdowns.type);
  bd("by format (media)", d.breakdowns.media);
  bd("by length", d.breakdowns.length);
  bd("best hours", d.breakdowns.hour);
  bd("best weekdays", d.breakdowns.weekday);

  if (d.keywords?.length) {
    const box = el("div", { class: "evals-state" });
    box.appendChild(el("h4", { class: "evals-h" }, "keywords working"));
    d.keywords.slice(0, 15).forEach(k =>
      box.appendChild(el("p", {}, `${k.token} — ${k.lift}× lift (n=${k.support})`)));
    body.appendChild(box);
  }

  const cards = (title, arr) => {
    const box = el("div", { class: "evals-state" });
    box.appendChild(el("h4", { class: "evals-h" }, title));
    (arr || []).forEach(c => {
      const p = el("p", {}, `${(c.eng_rate * 100).toFixed(1)}% · ${c.views} views · ${c.text.slice(0, 120)}`);
      box.appendChild(p);
    });
    body.appendChild(box);
  };
  cards("top posts", d.top);
  cards("bottom posts", d.bottom);
}

document.getElementById("analytics-run")?.addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true; btn.textContent = "analyzing…";
  try { await fetch("/analytics/run", { method: "POST" }); await loadAnalytics(); }
  finally { btn.disabled = false; btn.textContent = "run analysis now"; }
});
```

- [ ] **Step 5: Manual verification in the browser**

Run the server (`run.sh` or daemon), open `http://127.0.0.1:7873`, click `13 analytics`, then **run analysis now**. Confirm: the meta line shows post count + window + "v1 excludes replies"; breakdown blocks render; clicking the button re-runs without error. (Empty state is acceptable on day one — history accrues over subsequent daily runs.)

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat(analytics): 13 analytics screen (insights, breakdowns, keywords, top/bottom)"
```

---

## Self-Review

**Spec coverage:**
- Engagement-rate metric → `eng_rate` (Task 1) ✓
- Originals + quotes, replies/retweets excluded → `snapshot_metrics` filters (Task 2) ✓
- Daily snapshots + trajectory → `snapshot_metrics` per-day dedupe + `snapshots[]` (Task 2) ✓
- 30-day window → `WINDOW_DAYS`, enforced in snapshot + compute (Tasks 2, 3) ✓
- Deterministic breakdowns (type/media/link/length/hour/weekday) + keyword lift + top/bottom → `compute_report` (Task 3) ✓
- LLM narrative (themes/timing/format/recommendations) → `build_insight_prompt` + `run_analytics` (Task 4) ✓
- Manual refresh → `POST /analytics/run` + button (Tasks 5, 7) ✓
- Daily auto-refresh → pipeline hook, daily-guarded; daemon already runs pipeline twice daily (Task 6) ✓
- `13 analytics` screen with all sections → Task 7 ✓
- Surfaced limits (replies excluded, small-sample counts) → meta line + per-bucket `n=` counts (Tasks 3, 7) ✓
- Tests with injected fetcher/caller, no network/LLM → Tasks 1-4 ✓

**Placeholder scan:** none — every code step contains full code; no TBD/TODO/"handle errors" placeholders.

**Type consistency:** `eng_rate`, `snapshot_metrics`, `compute_report`, `run_analytics`, `overview`, `load_report` names are used identically across tasks; report keys (`n_posts`, `breakdowns.type/media/link/length/hour/weekday`, `keywords[].lift/support`, `top`/`bottom[].eng_rate`, `insights`) match between Task 3/4 producers and the Task 7 consumer. History entry keys (`created_at`, `created_local`, `kind`, `snapshots[]`) match between Task 2 writer and Task 3 reader.
