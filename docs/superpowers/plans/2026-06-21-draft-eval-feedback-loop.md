# Draft eval + feedback loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a closed feedback loop to the X/Twitter draft pipeline — capture which drafts the user keeps vs. discards, run a daily automatic eval that tunes a machine-managed voice state injected into the prompt, and surface it all in a new "12 evals" screen. Also bump reply/quote draft volume to 300 each.

**Architecture:** Three new Python modules under the skill root — `voice_state.py` (read/merge/format the learned data file), `feedback.py` (append-only event store + summary), `eval_engine.py` (daily-guarded eval that reads good/bad examples + current state, calls `claude`, and auto-writes the learned state + a logged, revertible run record). The pipeline injects the learned state into every draft prompt and calls the eval guard before drafting. The server exposes `/feedback`, `/evals`, `/eval/run`, `/evals/revert`. The front-end adds card actions (mark-posted, like) with edit-delta capture, and the evals screen.

**Tech Stack:** Python 3.10+ stdlib only (no third-party deps), stdlib `http.server`, vanilla JS/HTML/CSS. Tests run directly via each file's `__main__` harness (`python3 tests/test_X.py`) — **pytest is not installed**.

## Global Constraints

- Python: stdlib only — no new pip dependencies. (Repo runs on system Python 3.10+; current interpreter is 3.14.)
- All new state files live under `data/` which is **gitignored** (`data/` in `.gitignore`, only `data/.gitkeep` tracked). Never commit `data/*.json`.
- The eval NEVER edits `pipeline.py` source or `~/.claude/agents/voice.md`. It only writes `data/voice_state.json` and `data/evals.json`.
- Eval autonomy is **fully automatic** (no approval gate). Eval cadence is **daily** (guarded at ≤1 run / 24h) plus a manual `Run eval now` trigger.
- Path-default pattern for all file I/O helpers: signature `def f(..., path=None)` then `path = path or MODULE_CONSTANT` — so tests can monkeypatch the module constant. (Binding the default at def-time would defeat monkeypatching.)
- File naming: the eval module is `eval_engine.py` (NOT `eval.py`) to avoid confusion with the `eval` builtin.
- Follow existing repo conventions: lowercase log lines prefixed `[pipeline]` / `[server]`, `_send_json(code, payload)` for responses, `load_json`/`save_json` helpers in `server.py`.
- **pytest is NOT installed.** Tests run via the repo's direct `__main__` harness (`python3 tests/test_X.py`), which **skips any test function that takes arguments**. Therefore: NO pytest fixtures (`tmp_path`, `monkeypatch`). Every test is a **zero-argument** `def test_*()`. Use `tempfile.mkdtemp()` for temp paths, pass explicit `path=` args to module functions, and for redirecting a module's default path constant assign it directly (`EE.EVALS_PATH = ...`) or save/restore an attribute in a `try/finally`. Each new test file ends with the same `if __name__ == "__main__":` runner as `tests/test_pipeline_variety.py` (run all `test_*`, print ok/FAIL, `sys.exit(1 if failed else 0)`). Test run command is `python3 tests/test_X.py`, never `pytest`.
- Tests follow `tests/test_pipeline_variety.py` style: module docstring, `sys.path.insert(0, ...)` to import from root, plain `def test_*` functions with `assert`.

---

## File Structure

- Create: `voice_state.py` — load/save/merge/format `data/voice_state.json` (the learned gold/anti/rules injected into prompts).
- Create: `feedback.py` — append/load `data/feedback.json` events + `summarize()`.
- Create: `eval_engine.py` — `run_eval()`, `revert_eval()`, `overview()`, guard + merge + logging to `data/evals.json`.
- Create: `tests/test_voice_state.py`, `tests/test_feedback.py`, `tests/test_eval_engine.py`.
- Modify: `pipeline.py` — bump `REPLIES_TARGET`/`QUOTES_TARGET` defaults to 300, raise feed pull, inject learned state in `_voice_header`, call eval guard in `main()`.
- Modify: `server.py` — `import feedback`, `import eval_engine`; add routes `/feedback`, `/evals`, `/eval/run`, `/evals/revert`.
- Modify: `static/index.html` — nav item `12 evals` + `section-evals` markup.
- Modify: `static/app.js` — card actions (mark-posted, like) + feedback POST on discard/post/mark/like with edit delta; `loadEvals()` + render; `SECTION_META` + routing entry.
- Modify: `static/style.css` — minimal styles for the evals screen + new buttons.

---

## Task 1: Bump reply/quote volume to 300

**Files:**
- Modify: `pipeline.py:59-60` (targets), `pipeline.py:134` (feed pull count)
- Test: `tests/test_pipeline_variety.py` (append one test)

**Interfaces:**
- Produces: module constants `pipeline.REPLIES_TARGET == 300`, `pipeline.QUOTES_TARGET == 300`, `pipeline.POSTS_TARGET == 100` (when env overrides unset).

**Notes:** Actual reply/quote counts are bounded by available unique feed targets (one draft per item, `per_author_cap=2`). Raising the target raises the ceiling; raising the feed pull from 300→500 increases target supply. The feed pull is the slowest fetch (~45-50s at 300) so it will get somewhat slower — acceptable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_variety.py`:
```python
def test_reply_quote_targets_default_to_300():
    # posts unchanged, replies + quotes bumped to 300 (env overrides unset)
    assert P.POSTS_TARGET == 100
    assert P.REPLIES_TARGET == 300
    assert P.QUOTES_TARGET == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_pipeline_variety.py`
Expected: `test_reply_quote_targets_default_to_300` prints `FAIL` — `assert 100 == 300` (current default is 100); overall exit 1.

- [ ] **Step 3: Make the change**

In `pipeline.py` lines 59-60, change the defaults (keep env override):
```python
REPLIES_TARGET = int(os.environ.get("DASHBOARD_REPLIES") or 300)
QUOTES_TARGET  = int(os.environ.get("DASHBOARD_QUOTES")  or 300)
```
In `pipeline.py` line 134, raise the feed pull to supply enough targets:
```python
        "feed":      ["feed", "-n", "500"],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_pipeline_variety.py`
Expected: all `test_*` print `ok` (fixture tests skip), including the new one; exit 0.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline_variety.py
git commit -m "feat(pipeline): bump reply/quote draft targets to 300"
```

