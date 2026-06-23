"""Daily-guarded, fully-automatic eval that tunes the learned voice state.

Reads kept (good) vs discarded (bad) drafts from feedback.py, asks claude what
separates them, and auto-writes voice_state.py's data file. Every run is logged
to data/evals.json with the conclusion, the diff applied, and a state snapshot
for one-click revert. Lives in data/ (gitignored).
"""
import json
import os
import tempfile
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


def _save_runs(runs: list, path=None) -> None:
    path = path or EVALS_PATH
    _atomic_write_json(path, runs)


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

    # Fresh-per-cycle: only learn from feedback gathered since the last eval, so
    # each eval evaluates the draft set produced after the previous one (the old
    # set's feedback is considered consumed once an eval has run on it).
    last_ts = runs[-1]["ts"] if runs else None
    fresh = [e for e in events if not last_ts or (e.get("ts") or "") > last_ts]
    good = [e for e in fresh if e.get("signal") == "good"][-MAX_EXAMPLES:]
    bad = [e for e in fresh if e.get("signal") == "bad"][-MAX_EXAMPLES:]
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


def voice_changed(run) -> bool:
    """True iff an eval run actually altered the learned voice state (added any
    gold/anti example or rule) — i.e. the drafting prompt is now different."""
    if not run or "skipped" in run:
        return False
    return any((run.get("added") or {}).values())


def overview() -> dict:
    runs = load_runs()
    events = fb.load_events()
    last_ts = runs[-1]["ts"] if runs else None
    return {
        "runs": list(reversed(runs)),   # newest first for the UI
        "summary": fb.summarize(events, since_ts=last_ts),
        "state": voice_state.load_state(),
    }
