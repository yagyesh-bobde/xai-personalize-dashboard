"""Append-only feedback event store for X drafts.

One event per user action on a draft card (discard / mark_posted / like / post).
Discards are the negative signal; kept/edited/posted are positive. The eval
(eval_engine.py) reads these to tune the learned voice state. Lives in data/.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FEEDBACK_PATH = ROOT / "data" / "feedback.json"

_LOCK = threading.Lock()

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
    with _LOCK:
        events = load_events(path)
        events.append(rec)
        _atomic_write_json(path, events)
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