---

## Task 2: `voice_state.py` — learned-state data file + prompt formatting

**Files:**
- Create: `voice_state.py`
- Test: `tests/test_voice_state.py`

**Interfaces:**
- Produces:
  - `voice_state.STATE_PATH` (module constant `Path`)
  - `voice_state.CAPS` = `{"gold": 20, "anti": 20, "rules": 12}`
  - `load_state(path=None) -> dict` — returns `{"gold": [...], "anti": [...], "rules": [...]}`, all lists; missing/corrupt file → empty lists.
  - `save_state(state: dict, path=None) -> None`
  - `merge_state(state: dict, *, gold=None, anti=None, rules=None) -> dict` — append new (stripped, deduped, order-preserving), cap each list to `CAPS[key]` keeping the most recent.
  - `format_for_prompt(state: dict) -> str` — `""` when all empty; otherwise the three `## LEARNED — ...` blocks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_state.py`:
```python
"""Unit tests for voice_state.py (learned-state file + prompt formatting).

Run directly (no pytest): python3 tests/test_voice_state.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import voice_state as VS  # noqa: E402


def _tmp():
    return Path(tempfile.mkdtemp()) / "vs.json"


def test_load_missing_returns_empty():
    state = VS.load_state(path=Path(tempfile.mkdtemp()) / "nope.json")
    assert state == {"gold": [], "anti": [], "rules": []}


def test_save_then_load_roundtrip():
    p = _tmp()
    VS.save_state({"gold": ["a"], "anti": ["b"], "rules": ["c"]}, path=p)
    assert VS.load_state(path=p) == {"gold": ["a"], "anti": ["b"], "rules": ["c"]}


def test_merge_dedupes_and_appends():
    state = {"gold": ["a"], "anti": [], "rules": []}
    out = VS.merge_state(state, gold=["a", "  b  ", ""], anti=["x"])
    assert out["gold"] == ["a", "b"]   # "a" deduped, "" dropped, "b" stripped
    assert out["anti"] == ["x"]
    assert out["rules"] == []


def test_merge_caps_to_most_recent():
    state = {"gold": [f"g{i}" for i in range(VS.CAPS["gold"])], "anti": [], "rules": []}
    out = VS.merge_state(state, gold=["newest"])
    assert len(out["gold"]) == VS.CAPS["gold"]
    assert out["gold"][-1] == "newest"   # newest kept
    assert out["gold"][0] == "g1"        # oldest ("g0") dropped


def test_format_empty_is_blank():
    assert VS.format_for_prompt({"gold": [], "anti": [], "rules": []}) == ""


def test_format_includes_blocks():
    out = VS.format_for_prompt({"gold": ["keep me"], "anti": ["avoid me"], "rules": ["be short"]})
    assert "LEARNED — drafts you've kept" in out
    assert "- keep me" in out
    assert "do NOT write like these" in out
    assert "- avoid me" in out
    assert "extra voice rules" in out
    assert "- be short" in out


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_voice_state.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_state'`.

- [ ] **Step 3: Write the implementation**

Create `voice_state.py`:
```python
"""Machine-managed learned voice state injected into draft prompts.

The eval (eval_engine.py) writes this file automatically; pipeline.py reads it
and appends the formatted blocks to every draft prompt. Lives in data/ (gitignored).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "voice_state.json"

KEYS = ("gold", "anti", "rules")
CAPS = {"gold": 20, "anti": 20, "rules": 12}


def _empty() -> dict:
    return {k: [] for k in KEYS}


def load_state(path=None) -> dict:
    path = path or STATE_PATH
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty()
    return {k: list(data.get(k) or []) for k in KEYS}


def save_state(state: dict, path=None) -> None:
    path = path or STATE_PATH
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({k: list(state.get(k) or []) for k in KEYS},
                            indent=2, ensure_ascii=False))


def merge_state(state: dict, *, gold=None, anti=None, rules=None) -> dict:
    out = {k: list(state.get(k) or []) for k in KEYS}
    for key, new in (("gold", gold), ("anti", anti), ("rules", rules)):
        for item in (new or []):
            item = (item or "").strip()
            if item and item not in out[key]:
                out[key].append(item)
        out[key] = out[key][-CAPS[key]:]
    return out


def format_for_prompt(state: dict) -> str:
    state = {k: list(state.get(k) or []) for k in KEYS}
    if not any(state.values()):
        return ""
    parts = []
    if state["gold"]:
        parts.append("## LEARNED — drafts you've kept (match this texture)\n"
                     + "\n".join(f"- {g}" for g in state["gold"]))
    if state["anti"]:
        parts.append("## LEARNED — drafts I rejected, do NOT write like these\n"
                     + "\n".join(f"- {a}" for a in state["anti"]))
    if state["rules"]:
        parts.append("## LEARNED — extra voice rules\n"
                     + "\n".join(f"- {r}" for r in state["rules"]))
    return "\n\n".join(parts) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_voice_state.py`
Expected: all 6 print `ok`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add voice_state.py tests/test_voice_state.py
git commit -m "feat: voice_state module — learned gold/anti/rules data file"
```

---

## Task 3: Inject learned state into draft prompts

**Files:**
- Modify: `pipeline.py:433-444` (`_voice_header`) + import near top of `pipeline.py`
- Test: `tests/test_pipeline_variety.py` (append one test)

