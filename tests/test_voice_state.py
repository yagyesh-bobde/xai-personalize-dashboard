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
