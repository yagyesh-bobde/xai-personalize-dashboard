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