**Interfaces:**
- Consumes: `voice_state.load_state()`, `voice_state.format_for_prompt()` from Task 2.
- Produces: `_voice_header()` output now ends with the learned blocks when state is non-empty (still ends with just `GOLD_EXAMPLES` when empty — preserving current behavior).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_variety.py` (zero-arg; save/restore the attribute — no monkeypatch):
```python
def test_voice_header_injects_learned_state():
    orig = P._learned_state
    try:
        P._learned_state = lambda: ("## LEARNED — drafts you've kept (match this texture)\n"
                                    "- kept one\n\n"
                                    "## LEARNED — drafts I rejected, do NOT write like these\n"
                                    "- rejected one\n")
        header = P._voice_header({"top_keywords": ["agents"], "top_accounts": ["@x"]}, [])
        assert "kept one" in header
        assert "rejected one" in header
    finally:
        P._learned_state = orig


def test_voice_header_empty_learned_state_is_noop():
    orig = P._learned_state
    try:
        P._learned_state = lambda: ""
        header = P._voice_header({"top_keywords": ["agents"], "top_accounts": ["@x"]}, [])
        assert "LEARNED" not in header
    finally:
        P._learned_state = orig
```
(The RED failure comes from `orig = P._learned_state` raising `AttributeError` before the function exists.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_pipeline_variety.py`
Expected: the two new `test_voice_header_*` print `FAIL` with `AttributeError: ... has no attribute '_learned_state'`; overall exit 1.

- [ ] **Step 3: Write the implementation**

Near the top of `pipeline.py` (with the other imports, after `import os`), add:
```python
import voice_state
```
Add a small indirection function just above `_voice_header` (so tests can monkeypatch it):
```python
def _learned_state() -> str:
    """Formatted learned voice blocks from data/voice_state.json (or '')."""
    try:
        return voice_state.format_for_prompt(voice_state.load_state())
    except Exception:
        return ""
```
In `_voice_header`, change the final return line from:
```python
        f"{GOLD_EXAMPLES}\n"
    )
```
to:
```python
        f"{GOLD_EXAMPLES}\n"
        + (("\n" + _learned_state()) if _learned_state() else "")
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_pipeline_variety.py`
Expected: all `test_*` print `ok` (fixture-free tests run; none skipped for these two), exit 0.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline_variety.py
git commit -m "feat(pipeline): inject learned voice state into draft prompts"
```

---

## Task 4: `feedback.py` — event store + summary

**Files:**
- Create: `feedback.py`
- Test: `tests/test_feedback.py`

**Interfaces:**
- Produces:
  - `feedback.FEEDBACK_PATH` (module constant `Path`)
  - `GOOD_ACTIONS = {"mark_posted", "like", "post"}`, `BAD_ACTIONS = {"discard"}`
  - `record_event(event: dict, path=None) -> dict` — stamps `ts`, derives `signal` (good/bad/None), normalizes `original_text`/`final_text`, sets `edited`, appends to file, returns the stored record.
  - `load_events(path=None) -> list`
  - `summarize(events: list, since_ts: str | None = None) -> dict` — `{"good", "bad", "total", "since_last", "by_kind": {kind: {"good", "bad"}}}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_feedback.py`:
```python
"""Unit tests for feedback.py (event store + summary).

Run directly (no pytest): python3 tests/test_feedback.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import feedback as FB  # noqa: E402


def _tmp():
    return Path(tempfile.mkdtemp()) / "fb.json"


def test_record_maps_signals_and_stamps_ts():
    p = _tmp()
    good = FB.record_event({"kind": "post", "action": "mark_posted",
                            "original_text": "hi"}, path=p)
    assert good["signal"] == "good"
    assert good["ts"]                      # stamped
    assert good["edited"] is False         # no edit
    bad = FB.record_event({"kind": "reply", "action": "discard",
                           "original_text": "meh"}, path=p)
    assert bad["signal"] == "bad"
    assert len(FB.load_events(path=p)) == 2


def test_record_detects_edit_delta():
    p = _tmp()
    rec = FB.record_event({"kind": "quote", "action": "like",
                           "original_text": "orig", "final_text": "edited"}, path=p)
    assert rec["edited"] is True
    assert rec["original_text"] == "orig"
    assert rec["final_text"] == "edited"


def test_record_unknown_action_signal_none():
    p = _tmp()
    rec = FB.record_event({"kind": "post", "action": "whatever",
                           "original_text": "x"}, path=p)
    assert rec["signal"] is None


def test_summarize_counts():
    p = _tmp()
    FB.record_event({"kind": "post", "action": "like", "original_text": "a"}, path=p)
    FB.record_event({"kind": "post", "action": "discard", "original_text": "b"}, path=p)
    FB.record_event({"kind": "reply", "action": "discard", "original_text": "c"}, path=p)
    s = FB.summarize(FB.load_events(path=p))
    assert s["good"] == 1
    assert s["bad"] == 2
    assert s["total"] == 3
    assert s["by_kind"]["post"] == {"good": 1, "bad": 1}
    assert s["by_kind"]["reply"] == {"good": 0, "bad": 1}


def test_summarize_since_ts():
    events = [{"ts": "2026-06-20T00:00:00Z", "signal": "good", "kind": "post"},
              {"ts": "2026-06-22T00:00:00Z", "signal": "bad", "kind": "post"}]
    s = FB.summarize(events, since_ts="2026-06-21T00:00:00Z")
    assert s["since_last"] == 1


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_feedback.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'feedback'`.

- [ ] **Step 3: Write the implementation**

Create `feedback.py`:
```python
"""Append-only feedback event store for X drafts.

One event per user action on a draft card (discard / mark_posted / like / post).
Discards are the negative signal; kept/edited/posted are positive. The eval
(eval_engine.py) reads these to tune the learned voice state. Lives in data/.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FEEDBACK_PATH = ROOT / "data" / "feedback.json"

