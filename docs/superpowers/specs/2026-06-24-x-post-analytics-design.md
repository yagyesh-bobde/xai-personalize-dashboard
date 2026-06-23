# X Post Analytics — Design

**Date:** 2026-06-24
**Status:** Approved (design); pending spec review
**Screen:** `13 analytics`

## Goal

Analyze which of Yagyesh's X posts are working and *why* — what kinds of
posts/quotes resonate, which keywords/themes carry traction, and what timing
gets the most reach. Refreshable manually (a button) and automatically daily
(via the existing refresh daemon). "Thorough": deterministic stats + an
LLM-written narrative on top.

## Decisions (locked)

- **Traction metric:** engagement rate = `(likes+rts+replies+quotes+bookmarks) / max(views,1)`.
  Absolute `views` shown alongside as "reach".
- **Scope (v1):** original posts + quote-tweets. Replies are **excluded** in v1
  (they don't appear in the posts timeline; would need per-id lookups — deferred).
- **Analysis engine:** deterministic breakdowns **plus** an LLM thematic narrative.
- **History model:** daily metric snapshots per post → trajectory + fair-age comparison.
- **Window:** rolling **30 days**.

## Data foundation & known limits

- **Source:** `twitter user-posts <handle> --json -n <N>` returns per-post
  `metrics{likes,retweets,replies,quotes,views,bookmarks}`, `createdAtISO`,
  `media[]`, `lang`, `isRetweet`.
- **Attribution:** join against `posted.json` by `tweet_id` to attach `kind`
  (post/quote) and `source` (dashboard vs manual). When a tweet isn't in
  `posted.json`, classify as `original` unless a quoted-status signal is present.
- **Limits surfaced in the UI (not hidden):**
  - Replies excluded in v1.
  - Quote-vs-original inferred when not in `posted.json`.
  - Retweets (`isRetweet`) skipped.
  - Views may be 0/None on very fresh posts → division guarded by `max(views,1)`.
  - Small buckets flagged with their `count`; keyword lift requires min support ≥3.

## Architecture

Mirrors the existing `eval_engine.py` pattern (engine module + gitignored JSON +
`/route` + numbered screen + daily-guarded pipeline hook + injectable-caller tests).

### `analytics.py`

Paths (under gitignored `data/`):
- `data/analytics_history.json` — keyed by `tweet_id`:
  ```json
  {
    "<tweet_id>": {
      "created_at": "<ISO>",
      "kind": "post|quote",
      "source": "dashboard|manual|unknown",
      "text": "...",
      "has_media": true,
      "has_link": false,
      "lang": "en",
      "snapshots": [
        {"ts": "<ISO>", "likes": 3, "retweets": 1, "replies": 0,
         "quotes": 0, "views": 592, "bookmarks": 0}
      ]
    }
  }
  ```
- `data/analytics.json` — the latest computed report the UI reads (single object).

Functions:
- `snapshot_metrics(now, fetcher=None)` — fetch `user-posts --json`, filter to
  non-retweets within the window, upsert each into history; append **one snapshot
  per day** per tweet (skip if a snapshot already exists for today). Daily-guarded
  (24h cadence) like the eval. `fetcher` is injectable for tests.
- `compute_report(history, now, window_days=30)` — pure function over history.
  Uses each post's **latest snapshot**. Produces:
  - per-post engagement rate + age_hours,
  - breakdowns: **by type** (post/quote), **by media**, **by link**,
    **by length bucket** (`<100` / `100–200` / `>200` chars),
    **by hour-of-day** (local), **by day-of-week**,
  - **keyword lift**: tokenize (lowercase; strip urls/@handles/stopwords);
    for each token with support ≥3, avg eng-rate of posts containing it vs
    overall; rank by lift,
  - **top performers / bottom performers**: top-N & bottom-N by eng-rate with a
    min-views floor.
  Each breakdown entry carries `{avg_eng_rate, avg_views, count}`.
- `build_insight_prompt(report)` — feeds top vs bottom performers + breakdowns;
  asks for JSON ONLY:
  `{themes_working:[], themes_flat:[], timing_insight, format_insight, recommendations:[]}`.
- LLM call via `_default_caller(prompt)` → `pipeline._claude_json(...)`
  (lazy import to avoid circular import, like `eval_engine`). Daily-guarded;
  on failure the report still ships with deterministic stats and
  `insights: null`.
- `run_analytics(force=False, now=None, fetcher=None, caller=None)` — orchestrate
  snapshot → compute → (guarded) insights → write `analytics.json`
  `{ts, window_days, n_posts, metric, breakdowns, top, bottom, keywords,
  insights, generated_at}`. Returns `{"skipped": reason}` when guarded off.
- `load_report()` / `overview()` — return latest `analytics.json` for the route.

### Server (`server.py`)

- `GET /analytics` → `analytics.overview()` (mirrors `/evals`).
- `POST /analytics/run` → `analytics.run_analytics(force=True)` (mirrors `/eval/run`).

### Pipeline hook (`pipeline.py`)

Next to the existing `eval_engine.run_eval()` block in `main()`:
```python
try:
    import analytics
    analytics.run_analytics()  # daily-guarded; non-fatal
except Exception as e:
    sys.stderr.write(f"[pipeline] analytics failed (non-fatal): {e}\n")
```
The twice-daily refresh daemon keeps the report fresh automatically; the daily
guard ensures at most one snapshot/day.

### Frontend (`static/index.html` + `app.js`)

- Nav item `13 analytics` (`data-section="analytics"`); `loadAnalytics()` on select
  (mirrors `loadEvals()`); section title registered in the screen map.
- Sections:
  1. Header — "run analysis now" button (`POST /analytics/run`) + last-updated
     stamp + window + a one-line "v1 excludes replies" note.
  2. **What's working** — LLM narrative: themes working / flat, timing insight,
     format insight, recommendations.
  3. **Best times** — hour-of-day + day-of-week bars (avg eng-rate, count).
  4. **Format breakdown** — type / media / link / length tables
     (avg eng-rate, avg views, count).
  5. **Keywords working** — ranked by lift with support count.
  6. **Top posts / Bottom posts** — cards with text + metrics + eng-rate,
     linking to the tweet.

## Testing

`tests/test_analytics.py` — pure-function units with an injected fake `fetcher`
and fake `caller` (no network, no LLM), following `tests/test_eval_engine.py`:
- engagement-rate math (incl. `views=0` guard),
- length / hour / weekday bucketing,
- keyword lift + min-support threshold,
- snapshot upsert + same-day dedupe,
- window filtering (drops >30-day-old, drops retweets),
- `compute_report` over a fixture producing expected top/bottom ordering,
- `run_analytics(force=True)` end-to-end with injected fetcher+caller writing a
  well-formed `analytics.json`.

## Out of scope (v1)

- Reply-level analytics (deferred; needs per-id metric lookups).
- Cross-platform (LinkedIn) analytics.
- Editing/acting on posts from this screen (read-only insights).
