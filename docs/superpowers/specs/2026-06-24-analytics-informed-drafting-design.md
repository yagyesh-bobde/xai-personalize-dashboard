# Analytics-informed post drafting — design

**Date:** 2026-06-24
**Status:** Approved, ready for implementation plan

## Problem

The dashboard has two independent learning loops:

1. **Taste loop** — `feedback.py` → `eval_engine.py` → `voice_state.json`,
   injected into every draft prompt via `_learned_state()`. Learns from what
   the user *keeps vs discards*.
2. **Performance loop** — `analytics.py` snapshots real posted-tweet metrics,
   computes breakdowns / keyword-lift / top-bottom, and an LLM produces an
   `insights` block (`themes_working`, `themes_flat`, `timing_insight`,
   `format_insight`, `recommendations`). **Today this is display-only** — it
   feeds the analytics screen but never touches draft generation.

The taste loop learns from *opinion* (keep/discard); the performance loop learns
from *real-world engagement*. Only the first one currently shapes drafts. This
closes that gap: feed the engagement signal back into drafting.

## Goal

Make the real-world engagement insights that `analytics.py` already produces
influence the **original-posts** prompt, kept cleanly separate from the taste
loop.

## Scope decisions (from brainstorming)

- **Integration depth:** Inject insights as a formatted prompt block. No new LLM
  call, no new state file, no biasing of batch theme/keyword *selection* — just
  prompt text. (Rejected: merging into `voice_state` — would conflate "what I
  like" with "what performs" and make reverts harder.)
- **Scope:** **Posts only.** Replies and quotes react to someone else's content,
  so topic/theme guidance doesn't fit them, and analytics v1 excludes replies
  entirely. The block goes into `_posts_prompt`, NOT the shared `_voice_header`.
- **Excluded field:** `timing_insight` — it's about *when* to post, not what to
  write, so it has no place in a draft-text prompt.

## Design

### 1. `analytics.format_for_prompt(report) -> str`

New pure function in `analytics.py`, mirroring `voice_state.format_for_prompt`.

- **Input:** a loaded `analytics.json` report dict (as returned by
  `load_report()` / `compute_report` + `insights`).
- **Output:** a compact prompt block, or `""` when there's nothing useful.
- **Empty/guard cases that return `""`:** report is empty/falsy; `insights` is
  missing or `None`; `insights` present but every rendered field is empty.
- **Rendered shape** (omit any line whose source field is empty):

  ```
  ## WHAT'S ACTUALLY WORKING ON X (from real engagement, last 30d)
  Themes that resonate: <themes_working joined>
  Themes that fall flat: <themes_flat joined>
  Format: <format_insight>
  Topics that overperform: keyword(x<lift>), keyword(x<lift>), ...
  Lean into: <recommendations joined>
  ```

- **"Topics that overperform"** line is built deterministically from
  `report["keywords"]`: take the top high-lift keywords (lift > 1.0), cap at a
  small number (e.g. 8), format as `token(x{lift})`. Skip the line if none
  qualify. This reuses the same keyword-lift data the analytics screen shows; it
  does NOT bias which themes batches draw from (that was an explicitly rejected
  deeper integration).
- `window_days` in the header should read from the report when present, else
  fall back to `WINDOW_DAYS`.

### 2. `pipeline._performance_state() -> str`

Mirror of the existing `_learned_state()`:

```python
def _performance_state() -> str:
    """Formatted 'what's working' block from data/analytics.json (or '')."""
    try:
        return analytics.format_for_prompt(analytics.load_report())
    except Exception:
        return ""
```

Add `import analytics` to pipeline (analytics already imports pipeline lazily
inside functions, so a top-level import here is safe — no import cycle at module
load).

### 3. Inject into `_posts_prompt` only

In `_posts_prompt`, after `_voice_header(sig, mine)` and before the batch's
themes block, append the performance block when non-empty:

```python
perf = _performance_state()
... + (("\n" + perf + "\n") if perf else "") + ...
```

`_voice_header`, `_replies_prompt`, and `_quotes_prompt` are unchanged, so
replies and quotes never see this block.

## Data flow

```
daily analytics run ──► data/analytics.json  (insights + keywords)
                              │
        _performance_state() reads it (best-effort)
                              │
                              ▼
                       _posts_prompt only  ──►  performance-informed posts
```

When analytics hasn't run yet (no file / `insights` is `None`), the block is
`""` and the posts prompt is exactly as it is today.

## Testing

- **`analytics.format_for_prompt` unit tests:**
  - Full report with insights + high-lift keywords → block contains the header,
    each populated line, and `token(x<lift>)` entries.
  - `insights` is `None` → `""`.
  - Empty report `{}` → `""`.
  - Insights present but all fields empty, no qualifying keywords → `""`.
  - Keywords all at/below lift 1.0 → "Topics that overperform" line omitted but
    other lines still render.
- **Pipeline wiring tests:**
  - With a stubbed non-empty `analytics.load_report()`, `_posts_prompt(...)`
    output contains the performance header.
  - `_replies_prompt(...)` and `_quotes_prompt(...)` output does NOT contain it.
  - With `analytics.load_report()` returning `{}`, `_posts_prompt(...)` omits
    the block and still produces a valid prompt.

## Out of scope (YAGNI)

- Biasing batch theme/keyword selection by lift.
- Feeding analytics into the eval / `voice_state`.
- Reply-level analytics (analytics v1 doesn't measure replies).
- Surfacing "drafts are now analytics-informed" in the UI — the analytics screen
  already shows the underlying insights.