GOOD_ACTIONS = {"mark_posted", "like", "post"}
BAD_ACTIONS = {"discard"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_events(path=None) -> list:
    path = path or FEEDBACK_PATH
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def record_event(event: dict, path=None) -> dict:
    path = path or FEEDBACK_PATH
    action = event.get("action")
    signal = "good" if action in GOOD_ACTIONS else "bad" if action in BAD_ACTIONS else None
    original = (event.get("original_text") or "").strip()
    final = (event.get("final_text") or original).strip()
    rec = {
        "ts": _now_iso(),
        "kind": event.get("kind"),
        "action": action,
        "signal": signal,
        "original_text": original,
        "final_text": final,
        "edited": bool(original) and final != original,
        "target_author": event.get("target_author"),
        "target_text": event.get("target_text"),
    }
    events = load_events(path)
    events.append(rec)
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(events, indent=2, ensure_ascii=False))
    return rec


def summarize(events: list, since_ts: str | None = None) -> dict:
    good = sum(1 for e in events if e.get("signal") == "good")
    bad = sum(1 for e in events if e.get("signal") == "bad")
    by_kind: dict = {}
    for e in events:
        k = e.get("kind") or "?"
        d = by_kind.setdefault(k, {"good": 0, "bad": 0})
        if e.get("signal") in d:
            d[e["signal"]] += 1
    since = sum(1 for e in events if since_ts and (e.get("ts") or "") > since_ts)
    return {"good": good, "bad": bad, "total": len(events),
            "since_last": since, "by_kind": by_kind}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_feedback.py`
Expected: all 5 print `ok`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add feedback.py tests/test_feedback.py
git commit -m "feat: feedback module — append-only draft event store"
```

---

## Task 5: `eval_engine.py` — guarded auto-eval + revert

**Files:**
- Create: `eval_engine.py`
- Test: `tests/test_eval_engine.py`

**Interfaces:**
- Consumes: `voice_state` (Task 2), `feedback` (Task 4).
- Produces:
  - `eval_engine.EVALS_PATH`, `eval_engine.MIN_EVENTS`, `eval_engine.CADENCE_HOURS`, `eval_engine.MAX_EXAMPLES`
  - `load_runs(path=None) -> list`
  - `run_eval(force=False, now=None, caller=None) -> dict` — guard (cadence + threshold) unless `force`; gather good/bad; call `caller(prompt) -> dict|None` (defaults to a lazy `pipeline._claude_json` wrapper); auto-merge+save voice state; append + return a run record. On guard skip returns `{"skipped": <reason>}`; on claude failure `{"skipped": "claude_failed"}`.
  - `revert_eval(run_id, now=None) -> dict` — restore that run's `state_before`, mark `reverted`; `{"ok": bool, ...}`.
  - `overview() -> dict` — `{"runs": [...], "summary": {...}, "state": {...}}` for the `/evals` route.
  - `build_prompt(good: list, bad: list, state: dict) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_engine.py`:
```python
"""Unit tests for eval_engine.py (guard, merge, revert).

Run directly (no pytest): python3 tests/test_eval_engine.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import voice_state as VS  # noqa: E402
import feedback as FB     # noqa: E402
import eval_engine as EE  # noqa: E402


def _wire():
    """Point all three modules' file constants at a fresh temp dir (direct
    attribute assignment — the path=None-then-or-CONSTANT pattern reads these
    globals at call time, so reassigning isolates each test)."""
    d = Path(tempfile.mkdtemp())
    VS.STATE_PATH = d / "voice_state.json"
    FB.FEEDBACK_PATH = d / "feedback.json"
    EE.EVALS_PATH = d / "evals.json"
    return d


def _seed_events(n_good, n_bad):
    for i in range(n_good):
        FB.record_event({"kind": "post", "action": "like", "original_text": f"good{i}"})
    for i in range(n_bad):
        FB.record_event({"kind": "post", "action": "discard", "original_text": f"bad{i}"})


def test_skips_when_insufficient_events():
    _wire()
    _seed_events(1, 1)   # below MIN_EVENTS
    res = EE.run_eval(caller=lambda p: {"conclusion": "x"})
    assert res == {"skipped": "insufficient"}


def test_runs_and_writes_state():
    _wire()
    _seed_events(5, 5)
    fake = {"conclusion": "keep it short", "gold_examples_to_add": ["short one"],
            "anti_examples_to_add": ["long polished one"], "rule_adjustments": ["be terse"]}
    run = EE.run_eval(caller=lambda p: fake)
    assert "skipped" not in run
    assert run["conclusion"] == "keep it short"
    assert run["added"]["gold"] == ["short one"]
    state = VS.load_state()
    assert "short one" in state["gold"]
    assert "long polished one" in state["anti"]
    assert "be terse" in state["rules"]
    assert len(EE.load_runs()) == 1


def test_cadence_guard_blocks_second_run():
    _wire()
    _seed_events(5, 5)
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    EE.run_eval(caller=lambda p: {"conclusion": "a"}, now=now)
    _seed_events(5, 5)   # plenty of new events
    res = EE.run_eval(caller=lambda p: {"conclusion": "b"},
                      now=now + timedelta(hours=1))   # <24h later
    assert res == {"skipped": "cadence"}


def test_force_bypasses_guards():
    _wire()
    _seed_events(0, 0)   # nothing
    run = EE.run_eval(force=True, caller=lambda p: {"conclusion": "forced"})
    assert run["conclusion"] == "forced"


def test_claude_failure_returns_skip():
    _wire()
    _seed_events(5, 5)
    res = EE.run_eval(caller=lambda p: None)
    assert res == {"skipped": "claude_failed"}


def test_revert_restores_prior_state():
    _wire()
    VS.save_state({"gold": ["before"], "anti": [], "rules": []})
    _seed_events(5, 5)
    run = EE.run_eval(caller=lambda p: {"conclusion": "c", "gold_examples_to_add": ["after"]})
    assert "after" in VS.load_state()["gold"]
    res = EE.revert_eval(run["id"])
    assert res["ok"] is True
    assert VS.load_state() == {"gold": ["before"], "anti": [], "rules": []}
    # second revert is a no-op
    assert EE.revert_eval(run["id"])["ok"] is False


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_eval_engine.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval_engine'`.

