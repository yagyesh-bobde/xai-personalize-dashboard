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
