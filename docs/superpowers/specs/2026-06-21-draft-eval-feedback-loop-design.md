# Draft eval + feedback loop — design

Date: 2026-06-21
Branch: feat/linkedin-automation (X/Twitter dashboard work)

## Problem

The X/Twitter draft pipeline has three gaps:

1. The voice prompt produces drafts that read off-voice/generic, repetitive, and low-value.
2. Reply/quote draft volume is too low (100 each).
3. There is no feedback loop: `discard` is cosmetic only, there is no way to record a draft posted manually, and nothing learns from what the user keeps vs. throws away.

The user wants a closed loop: discarded drafts define what to avoid; kept / edited / manually-posted drafts define what good looks like; a daily eval reads both and **automatically** tunes the voice prompt; an eval-metrics screen shows each run's conclusion and the changes it made.

## Decisions (locked)

- **Volume:** generate **100 posts, 300 replies, 300 quotes** per refresh (count of drafts, not char length).
- **Eval autonomy:** **fully automatic** — the eval edits the learned voice state itself each run and logs the diff. No approval gate.
- **Eval cadence:** **scheduled daily** — guarded inside the pipeline (at most once per 24h, before drafting). Plus a manual "Run eval now" button for testing.
- **Where learned changes live:** Approach **A** — a machine-managed **data file** (`data/voice_state.json`) that the pipeline injects into the prompt. The eval NEVER edits `pipeline.py` source or the shared `voice.md` agent file. Safe, diffable, reversible, gitignored.
- **What's wrong today (eval focus):** off-voice/generic, repetitive, low-value.

## Architecture

```
draft cards ──(discard / mark-posted / like / post, with edit delta)──▶ data/feedback.json
                                                                              │
                                          daily guard (≤1/24h, ≥threshold)    │
pipeline refresh ──▶ eval.py ──reads good+bad+current state──▶ claude ──▶ JSON │
                        │                                                      │
                        ├─▶ writes data/voice_state.json  (learned gold/anti/rules, capped)
                        └─▶ appends data/evals.json        (conclusion + diff per run)
                                                                              │
pipeline drafting ──▶ _voice_header injects voice_state.json into every prompt
                                                                              │
"12 evals" screen ◀── /evals, /feedback summary, /evals/revert, /eval/run ────┘
```

## Components

### 1. Draft volume (`pipeline.py`)

- `REPLIES_TARGET` default `100 → 300`, `QUOTES_TARGET` default `100 → 300`. `POSTS_TARGET` unchanged at 100.
- Env overrides (`DASHBOARD_REPLIES`, `DASHBOARD_QUOTES`) still win.
- Existing per-kind parallel batching handles the larger volume; no new batching logic. Refresh will take longer — acceptable.

### 2. Feedback store (`data/feedback.json`)

Append-only JSON list. One event per user action on a draft card.

Event shape:
```json
{
  "ts": "2026-06-21T20:30:00Z",
  "kind": "post | reply | quote",
  "signal": "good | bad",
  "action": "discard | mark_posted | like | post",
  "original_text": "<the draft as generated>",
  "final_text": "<text at action time; differs from original_text iff edited>",
  "edited": true,
  "target_author": "<@handle or null>",
  "target_text": "<first 80 chars of target or null>"
}
```

Signal mapping:
- `discard` → **bad**
- `mark_posted` (manual, no CLI call) → **good**
- `like` (keep, not posting now) → **good**
- `post` (CLI post, already wired) → **good** (now also logged here)
- Edits: any good action where `final_text != original_text` sets `edited:true`; the delta is signal for the eval (what the user fixes).

### 3. Card actions (`static/app.js`, `index.html`, `style.css`)

Each draft card's action row gains:
- **mark as posted** ✓ — greys the card (`posted` state), POSTs a `mark_posted` feedback event. No `twitter` CLI call.
- **like** 👍 — lightweight "good, not posting" — marks card kept, POSTs a `like` feedback event.
- Existing **discard** — now also POSTs a `discard` (bad) feedback event in addition to the CSS state.
- Existing **post now** — on success, also POSTs a `post` (good) feedback event.
- Edit capture: the card already holds the generated `draft.text` as `original_text`; on any feedback action, `final_text` = current textarea value, `edited` = (final != original).

Cards already track `kind`, `target_id`, target author/text — reuse those.

### 4. Eval engine (`eval.py`, new)

`run_eval(force=False) -> dict` :