- [ ] **Step 3: Write the implementation**

Create `eval_engine.py`:
```python
"""Daily-guarded, fully-automatic eval that tunes the learned voice state.

Reads kept (good) vs discarded (bad) drafts from feedback.py, asks claude what
separates them, and auto-writes voice_state.py's data file. Every run is logged
to data/evals.json with the conclusion, the diff applied, and a state snapshot
for one-click revert. Lives in data/ (gitignored).
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import voice_state
import feedback as fb

ROOT = Path(__file__).resolve().parent
EVALS_PATH = ROOT / "data" / "evals.json"

MIN_EVENTS = int(os.environ.get("EVAL_MIN_EVENTS") or 5)
CADENCE_HOURS = 24
MAX_EXAMPLES = 40


def load_runs(path=None) -> list:
    path = path or EVALS_PATH
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_runs(runs: list, path=None) -> None:
    path = path or EVALS_PATH
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(runs, indent=2, ensure_ascii=False))


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _default_caller(prompt: str):
    # Lazy import avoids a circular import (pipeline imports eval_engine).
    import pipeline
    return pipeline._claude_json(prompt, timeout=300, label="eval")


def build_prompt(good: list, bad: list, state: dict) -> str:
    def block(items):
        out = []
        for e in items:
            t = (e.get("final_text") or e.get("original_text") or "").strip()
            if not t:
                continue
            tag = " (edited from: " + e["original_text"] + ")" if e.get("edited") else ""
            out.append(f"- [{e.get('kind', '?')}] {t}{tag}")
        return "\n".join(out) or "(none)"

    current = voice_state.format_for_prompt(state) or "(none yet)"
    return (
        "You tune the voice of an automated tweet-drafting system by contrasting drafts "
        "the user KEPT against drafts they DISCARDED.\n\n"
        "## KEPT (good — these match his voice / were worth posting)\n"
        f"{block(good)}\n\n"
        "## DISCARDED (bad — he rejected these; learn what to avoid)\n"
        f"{block(bad)}\n\n"
        "## Current learned guidance already in the prompt\n"
        f"{current}\n\n"
        "## Task\n"
        "Figure out what separates kept from discarded. Then return JSON ONLY (no fences, "
        "start with `{` end with `}`) with this exact shape:\n"
        "{\n"
        '  "conclusion": "<2-4 sentences: what makes his kept drafts work and what the discarded ones got wrong>",\n'
        '  "gold_examples_to_add": ["<verbatim text of the best KEPT drafts to reuse as exemplars; 0-5 items>"],\n'
        '  "anti_examples_to_add": ["<verbatim text of representative DISCARDED drafts to explicitly avoid; 0-5 items>"],\n'
        '  "rule_adjustments": ["<short new voice-rule lines distilled from the contrast; 0-4 items>"]\n'
        "}\n"
        "Only include NEW items not already covered by the current guidance. Empty arrays are fine."
    )


def _should_run(runs: list, events: list, now: datetime):
    last = runs[-1]["ts"] if runs else None
    if last:
        last_dt = _parse_ts(last)
        if last_dt and (now - last_dt) < timedelta(hours=CADENCE_HOURS):
            return False, "cadence"
    new_events = sum(1 for e in events if not last or (e.get("ts") or "") > last)
    if new_events < MIN_EVENTS:
        return False, "insufficient"
    return True, ""


def run_eval(force=False, now=None, caller=None) -> dict:
    now = now or datetime.now(timezone.utc)
    runs = load_runs()
    events = fb.load_events()
    if not force:
        ok, reason = _should_run(runs, events, now)
        if not ok:
            return {"skipped": reason}

    good = [e for e in events if e.get("signal") == "good"][-MAX_EXAMPLES:]
    bad = [e for e in events if e.get("signal") == "bad"][-MAX_EXAMPLES:]
    state = voice_state.load_state()
    caller = caller or _default_caller

    result = caller(build_prompt(good, bad, state))
    if not result:
        return {"skipped": "claude_failed"}

    new_state = voice_state.merge_state(
        state,
        gold=result.get("gold_examples_to_add"),
        anti=result.get("anti_examples_to_add"),
        rules=result.get("rule_adjustments"),
    )
    voice_state.save_state(new_state)

    added = {k: [x for x in new_state[k] if x not in state.get(k, [])]
             for k in voice_state.KEYS}
    last_ts = runs[-1]["ts"] if runs else None
    run = {
        "id": now.strftime("%Y%m%dT%H%M%S"),
        "ts": now.isoformat(),
        "conclusion": result.get("conclusion", ""),
        "added": added,
        "counts": {"good": len(good), "bad": len(bad),
                   "since_last": sum(1 for e in events
                                     if not last_ts or (e.get("ts") or "") > last_ts)},
        "state_before": state,
        "reverted": False,
    }
    runs.append(run)
    _save_runs(runs)
    return run


def revert_eval(run_id, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    runs = load_runs()
    for r in runs:
        if r.get("id") == run_id and not r.get("reverted"):
            voice_state.save_state(r.get("state_before") or voice_state.load_state())
            r["reverted"] = True
            r["reverted_at"] = now.isoformat()
            _save_runs(runs)
            return {"ok": True, "id": run_id}
    return {"ok": False, "error": "not found or already reverted"}


def overview() -> dict:
    runs = load_runs()
    events = fb.load_events()
    last_ts = runs[-1]["ts"] if runs else None
    return {
        "runs": list(reversed(runs)),   # newest first for the UI
        "summary": fb.summarize(events, since_ts=last_ts),
        "state": voice_state.load_state(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_eval_engine.py`
