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


def _post(pid, text, views, likes, created_iso, created_local,
          media=None, urls=None, retweet=False, lang="en"):
    return {
        "id": pid, "text": text, "isRetweet": retweet, "lang": lang,
        "createdAtISO": created_iso, "createdAtLocal": created_local,
        "media": media or [], "urls": urls or [],
        "metrics": {"likes": likes, "retweets": 0, "replies": 0,
                    "quotes": 0, "views": views, "bookmarks": 0},
    }


def _wire(tmp):
    AN.REPORT_PATH = tmp / "analytics.json"
    AN.HISTORY_PATH = tmp / "analytics_history.json"
    AN.POSTED_PATH = tmp / "posted.json"


def test_snapshot_upserts_and_skips_retweets():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [
        _post("1", "hello world", 100, 5, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30"),
        _post("2", "a retweet", 9, 0, "2026-06-24T09:00:00+00:00", "2026-06-24 14:30", retweet=True),
    ]
    hist = AN.snapshot_metrics(now, fetcher=lambda: posts)
    assert "1" in hist and "2" not in hist          # retweet skipped
    assert len(hist["1"]["snapshots"]) == 1
    assert hist["1"]["snapshots"][0]["views"] == 100


def test_snapshot_dedupes_same_day_appends_next_day():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    posts = [_post("1", "hi", 100, 5, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30")]
    AN.snapshot_metrics(datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc), fetcher=lambda: posts)
    AN.snapshot_metrics(datetime(2026, 6, 24, 20, 0, tzinfo=timezone.utc), fetcher=lambda: posts)
    hist = AN.load_history()
    assert len(hist["1"]["snapshots"]) == 1          # same day → no second snapshot
    posts2 = [_post("1", "hi", 250, 9, "2026-06-24T10:00:00+00:00", "2026-06-24 15:30")]
    hist = AN.snapshot_metrics(datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc), fetcher=lambda: posts2)
    assert len(hist["1"]["snapshots"]) == 2          # next day → appended
    assert hist["1"]["snapshots"][-1]["views"] == 250


def test_snapshot_drops_out_of_window_posts():
    tmp = Path(tempfile.mkdtemp()); _wire(tmp)
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    posts = [_post("old", "ancient", 100, 5, "2026-04-01T10:00:00+00:00", "2026-04-01 15:30")]
    hist = AN.snapshot_metrics(now, fetcher=lambda: posts)
    assert hist == {}


def _hist_entry(text, views, likes, created_iso, created_local,
                kind="post", media=False, link=False, ts="2026-06-24T12:00:00+00:00"):
    return {
        "created_at": created_iso, "created_local": created_local, "kind": kind,
        "source": "manual", "text": text, "has_media": media, "has_link": link,
        "lang": "en",
        "snapshots": [{"ts": ts, "likes": likes, "retweets": 0, "replies": 0,
                       "quotes": 0, "views": views, "bookmarks": 0}],
    }


def test_compute_report_breakdowns_and_ranking():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {
        "hi": _hist_entry("agent agent agent winner", 100, 30, "2026-06-23T10:00:00+00:00", "2026-06-23 15:30"),
        "lo": _hist_entry("agent dull loser", 100, 1, "2026-06-22T10:00:00+00:00", "2026-06-22 09:30"),
        "mid": _hist_entry("agent middle road", 100, 10, "2026-06-21T10:00:00+00:00", "2026-06-21 18:30"),
    }
    rep = AN.compute_report(hist, now)
    assert rep["n_posts"] == 3
    assert rep["top"][0]["id"] == "hi"
    assert rep["bottom"][0]["id"] == "lo"
    # "agent" appears in all 3 (support 3) → present in keywords
    assert any(k["token"] == "agent" and k["support"] == 3 for k in rep["keywords"])
    assert "post" in rep["breakdowns"]["type"]
    assert rep["breakdowns"]["type"]["post"]["count"] == 3


def test_compute_report_drops_low_view_posts_from_ranking():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {
        "tiny": _hist_entry("agent low reach high rate", 5, 5, "2026-06-23T10:00:00+00:00", "2026-06-23 15:30"),
        "real": _hist_entry("agent real reach", 200, 20, "2026-06-23T11:00:00+00:00", "2026-06-23 16:30"),
    }
    rep = AN.compute_report(hist, now)
    ids = [p["id"] for p in rep["top"]]
    assert "tiny" not in ids            # below MIN_VIEWS floor
    assert "real" in ids


def test_compute_report_window_excludes_old():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    hist = {"old": _hist_entry("agent ancient", 100, 5, "2026-04-01T10:00:00+00:00", "2026-04-01 15:30")}
    rep = AN.compute_report(hist, now)
    assert rep["n_posts"] == 0


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
