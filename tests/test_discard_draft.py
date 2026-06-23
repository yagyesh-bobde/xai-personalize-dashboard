"""Unit tests for persistent discard of X drafts (server.remove_draft_from_data).

Discarding an X draft must remove it from dashboard_data.json so it does not
reappear on reload. Run directly: python3 tests/test_discard_draft.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import server as SRV  # noqa: E402


def _tmp_data():
    p = Path(tempfile.mkdtemp()) / "dashboard_data.json"
    p.write_text(json.dumps({
        "drafts": {
            "posts":   [{"id": "p1", "text": "a"}, {"id": "p2", "text": "b"}],
            "replies": [{"id": "r1", "text": "c"}],
            "quotes":  [{"id": "q1", "text": "d"}],
        }
    }))
    return p


def test_discard_removes_draft_and_persists():
    p = _tmp_data()
    res = SRV.remove_draft_from_data("post", "p1", path=p)
    assert res["ok"] is True
    assert res["removed"] == 1
    data = json.loads(p.read_text())
    ids = [d["id"] for d in data["drafts"]["posts"]]
    assert ids == ["p2"]                       # p1 gone, p2 kept


def test_discard_maps_kind_to_list():
    p = _tmp_data()
    SRV.remove_draft_from_data("reply", "r1", path=p)
    SRV.remove_draft_from_data("quote", "q1", path=p)
    data = json.loads(p.read_text())
    assert data["drafts"]["replies"] == []
    assert data["drafts"]["quotes"] == []


def test_discard_unknown_kind_errors():
    p = _tmp_data()
    res = SRV.remove_draft_from_data("bogus", "p1", path=p)
    assert res["ok"] is False


def test_discard_missing_id_is_noop_ok():
    p = _tmp_data()
    res = SRV.remove_draft_from_data("post", "nope", path=p)
    assert res["ok"] is True
    assert res["removed"] == 0
    data = json.loads(p.read_text())
    assert len(data["drafts"]["posts"]) == 2   # untouched


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