Expected: all 6 print `ok`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add eval_engine.py tests/test_eval_engine.py
git commit -m "feat: eval_engine — guarded auto-eval of kept vs discarded drafts"
```

---

## Task 6: Pipeline daily eval guard hook

**Files:**
- Modify: `pipeline.py:861` area (`main()`, just before `drafts = generate_drafts(...)`)
- Test: manual (the guard logic itself is covered by Task 5).

**Interfaces:**
- Consumes: `eval_engine.run_eval()` (Task 5).

- [ ] **Step 1: Add the guarded call**

In `pipeline.py` `main()`, immediately **before** the line `drafts = generate_drafts(sig, mine, curated, trending, history=history)`, insert:
```python
    try:
        import eval_engine
        ev = eval_engine.run_eval()
        if ev.get("skipped"):
            print(f"[pipeline] eval skipped ({ev['skipped']})", flush=True)
        else:
            a = ev.get("added", {})
            print(f"[pipeline] eval ran {ev.get('id')} "
                  f"(+gold {len(a.get('gold', []))} / +anti {len(a.get('anti', []))} "
                  f"/ +rules {len(a.get('rules', []))})", flush=True)
    except Exception as e:
        sys.stderr.write(f"[pipeline] eval failed (non-fatal): {e}\n")
```

- [ ] **Step 2: Verify import resolves + no syntax error**

Run: `python3 -c "import pipeline; print('ok')"`
Expected: prints `ok` (no ImportError / SyntaxError).

- [ ] **Step 3: Verify the guard is callable in isolation**

Run: `python3 -c "import eval_engine; print(eval_engine.run_eval())"`
Expected: prints `{'skipped': 'insufficient'}` (or `{'skipped': 'cadence'}`) on a fresh checkout — proves the guard short-circuits without calling claude.

- [ ] **Step 4: Commit**

```bash
git add pipeline.py
git commit -m "feat(pipeline): run daily eval guard before drafting"
```

---

## Task 7: Server routes — `/feedback`, `/evals`, `/eval/run`, `/evals/revert`

**Files:**
- Modify: `server.py` (imports near top with the other `import *_mod`; `do_GET` add `/evals`; `do_POST` add `/feedback`, `/eval/run`, `/evals/revert`)
- Test: manual (curl) — the underlying module functions are unit-tested in Tasks 4-5.

**Interfaces:**
- Consumes: `feedback.record_event`, `eval_engine.overview/run_eval/revert_eval`.
- Produces: HTTP routes returning JSON via `self._send_json`.

- [ ] **Step 1: Add imports**

Near the top of `server.py` where other modules are imported (e.g. alongside `linkedin_mod` / `blog_mod`), add:
```python
import feedback as feedback_mod
import eval_engine
```

- [ ] **Step 2: Add the GET route**

In `server.py` `do_GET`, after the `if path == "/history":` block (around line 704), add:
```python
        if path == "/evals":
            try:
                return self._send_json(200, eval_engine.overview())
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
```

- [ ] **Step 3: Add the POST routes**

In `server.py` `do_POST`, after the `if path == "/post":` block (around line 846), add:
```python
        if path == "/feedback":
            try:
                rec = feedback_mod.record_event(body)
                return self._send_json(200, {"ok": True, "event": rec})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e)})

        if path == "/eval/run":
            try:
                return self._send_json(200, eval_engine.run_eval(force=True))
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e)})

        if path == "/evals/revert":
            res = eval_engine.revert_eval(body.get("id"))
            return self._send_json(200 if res.get("ok") else 404, res)
```

- [ ] **Step 4: Smoke-test the routes**

Restart the server: `lsof -ti tcp:7873 | xargs kill -9 2>/dev/null; ~/.claude/skills/yagyesh-dashboard/run.sh &` then wait ~2s.
Run:
```bash
curl -s -XPOST localhost:7873/feedback -H 'Content-Type: application/json' \
  -d '{"kind":"post","action":"like","original_text":"smoke test"}'
curl -s localhost:7873/evals
```
Expected: first returns `{"ok": true, "event": {... "signal": "good" ...}}`; second returns `{"runs": [], "summary": {"good": 1, ...}, "state": {...}}`.
Cleanup the smoke event: `rm -f data/feedback.json` (it only held the test event).

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(server): feedback + evals routes"
```

---

## Task 8: Card actions — mark-posted, like, edit-delta feedback

**Files:**
- Modify: `static/app.js:380-563` (`makeDraftCard`)
- Modify: `static/style.css` (button variants, if needed)
- Test: manual (browser).

**Interfaces:**
- Consumes: `/feedback` route (Task 7).
- Produces: every draft card POSTs a feedback event on discard / post-success / mark-posted / like, including the edit delta.

- [ ] **Step 1: Add a feedback helper inside `makeDraftCard`**

In `static/app.js`, inside `makeDraftCard` (after `let imagePaths = [];` near line 382), capture the original text and add a helper:
```javascript
  const originalText = draft.text || "";
  const sendFeedback = (action) => {
    fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        action,
        original_text: originalText,
        final_text: textarea.value,
        target_author: draft.target_author || null,
        target_text: draft.target_text || null,
      }),
    }).catch(() => {});   // best-effort; never block the UI
  };
```

- [ ] **Step 2: Record feedback on post success and discard**

In `doPost` (the `if (data.ok) {` branch around line 484-487), after `wrap.classList.add("posted");` add:
```javascript
        sendFeedback("post");
```
In the `discardBtn` click handler (around line 552-557), after `wrap.classList.add("discarded");` add:
```javascript
    sendFeedback("discard");
```

- [ ] **Step 3: Add the "mark as posted" and "like" buttons**

