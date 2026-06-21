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
