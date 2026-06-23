"""Unit tests for analytics.py.

Run directly (no pytest): python3 tests/test_analytics.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics as AN  # noqa: E402


def test_eng_rate_basic():
    m = {"likes": 5, "retweets": 2, "replies": 1, "quotes": 1, "bookmarks": 1, "views": 100}
    assert AN.eng_rate(m) == 0.1   # 10 / 100


def test_eng_rate_guards_zero_views():
    m = {"likes": 3, "retweets": 0, "replies": 0, "quotes": 0, "bookmarks": 0, "views": 0}
    assert AN.eng_rate(m) == 3.0   # 3 / max(0,1)


def test_classify_length():
    assert AN.classify_length("a" * 50) == "short"
    assert AN.classify_length("a" * 150) == "medium"
    assert AN.classify_length("a" * 250) == "long"


def test_has_link():
    assert AN.has_link("check https://x.com/foo", None) is True
    assert AN.has_link("no link here", []) is False
    assert AN.has_link("plain", ["https://t.co/x"]) is True


def test_tokenize_strips_stopwords_urls_handles():
    toks = AN.tokenize("Building an AI agent with @claude https://t.co/x the best")
    assert "building" in toks
    assert "agent" in toks
    assert "claude" not in toks       # @handle stripped
    assert "the" not in toks          # stopword
    assert "https" not in toks        # url stripped


def test_local_hour_and_weekday():
    assert AN.local_hour("2026-06-23 19:58") == 19
    # 2026-06-23 is a Tuesday → weekday() == 1
    assert AN.local_weekday("2026-06-23 19:58") == 1


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