After the `discardBtn` definition (line 466), add:
```javascript
  const markBtn = el("button", { class: "btn ghost", title: "I posted this manually elsewhere" }, [
    el("span", { class: "btn-key" }, "✓"),
    el("span", {}, "mark posted"),
  ]);
  const likeBtn = el("button", { class: "btn ghost", title: "good draft — keep as a positive example" }, [
    el("span", { class: "btn-key" }, "♥"),
    el("span", {}, "like"),
  ]);
```
Update the `allBtns` array (line 468) to include them:
```javascript
  const allBtns = [postBtn, schedBtn, queueBtn, discardBtn, markBtn, likeBtn];
```
Add their handlers after the `discardBtn` handler (after line 557):
```javascript
  markBtn.addEventListener("click", () => {
    sendFeedback("mark_posted");
    wrap.classList.add("posted");
    textarea.disabled = true;
    setDisabled(true);
    queueButtons.delete(queueBtn);
    toast("marked as posted ✓", "ok");
  });
  likeBtn.addEventListener("click", () => {
    sendFeedback("like");
    likeBtn.classList.add("done");
    toast("saved as a good example ♥", "ok");
  });
```

- [ ] **Step 4: Add the buttons to the action row**

Change the actions append (line 559) to include the new buttons:
```javascript
  wrap.appendChild(el("div", { class: "actions" }, [uploadLabel, counter, discardBtn, likeBtn, markBtn, postBtn, schedBtn, queueBtn]));
```

- [ ] **Step 5: Manual verification**

Restart the server and hard-reload `http://127.0.0.1:7873/#drafts`. On a draft card:
- Edit the text, click **like** → toast appears; `curl -s localhost:7873/evals` shows `summary.good` incremented and the event has `edited: true`.
- Click **discard** on another → `summary.bad` increments.
- Click **mark posted** on another → card greys, `summary.good` increments, no tweet is posted (verify nothing new in `data/posted.json`).

- [ ] **Step 6: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): draft card feedback — mark posted, like, edit-delta capture"
```

---

## Task 9: Evals screen (`12 evals`)

**Files:**
- Modify: `static/index.html` (nav item + section markup)
- Modify: `static/app.js` (`SECTION_META`, routing, `loadEvals()` + render)
- Modify: `static/style.css` (screen styles)
- Test: manual (browser).

**Interfaces:**
- Consumes: `/evals` (GET), `/eval/run` (POST), `/evals/revert` (POST).

- [ ] **Step 1: Add the nav item**

In `static/index.html`, after the `linkedin-drafts` nav item (line 65-68), before `</nav>` (line 69), add:
```html
        <a href="#evals" class="nav-item" data-section="evals">
          <span class="nav-key">12</span><span class="nav-name">evals</span>
          <span class="nav-count" data-count="evals">—</span>
        </a>
```

- [ ] **Step 2: Add the section markup**

In `static/index.html`, after the `section-linkedin-drafts` `</section>` (around line 405+), add:
```html
      <section class="section hidden" id="section-evals">
        <div class="evals-summary" id="evals-summary"></div>
        <div class="evals-toolbar">
          <button class="btn primary" id="eval-run-btn">run eval now</button>
        </div>
        <div class="evals-state" id="evals-state"></div>
        <h3 class="evals-h">eval history</h3>
        <div id="evals-runs"></div>
      </section>
```

- [ ] **Step 3: Register the section meta + routing**

In `static/app.js` `SECTION_META` (after the `linkedin-drafts` entry, line 59), add:
```javascript
  evals:     { title: "evals", sub: "what the daily eval learned from your kept vs discarded drafts" },
```
In `showSection` (after line 205 `if (name === "linkedin-drafts") loadLinkedin();`), add:
```javascript
  if (name === "evals") loadEvals();
