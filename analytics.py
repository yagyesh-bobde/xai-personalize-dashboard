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
