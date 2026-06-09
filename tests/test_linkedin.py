"""Unit tests for linkedin.py pure helpers.

Run with pytest (`python3 -m pytest tests/test_linkedin.py -v`) or directly
(`python3 tests/test_linkedin.py`) when pytest is unavailable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import linkedin as L  # noqa: E402


def test_extract_json_strips_fences():
    assert L.extract_json('```json\n{"a":1}\n```') == {"a": 1}


def test_merge_preserves_approved_and_posted():
    old = {"drafts": [
        {"id": "d1", "text": "x", "status": "approved"},
        {"id": "d2", "text": "y", "status": "posted"},
        {"id": "d3", "text": "z", "status": "draft"},
    ], "ideas": [], "themes": []}
    new = {"drafts": [{"id": "d3", "text": "ZZ", "status": "draft"}],
           "ideas": [], "themes": ["t"]}
    merged = L.merge_data(old, new)
    by = {d["id"]: d for d in merged["drafts"]}
    assert by["d1"]["status"] == "approved"   # preserved
    assert by["d2"]["status"] == "posted"     # preserved
    assert by["d3"]["text"] == "ZZ"           # draft replaced
    assert merged["themes"] == ["t"]


def test_mine_themes_counts_tokens():
    themes = L.mine_themes(["shipping ai agents now", "ai agents are great", "react native rocks"])
    assert "agents" in themes and "ai" in themes


def _run_all():
    """Minimal runner used when pytest is not installed."""
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
