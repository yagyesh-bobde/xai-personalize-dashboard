# Analytics-Informed Post Drafting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed the real-engagement insights `analytics.py` already produces into the original-posts drafting prompt, separate from the keep/discard taste loop.

**Architecture:** Add a pure `analytics.format_for_prompt(report)` that renders a compact "what's working" block (or `""`), mirror it with a best-effort `pipeline._performance_state()` reader, and inject the block into `_posts_prompt` only — leaving `_voice_header`, `_replies_prompt`, and `_quotes_prompt` untouched.

**Tech Stack:** Python 3, stdlib only. Tests follow the repo convention: importable by pytest AND runnable directly via a `__main__` test-runner block (no pytest dependency).

## Global Constraints

- No new LLM call, no new state file — pure read of the existing `data/analytics.json`.
- The block is **posts-only**. `_replies_prompt` / `_quotes_prompt` / `_voice_header` must NOT change behavior.
- `format_for_prompt` returns `""` (never raises) on empty/missing report or `None` insights; `_performance_state()` wraps it in try/except → `""`, mirroring `_learned_state()`.
- Exclude `timing_insight` from the rendered block (it's about *when*, not *what*, to write).
- "Topics that overperform" line is built from `report["keywords"]`, keeping only `lift > 1.0`, capped at 8, formatted `token(x{lift})`; omit the line if none qualify.
- Tests: every test file stays runnable both under pytest and directly (`python3 tests/test_x.py`) via the existing `if __name__ == "__main__"` runner pattern.

---

### Task 1: `analytics.format_for_prompt(report)`

**Files:**
- Modify: `analytics.py` (add `format_for_prompt` near `build_insight_prompt`/`load_report`)
- Test: `tests/test_analytics.py` (add tests + ensure they run under the existing `__main__` runner)

**Interfaces:**
- Consumes: a report dict shaped like `compute_report(...)` output plus an `insights` key, e.g.
  `{"window_days": 30, "keywords": [{"token": "agents", "lift": 1.8, ...}], "insights": {"themes_working": [...], "themes_flat": [...], "format_insight": "...", "recommendations": [...], "timing_insight": "..."}}`.
- Produces: `analytics.format_for_prompt(report: dict) -> str` — a block ending in a single trailing `\n`, or `""`. Consumed by `pipeline._performance_state()` in Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_analytics.py` (the file already does `import analytics as AN`):

```python
def test_format_for_prompt_full():
    report = {
        "window_days": 30,
        "keywords": [
            {"token": "agents", "lift": 1.8, "support": 4, "avg_eng_rate": 0.1},
            {"token": "metro", "lift": 1.2, "support": 3, "avg_eng_rate": 0.07},
            {"token": "memes", "lift": 0.5, "support": 3, "avg_eng_rate": 0.02},
        ],
        "insights": {
            "themes_working": ["agent tooling", "shipping solo"],
            "themes_flat": ["memes"],
            "timing_insight": "evenings win",
            "format_insight": "short text-only wins",
            "recommendations": ["post more agent content"],
        },
    }
    out = AN.format_for_prompt(report)
    assert "WHAT'S ACTUALLY WORKING ON X" in out
    assert "30d" in out
    assert "agent tooling, shipping solo" in out          # themes_working joined
    assert "memes" in out                                 # themes_flat
    assert "short text-only wins" in out                  # format_insight
    assert "agents(x1.8)" in out and "metro(x1.2)" in out # high-lift keywords
    assert "memes(x0.5)" not in out                       # lift <= 1.0 dropped
    assert "post more agent content" in out               # recommendations
    assert "evenings win" not in out                      # timing_insight excluded
    assert out.endswith("\n")


def test_format_for_prompt_no_insights():
    assert AN.format_for_prompt({"keywords": [], "insights": None}) == ""


def test_format_for_prompt_empty_report():
    assert AN.format_for_prompt({}) == ""


def test_format_for_prompt_all_fields_empty():
    report = {"keywords": [{"token": "x", "lift": 0.9}],
              "insights": {"themes_working": [], "themes_flat": [],
                           "format_insight": "", "recommendations": []}}
    assert AN.format_for_prompt(report) == ""


def test_format_for_prompt_omits_keyword_line_when_no_lift():
    report = {"keywords": [{"token": "x", "lift": 0.9}],
              "insights": {"themes_working": ["agent tooling"], "themes_flat": [],
                           "format_insight": "", "recommendations": []}}
    out = AN.format_for_prompt(report)
    assert "agent tooling" in out
    assert "Topics that overperform" not in out
    assert out.endswith("\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_analytics.py -k format_for_prompt -v`
Expected: FAIL — `AttributeError: module 'analytics' has no attribute 'format_for_prompt'`

- [ ] **Step 3: Implement `format_for_prompt`**

Add to `analytics.py` (e.g. right after `build_insight_prompt`):

```python
def format_for_prompt(report: dict) -> str:
    """Render analytics.json into a compact 'what's working' block for the
    posts prompt, or '' when there's nothing useful. Never raises."""
    report = report or {}
    insights = report.get("insights") or {}
    if not insights:
        return ""

    def joined(key):
        vals = [str(v).strip() for v in (insights.get(key) or []) if str(v).strip()]
        return ", ".join(vals)

    working = joined("themes_working")
    flat = joined("themes_flat")
    fmt = (insights.get("format_insight") or "").strip()
    recs = joined("recommendations")

    kws = []
    for k in (report.get("keywords") or []):
        lift = k.get("lift") or 0
        if lift > 1.0:
            kws.append(f"{k['token']}(x{lift})")
        if len(kws) >= 8:
            break

    lines = []
    if working:
        lines.append(f"Themes that resonate: {working}")
    if flat:
        lines.append(f"Themes that fall flat: {flat}")
    if fmt:
        lines.append(f"Format: {fmt}")
    if kws:
        lines.append("Topics that overperform: " + ", ".join(kws))
    if recs:
        lines.append(f"Lean into: {recs}")
    if not lines:
        return ""

    window = report.get("window_days", WINDOW_DAYS)
    header = f"## WHAT'S ACTUALLY WORKING ON X (from real engagement, last {window}d)"
    return header + "\n" + "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_analytics.py -k format_for_prompt -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the whole analytics suite (pytest + direct runner) to confirm no regression**

Run: `python3 -m pytest tests/test_analytics.py -v && python3 tests/test_analytics.py`
Expected: all PASS / all `ok` lines, exit 0

- [ ] **Step 6: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): format_for_prompt renders a what's-working block

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire the block into `_posts_prompt` only

**Files:**
- Modify: `pipeline.py` — add top-level `import analytics` (after `import voice_state`, line ~25), add `_performance_state()` (next to `_learned_state` at line ~434), inject into `_posts_prompt` (line ~458)
- Test: `tests/test_pipeline_variety.py` (add wiring tests; file already does `import pipeline as P`)

**Interfaces:**
- Consumes: `analytics.format_for_prompt(report)` from Task 1; `analytics.load_report()` (existing).
- Produces: `pipeline._performance_state() -> str`. Injected text appears in `_posts_prompt(...)` output, absent from `_replies_prompt(...)` / `_quotes_prompt(...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline_variety.py` (uses `monkeypatch`-free stubbing via `P.analytics`, so keep it dependency-light):

```python
_SIG = {"top_keywords": ["agents"], "top_accounts": ["@x"]}
_MINE = [{"text": "shipped a thing"}]


def _stub_report(P, report):
    """Point pipeline.analytics.load_report at a fixed report for one test."""
    import analytics
    orig = analytics.load_report
    analytics.load_report = lambda: report
    return orig


def test_posts_prompt_includes_performance_block():
    import analytics
    orig = _stub_report(P, {
        "window_days": 30,
        "keywords": [{"token": "agents", "lift": 1.8}],
        "insights": {"themes_working": ["agent tooling"], "themes_flat": [],
                     "format_insight": "short wins", "recommendations": ["post agents"]},
    })
    try:
        out = P._posts_prompt(_SIG, _MINE, [], 3)
        assert "WHAT'S ACTUALLY WORKING ON X" in out
        assert "agent tooling" in out
    finally:
        analytics.load_report = orig


def test_replies_and_quotes_omit_performance_block():
    import analytics
    orig = _stub_report(P, {
        "window_days": 30, "keywords": [],
        "insights": {"themes_working": ["agent tooling"], "themes_flat": [],
                     "format_insight": "short wins", "recommendations": []},
    })
    chunk = [{"id": "t1", "author": "@a", "text": "hello world"}]
    try:
        assert "WHAT'S ACTUALLY WORKING ON X" not in P._replies_prompt(_SIG, _MINE, chunk)
        assert "WHAT'S ACTUALLY WORKING ON X" not in P._quotes_prompt(_SIG, _MINE, chunk)
    finally:
        analytics.load_report = orig


def test_posts_prompt_omits_block_when_report_empty():
    import analytics
    orig = _stub_report(P, {})
    try:
        out = P._posts_prompt(_SIG, _MINE, [], 3)
        assert "WHAT'S ACTUALLY WORKING ON X" not in out
        assert "## Task" in out          # still a valid prompt
    finally:
        analytics.load_report = orig
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_pipeline_variety.py -k "performance or omit" -v`
Expected: FAIL — `_posts_prompt` output lacks the header (block not wired yet)

- [ ] **Step 3: Add the top-level import**

In `pipeline.py`, after `import voice_state` (line ~25), add:

```python
import analytics
```

(Safe: `analytics.py` only imports `pipeline` lazily inside functions, so there is no import cycle at module load — same pattern as the existing top-level `import voice_state`.)

- [ ] **Step 4: Add `_performance_state()`**

In `pipeline.py`, directly after `_learned_state()` (line ~434), add:

```python
def _performance_state() -> str:
    """Formatted 'what's working' block from data/analytics.json (or '')."""
    try:
        return analytics.format_for_prompt(analytics.load_report())
    except Exception:
        return ""
```

- [ ] **Step 5: Inject into `_posts_prompt`**

In `_posts_prompt` (line ~458), after computing `lane_block` and before the return, build the perf block and insert it right after the voice header. Replace the existing `return (` body so it reads:

```python
    perf = _performance_state()
    perf_block = (perf + "\n") if perf else ""
    return (
        _voice_header(sig, mine)
        + perf_block
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
```

(Only `perf`/`perf_block` lines and the `+ perf_block` insertion are new; the rest is the existing body, repeated here so the edit is unambiguous.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_pipeline_variety.py -k "performance or omit" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full pipeline-variety + analytics suites (no regression)**

Run: `python3 -m pytest tests/test_pipeline_variety.py tests/test_analytics.py -v && python3 tests/test_pipeline_variety.py`
Expected: all PASS / all `ok`, exit 0

- [ ] **Step 8: Commit**

```bash
git add pipeline.py tests/test_pipeline_variety.py
git commit -m "feat(pipeline): inject analytics what's-working block into posts prompt

Posts-only; replies/quotes and the shared voice header are untouched.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- `analytics.format_for_prompt` + empty/None/all-empty/keyword-omit guards → Task 1 (all 5 cases tested).
- `pipeline._performance_state()` mirroring `_learned_state()` → Task 2 Step 4.
- Top-level `import analytics`, cycle-safety noted → Task 2 Step 3.
- Inject posts-only, replies/quotes/header unchanged → Task 2 Step 5 + tests in Step 1.
- `timing_insight` excluded → Task 1 test asserts `"evenings win" not in out`.
- Keyword line: lift > 1.0, cap 8, `token(x{lift})`, omit when none → Task 1 impl + `test_format_for_prompt_omits_keyword_line_when_no_lift`.
- Tests runnable under pytest and direct runner → existing `__main__` block in both files covers added tests; Task 1 Step 5 / Task 2 Step 7 run both.
- No new LLM call / state file → confirmed, pure read of `analytics.json`.

**Placeholder scan:** none — every code/test step shows complete content.

**Type consistency:** `format_for_prompt(report: dict) -> str` and `_performance_state() -> str` used identically across both tasks; header string `"## WHAT'S ACTUALLY WORKING ON X"` asserted verbatim in both test files.
