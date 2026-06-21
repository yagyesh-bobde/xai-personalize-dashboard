"""Unit tests for pipeline.py draft-variety helpers.

Run with pytest (`python3 -m pytest tests/test_pipeline_variety.py -v`) or
directly (`python3 tests/test_pipeline_variety.py`) when pytest is unavailable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pipeline as P  # noqa: E402


def test_jaccard_identical_and_disjoint():
    a = P._word_set("shipped my own feed tool last week")
    assert P._jaccard(a, a) == 1.0
    b = P._word_set("metro bundler rebuilt everything again")
    assert P._jaccard(a, b) == 0.0


def test_dedupe_drops_near_duplicates():
    posts = [
        {"id": "p1", "text": "shipped my own x feed tool last week, super productive"},
        {"id": "p2", "text": "shipped my own feed tool last wk, lowkey productive stretch"},
        {"id": "p3", "text": "metro decided to rebuild the entire bundle again, classic"},
    ]
    out = P._dedupe_posts(posts)
    texts = [p["id"] for p in out]
    assert "p1" in texts and "p3" in texts
    assert "p2" not in texts  # near-duplicate of p1 collapses


def test_dedupe_opener_cap():
    posts = [
        {"id": "a", "text": "anyone actually using fable 5 for refactors yet"},
        {"id": "b", "text": "anyone actually moved off claude code to codex full time"},
        {"id": "c", "text": "anyone actually running opus on high effort overnight"},
    ]
    out = P._dedupe_posts(posts, opener_cap=2)
    assert len(out) == 2  # third "anyone actually" opener dropped


def test_dedupe_excludes_history():
    history = [P._word_set("metro bundler rebuilt the entire bundle again classic")]
    posts = [
        {"id": "p1", "text": "metro bundler rebuilt the entire bundle again, classic"},
        {"id": "p2", "text": "codex review flagged 11 things, 2 were real bugs"},
    ]
    out = P._dedupe_posts(posts, history=history)
    assert [p["id"] for p in out] == ["p2"]  # p1 already posted → dropped


def test_diversify_pool_caps_per_author():
    items = [{"id": str(i), "author": "@loud"} for i in range(5)]
    items += [{"id": "x", "author": "@quiet"}]
    out = P._diversify_pool(items, per_author_cap=2)
    authors = [t["author"] for t in out]
    assert authors.count("@loud") == 2
    assert "@quiet" in authors


def test_load_history_reads_posts_and_targets(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(P, "DATA_DIR", tmp_path)
    (tmp_path / "posted.json").write_text(json.dumps([
        {"kind": "post", "text": "metro bundler caching nonsense again"},
        {"kind": "reply", "target_id": "111"},
    ]))
    (tmp_path / "scheduled.json").write_text(json.dumps([
        {"kind": "quote", "target_id": "222"},
    ]))
    h = P.load_history(mine=[{"text": "my own recent post about agents"}])
    assert "111" in h["reply_target_ids"]
    assert "222" in h["quote_target_ids"]
    assert len(h["post_texts"]) == 2  # posted post + mine


def test_posts_prompt_includes_lane_and_keywords():
    sig = {"top_keywords": ["agents", "metro"], "top_accounts": []}
    prompt = P._posts_prompt(sig, [], [], 5, lane="honest gripe", keywords=["metro", "expo"])
    assert "honest gripe" in prompt
    assert "metro, expo" in prompt
    assert "DISTINCT topic" in prompt


def test_reply_quote_targets_default_to_300():
    # posts unchanged, replies + quotes bumped to 300 (env overrides unset)
    assert P.POSTS_TARGET == 100
    assert P.REPLIES_TARGET == 300
    assert P.QUOTES_TARGET == 300


def test_voice_header_injects_learned_state():
    orig = P._learned_state
    try:
        P._learned_state = lambda: ("## LEARNED — drafts you've kept (match this texture)\n"
                                    "- kept one\n\n"
                                    "## LEARNED — drafts I rejected, do NOT write like these\n"
                                    "- rejected one\n")
        header = P._voice_header({"top_keywords": ["agents"], "top_accounts": ["@x"]}, [])
        assert "kept one" in header
        assert "rejected one" in header
    finally:
        P._learned_state = orig


def test_voice_header_empty_learned_state_is_noop():
    orig = P._learned_state
    try:
        P._learned_state = lambda: ""
        header = P._voice_header({"top_keywords": ["agents"], "top_accounts": ["@x"]}, [])
        assert "LEARNED" not in header
    finally:
        P._learned_state = orig


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            # skip fixture-dependent tests in bare mode
            if fn.__code__.co_argcount:
                print(f"SKIP (needs pytest fixtures) {fn.__name__}")
                continue
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