1. **Guard** (skipped when `force=True`): load `data/evals.json`; if last run < 24h ago, return `{skipped:"cadence"}`. Count feedback events since last run; if `< THRESHOLD` (default 5 bad **or** 5 good new events; env `EVAL_MIN_EVENTS`), return `{skipped:"insufficient"}`.
2. Gather recent **good** examples (kept/edited/posted, prefer edited deltas) and recent **bad** examples (discarded), capped (e.g. last ~40 each).
3. Load current `data/voice_state.json`.
4. One `claude` call (reuse `_claude_json` from `pipeline.py`) with a prompt that asks it to contrast good vs discarded and return:
   ```json
   {
     "conclusion": "<plain-english: what separates kept from discarded>",
     "gold_examples_to_add": ["<best kept/edited drafts to use as exemplars>"],
     "anti_examples_to_add": ["<discarded drafts to explicitly avoid>"],
     "rule_adjustments": ["<short additive/edited voice-rule lines>"]
   }
   ```
5. **Apply automatically:** merge into `voice_state.json` — append new gold/anti/rules, dedupe, cap each list to last N (default 20 gold, 20 anti, 12 rules). Write the file.
6. **Log:** append a run record to `data/evals.json`:
   ```json
   {
     "id": "<ts-based id>",
     "ts": "...",
     "conclusion": "...",
     "added": {"gold": [...], "anti": [...], "rules": [...]},
     "counts": {"good": N, "bad": M, "since_last": K},
     "state_before": { ...snapshot for revert... }
   }
   ```
   `state_before` enables per-run revert.

`revert_eval(id)`: restore `voice_state.json` to that run's `state_before` and mark the run reverted in `evals.json`.

### 5. Pipeline injection (`pipeline.py` `_voice_header`)

Read `data/voice_state.json` (empty/absent → no-op, current behavior). Append to the prompt header:
- `## LEARNED — drafts you've kept (match this)` → gold list
- `## LEARNED — drafts rejected, do NOT write like this` → anti list
- `## LEARNED — extra voice rules` → rule_adjustments

This layers on top of the static `GOLD_EXAMPLES` / `VOICE_RULES`; it never replaces them.

### 6. Daily guard hook (`pipeline.py`)

At the start of a refresh, before drafting, call `eval.run_eval()` (non-forced). It self-guards on cadence + threshold, so calling every refresh is safe and yields "scheduled daily" behavior without a new daemon. Eval failure must not break a refresh — wrap in try/except and log.

### 7. Server routes (`server.py`)

- `POST /feedback` — append one event to `data/feedback.json`. Body = event minus `ts` (server stamps).
- `GET /evals` — returns `{runs:[...], summary:{good,bad,by_kind,since_last}, state:voice_state}`.
- `POST /eval/run` — `run_eval(force=True)`; returns the run record or skip reason.
- `POST /evals/revert` — body `{id}`; reverts that run.

### 8. Eval metrics screen (`12 evals`)

New nav entry following the numbered convention. Three sections:
- **Feedback summary** — good vs discarded counts, split by kind; new-since-last-eval and all-time. **Run eval now** button (→ `/eval/run`).
- **Eval run history** — newest first: date, `conclusion`, the `added` changes (gold/anti/rules), each with **revert this run** (→ `/evals/revert`).
- **Current learned state** — the active gold + anti-examples + extra rules the prompt is using right now (from `voice_state`).

## Data files (all gitignored under `data/`)

- `data/feedback.json` — append-only event log
- `data/voice_state.json` — machine-managed learned state injected into prompts
- `data/evals.json` — eval run history with revert snapshots

## Error handling

- Eval claude call failure / bad JSON: log, skip this run, do not write state, never break refresh.
- Missing data files: treat as empty (preserves current behavior on first run).
- `/feedback` failures are non-fatal to the UI: card state still updates; feedback POST is best-effort with a toast on failure.
- Capping every learned list prevents unbounded prompt growth.

## Testing

- Unit: `eval.py` guard logic (cadence, threshold), merge+cap, revert restores prior state. Extend `tests/` (there is `tests/test_pipeline_variety.py`).
- Unit: feedback signal mapping (each action → correct signal, edit delta detection).
- Manual: discard/like/mark-posted/post each write a correct feedback event; `Run eval now` produces a run record + updates `voice_state.json`; next refresh's prompt contains the learned blocks; revert restores state and updates the screen.

## Out of scope (YAGNI)

- No approval/human-in-loop gate (user chose fully automatic).
- No editing of `pipeline.py` source or `voice.md` by the eval.
- No new launchd daemon (daily cadence rides the existing refresh path / refresh daemon).
- LinkedIn drafts unchanged — this is X/Twitter only.
