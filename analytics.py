"""X post analytics: daily metric snapshots + deterministic breakdowns + an
LLM narrative. Mirrors eval_engine.py. All state lives in data/ (gitignored).
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "data" / "analytics.json"
HISTORY_PATH = ROOT / "data" / "analytics_history.json"
POSTED_PATH = ROOT / "data" / "posted.json"

WINDOW_DAYS = 30
MIN_SUPPORT = 3
MIN_VIEWS = 50
FETCH_N = 150
CADENCE_HOURS = 24

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "is", "are", "was",
    "were", "be", "been", "to", "of", "in", "on", "for", "with", "at", "by",
    "from", "it", "this", "that", "these", "those", "i", "you", "he", "she",
    "we", "they", "my", "your", "its", "as", "so", "just", "not", "no", "do",
    "does", "did", "have", "has", "had", "will", "would", "can", "could", "all",
}


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


def eng_rate(metrics: dict) -> float:
    m = metrics or {}
    engaged = (m.get("likes", 0) + m.get("retweets", 0) + m.get("replies", 0)
               + m.get("quotes", 0) + m.get("bookmarks", 0))
    return engaged / max(m.get("views", 0) or 0, 1)


def classify_length(text: str) -> str:
    n = len(text or "")
    if n < 100:
        return "short"
    if n <= 200:
        return "medium"
    return "long"


def has_link(text: str, urls) -> bool:
    if urls:
        return True
    return "http://" in (text or "") or "https://" in (text or "")


def tokenize(text: str) -> list:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[@#]\w+", " ", cleaned)
    words = re.findall(r"[a-z][a-z'-]+", cleaned.lower())
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


def local_hour(created_local: str) -> int:
    return int(created_local[11:13])


def local_weekday(created_local: str) -> int:
    return datetime.strptime(created_local, "%Y-%m-%d %H:%M").weekday()


def load_history() -> dict:
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(hist: dict) -> None:
    _atomic_write_json(HISTORY_PATH, hist)


def _kind_map() -> dict:
    """tweet_id -> kind/source from posted.json (best-effort attribution)."""
    try:
        posted = json.loads(POSTED_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for p in posted:
        tid = p.get("tweet_id")
        if tid:
            out[tid] = {"kind": p.get("kind", "post"), "source": p.get("source", "unknown")}
    return out


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def snapshot_metrics(now: datetime, fetcher=None) -> dict:
    fetcher = fetcher or _default_fetcher
    hist = load_history()
    kinds = _kind_map()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    today = now.date().isoformat()
    for raw in (fetcher() or []):
        if raw.get("isRetweet"):
            continue
        tid = raw.get("id")
        created = _parse_iso(raw.get("createdAtISO"))
        if not tid or not created or created < cutoff:
            continue
        attrib = kinds.get(tid, {})
        kind = attrib.get("kind", "post")
        if kind == "reply":            # v1 excludes replies
            continue
        text = raw.get("text", "")
        entry = hist.setdefault(tid, {"snapshots": []})
        entry.update({
            "created_at": raw.get("createdAtISO"),
            "created_local": raw.get("createdAtLocal"),
            "kind": kind,
            "source": attrib.get("source", "unknown"),
            "text": text,
            "has_media": bool(raw.get("media")),
            "has_link": has_link(text, raw.get("urls")),
            "lang": raw.get("lang"),
        })
        snaps = entry["snapshots"]
        if snaps and (_parse_iso(snaps[-1]["ts"]) or now).date().isoformat() == today:
            continue                   # already snapshotted today
        m = raw.get("metrics", {})
        snaps.append({"ts": now.isoformat(), "likes": m.get("likes", 0),
                      "retweets": m.get("retweets", 0), "replies": m.get("replies", 0),
                      "quotes": m.get("quotes", 0), "views": m.get("views", 0),
                      "bookmarks": m.get("bookmarks", 0)})
    save_history(hist)
    return hist


def _default_fetcher():
    import pipeline
    res = pipeline.twitter_json(["user-posts", f"@{pipeline.USERNAME}", "-n", str(FETCH_N)], timeout=150)
    if isinstance(res, dict):
        return res.get("data") or []
    return res or []


def _avg(nums):
    nums = list(nums)
    return sum(nums) / len(nums) if nums else 0.0


def _group(recs, keyfn):
    out = {}
    for r in recs:
        k = keyfn(r)
        out.setdefault(k, []).append(r)
    return {str(k): {"avg_eng_rate": round(_avg(x["eng_rate"] for x in v), 4),
                     "avg_views": round(_avg(x["views"] for x in v), 1),
                     "count": len(v)}
            for k, v in out.items()}


def compute_report(history: dict, now, window_days=WINDOW_DAYS) -> dict:
    cutoff = now - timedelta(days=window_days)
    recs = []
    for tid, e in history.items():
        if not e.get("snapshots"):
            continue
        created = _parse_iso(e.get("created_at"))
        if not created or created < cutoff:
            continue
        snap = e["snapshots"][-1]
        recs.append({
            "id": tid, "text": e.get("text", ""), "kind": e.get("kind", "post"),
            "has_media": e.get("has_media", False), "has_link": e.get("has_link", False),
            "created_local": e.get("created_local", ""),
            "views": snap.get("views", 0), "eng_rate": eng_rate(snap),
        })

    overall_rate = _avg(r["eng_rate"] for r in recs)
    breakdowns = {
        "type":    _group(recs, lambda r: r["kind"]),
        "media":   _group(recs, lambda r: "with_media" if r["has_media"] else "text_only"),
        "link":    _group(recs, lambda r: "with_link" if r["has_link"] else "no_link"),
        "length":  _group(recs, lambda r: classify_length(r["text"])),
        "hour":    _group([r for r in recs if r["created_local"]], lambda r: local_hour(r["created_local"])),
        "weekday": _group([r for r in recs if r["created_local"]], lambda r: local_weekday(r["created_local"])),
    }

    # keyword lift
    tok_rates = {}
    for r in recs:
        for tok in set(tokenize(r["text"])):
            tok_rates.setdefault(tok, []).append(r["eng_rate"])
    keywords = []
    for tok, rates in tok_rates.items():
        if len(rates) < MIN_SUPPORT:
            continue
        avg = _avg(rates)
        keywords.append({"token": tok, "support": len(rates),
                         "avg_eng_rate": round(avg, 4),
                         "lift": round(avg / overall_rate, 2) if overall_rate else 0.0})
    keywords.sort(key=lambda k: k["lift"], reverse=True)

    ranked = sorted((r for r in recs if r["views"] >= MIN_VIEWS),
                    key=lambda r: r["eng_rate"], reverse=True)

    def card(r):
        return {"id": r["id"], "text": r["text"], "kind": r["kind"],
                "eng_rate": round(r["eng_rate"], 4), "views": r["views"],
                "created_local": r["created_local"]}

    return {
        "n_posts": len(recs),
        "metric": "engagement_rate",
        "overall": {"avg_eng_rate": round(overall_rate, 4),
                    "avg_views": round(_avg(r["views"] for r in recs), 1)},
        "breakdowns": breakdowns,
        "keywords": keywords[:20],
        "top": [card(r) for r in ranked[:5]],
        "bottom": [card(r) for r in ranked[-5:][::-1]],
    }


def build_insight_prompt(report: dict) -> str:
    def cards(items):
        return "\n".join(
            f"- [{c['kind']}] eng_rate={c['eng_rate']} views={c['views']} :: {c['text']}"
            for c in items) or "(none)"
    kw = ", ".join(f"{k['token']}(x{k['lift']})" for k in report["keywords"][:12]) or "(none)"
    return (
        "You analyze what's working on an X (Twitter) account. Engagement rate = "
        "(likes+rts+replies+quotes+bookmarks)/views.\n\n"
        f"## Best performers\n{cards(report['top'])}\n\n"
        f"## Worst performers\n{cards(report['bottom'])}\n\n"
        f"## High-lift keywords\n{kw}\n\n"
        f"## Format/timing breakdowns (avg_eng_rate, count)\n"
        f"{json.dumps(report['breakdowns'], ensure_ascii=False)}\n\n"
        "## Task\nReturn JSON ONLY (no fences, start with `{` end with `}`):\n"
        "{\n"
        '  "themes_working": ["<themes/topics that resonate; 0-5>"],\n'
        '  "themes_flat": ["<themes that fall flat; 0-5>"],\n'
        '  "timing_insight": "<1-2 sentences on best posting times>",\n'
        '  "format_insight": "<1-2 sentences on post type/length/media/link>",\n'
        '  "recommendations": ["<concrete next-post suggestions; 2-5>"]\n'
        "}"
    )


def _default_caller(prompt: str):
    import pipeline
    return pipeline._claude_json(prompt, timeout=300, label="analytics")


def load_report() -> dict:
    try:
        return json.loads(REPORT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _should_run(now: datetime):
    rep = load_report()
    last = _parse_iso(rep.get("generated_at")) if rep else None
    if last and (now - last) < timedelta(hours=CADENCE_HOURS):
        return False, "cadence"
    return True, ""


def run_analytics(force=False, now=None, fetcher=None, caller=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not force:
        ok, reason = _should_run(now)
        if not ok:
            return {"skipped": reason}
    caller = caller or _default_caller
    hist = snapshot_metrics(now, fetcher=fetcher)
    report = compute_report(hist, now)
    try:
        report["insights"] = caller(build_insight_prompt(report)) or None
    except Exception:
        report["insights"] = None
    report["ts"] = now.isoformat()
    report["generated_at"] = now.isoformat()
    report["window_days"] = WINDOW_DAYS
    _atomic_write_json(REPORT_PATH, report)
    return report


def overview() -> dict:
    return load_report()