```

- [ ] **Step 4: Implement `loadEvals()` + render**

In `static/app.js`, add near the other loader functions (e.g. after `loadHistory`):
```javascript
async function loadEvals() {
  const summaryEl = $("#evals-summary");
  const stateEl = $("#evals-state");
  const runsEl = $("#evals-runs");
  summaryEl.textContent = "loading…";
  let data;
  try {
    data = await (await fetch("/evals")).json();
  } catch (e) {
    summaryEl.textContent = "failed to load evals.";
    return;
  }
  const s = data.summary || { good: 0, bad: 0, by_kind: {}, since_last: 0 };

  summaryEl.innerHTML = "";
  summaryEl.append(
    el("div", { class: "eval-stat good" }, [el("b", {}, String(s.good)), el("span", {}, "kept / good")]),
    el("div", { class: "eval-stat bad" }, [el("b", {}, String(s.bad)), el("span", {}, "discarded")]),
    el("div", { class: "eval-stat" }, [el("b", {}, String(s.since_last)), el("span", {}, "new since last eval")]),
  );

  // current learned state
  const st = data.state || { gold: [], anti: [], rules: [] };
  stateEl.innerHTML = "";
  const stateBlock = (title, items, cls) => {
    if (!items || !items.length) return;
    stateEl.appendChild(el("h4", { class: "evals-h" }, title));
    stateEl.appendChild(el("ul", { class: `eval-list ${cls}` },
      items.map(i => el("li", {}, i))));
  };
  stateBlock("currently rewarding (gold)", st.gold, "good");
  stateBlock("currently avoiding (anti)", st.anti, "bad");
  stateBlock("extra rules", st.rules, "");
  if (!st.gold.length && !st.anti.length && !st.rules.length) {
    stateEl.appendChild(empty("nothing learned yet — discard/keep some drafts, then run an eval."));
  }

  // run history (already newest-first from the server)
  runsEl.innerHTML = "";
  const runs = data.runs || [];
  if (!runs.length) {
    runsEl.appendChild(empty("no eval runs yet."));
  } else {
    runs.forEach(r => {
      const card = el("div", { class: "eval-run" + (r.reverted ? " reverted" : "") });
      card.appendChild(el("div", { class: "eval-run-head" }, [
        el("span", {}, relTime(r.ts)),
        el("span", { class: "eval-run-counts" },
          `${r.counts?.good ?? 0} good · ${r.counts?.bad ?? 0} bad`),
      ]));
      card.appendChild(el("p", { class: "eval-conclusion" }, r.conclusion || "(no conclusion)"));
      const added = r.added || {};
      const addedBlock = (label, items) => {
        if (!items || !items.length) return;
        card.appendChild(el("div", { class: "eval-added" }, [
          el("span", { class: "eval-added-label" }, label),
          el("ul", {}, items.map(i => el("li", {}, i))),
        ]));
      };
      addedBlock("+ gold", added.gold);
      addedBlock("+ anti", added.anti);
      addedBlock("+ rules", added.rules);
      if (r.reverted) {
        card.appendChild(el("span", { class: "eval-reverted-tag" }, "reverted"));
      } else {
        const rev = el("button", { class: "btn ghost danger" }, "revert this run");
        rev.addEventListener("click", async () => {
          rev.disabled = true;
          const res = await (await fetch("/evals/revert", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: r.id }),
          })).json();
          if (res.ok) { toast("reverted ✓", "ok"); loadEvals(); }
          else { toast(`revert failed: ${res.error || "?"}`, "error"); rev.disabled = false; }
        });
        card.appendChild(rev);
      }
      runsEl.appendChild(card);
    });
  }
}
```

- [ ] **Step 5: Wire the "run eval now" button**

In `static/app.js`, at the end of init (where other one-time button listeners are registered), add:
```javascript
const evalRunBtn = $("#eval-run-btn");
if (evalRunBtn) {
  evalRunBtn.addEventListener("click", async () => {
    evalRunBtn.disabled = true;
    evalRunBtn.textContent = "running…";
    try {
      const res = await (await fetch("/eval/run", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: "{}" })).json();
      if (res.skipped) toast(`eval skipped (${res.skipped})`, "");
      else toast("eval done ✓", "ok");
      loadEvals();
    } catch (e) {
      toast(`eval failed: ${e.message}`, "error");
    } finally {
      evalRunBtn.disabled = false;
      evalRunBtn.textContent = "run eval now";
    }
  });
}
```

- [ ] **Step 6: Add styles**

In `static/style.css`, append:
```css
.evals-summary { display: flex; gap: 1rem; margin-bottom: 1rem; }
.eval-stat { display: flex; flex-direction: column; padding: .6rem 1rem; border: 1px solid var(--border, #2a2a2a); border-radius: 8px; }
.eval-stat b { font-size: 1.4rem; }
.eval-stat.good b { color: #4 caf50; }
.eval-stat.bad b { color: #e57373; }
.evals-toolbar { margin-bottom: 1rem; }
.evals-h { margin: 1.2rem 0 .4rem; opacity: .8; }
.eval-list { margin: 0 0 .6rem 1rem; }
.eval-list.good li { color: #9ccc9c; }
.eval-list.bad li { color: #e0a0a0; }
.eval-run { border: 1px solid var(--border, #2a2a2a); border-radius: 8px; padding: .8rem 1rem; margin-bottom: .8rem; }
.eval-run.reverted { opacity: .5; }
.eval-run-head { display: flex; justify-content: space-between; font-size: .85rem; opacity: .7; }
.eval-conclusion { margin: .5rem 0; }
.eval-added { font-size: .85rem; margin: .3rem 0; }
.eval-added-label { font-weight: 600; opacity: .8; }
.eval-reverted-tag { font-size: .8rem; color: #e57373; }
```
(Match the repo's existing CSS variable names if they differ — check the top of `static/style.css` for the actual `--border` / color tokens and use those; the fallbacks above are safe if no token exists. Fix the obvious typo: `#4caf50`, not `#4 caf50`.)

- [ ] **Step 7: Manual verification**

Restart the server, hard-reload, click nav **12 evals**:
- Summary shows good/bad/since-last counts (seed a few via the draft cards first).
- Click **run eval now** with ≥5 good and ≥5 bad events → a run card appears with a conclusion + added items; the "currently rewarding/avoiding" lists populate.
- Click **revert this run** → the run greys out and the learned-state lists shrink back.
- Confirm a normal **refresh** still works and the next draft prompt would include learned blocks (`python3 -c "import pipeline, voice_state; print(pipeline._learned_state()[:200])"`).

- [ ] **Step 8: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat(ui): evals screen — summary, run history, learned state, revert"
```

---

## Task 10: Docs — update SKILL.md

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Document the feedback loop**

In `SKILL.md`, add to the Files list:
```
- `feedback.py` — append-only draft feedback events (discard/like/mark-posted/post) → data/feedback.json
- `voice_state.py` — machine-managed learned voice state injected into draft prompts
- `eval_engine.py` — daily auto-eval: kept-vs-discarded → tunes voice_state, logs runs to data/evals.json
```
And add a short section describing the `12 evals` screen and the daily eval (mirror the LinkedIn workspace section's style): card actions now include **like** and **mark as posted** (manual post — records a positive signal, fires no tweet); discards record a negative signal; a daily, fully-automatic eval contrasts kept vs discarded drafts and rewrites the learned voice state, with every run logged and revertible in `12 evals`.

- [ ] **Step 2: Commit**

```bash
git add SKILL.md
git commit -m "docs: document draft feedback loop + evals screen"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** volume bump (T1), prompt injection (T2-T3), feedback capture incl. edit delta (T4, T8), eval engine fully-automatic + daily guard + revert (T5-T6), routes (T7), metrics screen with conclusion + changes (T9), docs (T10). All spec sections map to a task.
- **Type consistency:** voice state shape `{"gold","anti","rules"}` is identical across `voice_state`, `eval_engine`, and the UI. Feedback event keys match between `feedback.record_event`, the `/feedback` body in `app.js`, and `summarize`. `overview()` returns `{runs, summary, state}` consumed verbatim by `loadEvals`.
- **Known limitation:** actual reply/quote counts are bounded by available unique feed targets; 300 is a ceiling, not a guarantee. Documented in T1.
- **Circular import:** `pipeline` imports `eval_engine` (T6) and `eval_engine` imports `pipeline` lazily inside `_default_caller` (T5) — no import-time cycle.
