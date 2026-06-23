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


def test_eval_only_learns_from_feedback_since_last_run():
    """Fresh-per-cycle: a second eval must learn only from feedback recorded
    after the first eval, not the whole rolling history."""
    _wire()
    _seed_events(3, 2)                                  # 5 events before eval #1
    r1 = EE.run_eval(force=True, caller=lambda p: {"conclusion": "a"})
    assert r1["counts"]["good"] == 3 and r1["counts"]["bad"] == 2
    _seed_events(1, 4)                                  # new feedback after eval #1
    r2 = EE.run_eval(force=True, caller=lambda p: {"conclusion": "b"})
    assert r2["counts"]["good"] == 1                    # only the post-eval good
    assert r2["counts"]["bad"] == 4                     # only the post-eval bad


def test_voice_changed_flag():
    assert EE.voice_changed({"added": {"gold": ["x"], "anti": [], "rules": []}}) is True
    assert EE.voice_changed({"added": {"gold": [], "anti": [], "rules": []}}) is False
    assert EE.voice_changed({"skipped": "cadence"}) is False
    assert EE.voice_changed(None) is False


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
