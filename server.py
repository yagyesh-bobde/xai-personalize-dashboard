#!/usr/bin/env python3
"""
Yagyesh Dashboard — local HTTP server.

Routes:
  GET  /            → dashboard HTML
  GET  /static/<f>  → static assets
  GET  /uploads/<f> → uploaded image (served back for preview)
  GET  /data        → current dashboard_data.json
  POST /refresh     → re-run pipeline.py; returns new data on completion
  POST /post        → body {kind, text, target_id?, image_paths?} → twitter CLI now
  POST /schedule    → body {kind, text, target_id?, image_paths?, fire_at_iso}
  GET  /scheduled   → list of pending scheduled items
  DELETE /scheduled/<id> → cancel a scheduled item
  GET  /history     → list of posted items
  POST /upload      → multipart file → returns {path, url}
  GET  /healthz     → {ok: true}
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import blog as blog_mod

ROOT      = Path(__file__).resolve().parent
DATA_DIR  = ROOT / "data"
DATA      = DATA_DIR / "dashboard_data.json"
SCHED     = DATA_DIR / "scheduled.json"
POSTED    = DATA_DIR / "posted.json"
UPLOADS   = DATA_DIR / "uploads"
STATIC    = ROOT / "static"
PIPELINE  = ROOT / "pipeline.py"
_AGENT_NAME = (os.environ.get("DASHBOARD_AGENT") or "voice").strip()
_AGENT_MD_ENV = os.environ.get("DASHBOARD_AGENT_MD")
AGENT_MD  = Path(_AGENT_MD_ENV) if _AGENT_MD_ENV else (Path.home() / ".claude" / "agents" / f"{_AGENT_NAME}.md")

DATA_DIR.mkdir(exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
for f in (SCHED, POSTED):
    if not f.exists():
        f.write_text("[]")

PORT = int(os.environ.get("DASHBOARD_PORT", os.environ.get("YAGYESH_DASHBOARD_PORT", "7873")))
ENV_PATH = Path.home() / ".agent-reach" / "env.sh"

_refresh_lock = threading.Lock()
_file_lock    = threading.Lock()
_queue_lock   = threading.Lock()

QUEUE_INTERVAL_HOURS = 3
QUEUE_MIN_LEAD_SECONDS = 60


# ──────────────────────  helpers  ──────────────────────


def env_for_twitter() -> dict:
    env = os.environ.copy()
    if not ENV_PATH.exists():
        return env
    proc = subprocess.run(
        ["bash", "-c", f"set -a; source {ENV_PATH}; env"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1); env[k] = v
    return env


TW_ENV = env_for_twitter()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    with _file_lock:
        try:
            return json.loads(path.read_text() or "null") or default
        except Exception:
            return default


def save_json(path: Path, obj):
    with _file_lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
        tmp.replace(path)


def run_pipeline() -> tuple[bool, str]:
    if not _refresh_lock.acquire(blocking=False):
        return False, "refresh already in progress"
    try:
        proc = subprocess.run(
            [sys.executable, str(PIPELINE)],
            capture_output=True, text=True, timeout=300,
        )
        log = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode == 0, log[-2000:]
    except subprocess.TimeoutExpired:
        return False, "pipeline timed out after 5 minutes"
    finally:
        _refresh_lock.release()


def post_tweet(kind: str, text: str, target_id: str | None,
               image_paths: list[str] | None = None) -> tuple[bool, dict | str]:
    twitter_bin = shutil.which("twitter", path=TW_ENV.get("PATH"))
    if not twitter_bin:
        return False, "twitter CLI not found"
    text = (text or "").strip()
    if not text:
        return False, "empty text"

    if kind == "post":
        cmd = [twitter_bin, "post", text]
    elif kind == "reply":
        if not target_id:
            return False, "reply requires target_id"
        cmd = [twitter_bin, "post", text, "--reply-to", target_id]
    elif kind == "quote":
        if not target_id:
            return False, "quote requires target_id"
        cmd = [twitter_bin, "quote", target_id, text]
    else:
        return False, f"unknown kind: {kind}"

    for p in (image_paths or [])[:4]:
        if p and Path(p).exists():
            cmd += ["--image", str(p)]
    cmd += ["--json"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=TW_ENV, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "twitter command timed out"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or proc.stdout.strip())[:400] or "twitter command failed"

    try:
        result = json.loads(proc.stdout)
    except Exception:
        result = {"raw": proc.stdout.strip()}
    return True, result


def log_posted(kind: str, text: str, target_id: str | None,
               image_paths: list[str] | None, result, source: str = "manual"):
    posted = load_json(POSTED, [])
    # try to extract tweet id/url from result
    tweet_id  = None
    tweet_url = None
    if isinstance(result, dict):
        tweet_id = (
            (result.get("data") or {}).get("id")
            or result.get("id")
            or (result.get("tweet") or {}).get("id")
        )
        tweet_url = result.get("url") or (result.get("data") or {}).get("url")
        if not tweet_url and tweet_id:
            tweet_url = f"https://x.com/i/status/{tweet_id}"
    entry = {
        "id": uuid.uuid4().hex[:10],
        "posted_at": now_iso(),
        "kind": kind,
        "text": text,
        "target_id": target_id,
        "image_paths": image_paths or [],
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
        "source": source,
    }
    posted.insert(0, entry)
    save_json(POSTED, posted[:200])  # cap history
    return entry


# ──────────────────────  queue cadence  ──────────────────────


def compute_next_queue_slot() -> tuple[datetime, int, datetime | None]:
    """
    Returns (next_fire_at, pending_count, latest_pending_or_None).

    Anchors against the LATEST pending item (manual + auto schedules — one unified queue).
    Empty queue → now + 3h. Result is clamped to now + 1min minimum.
    """
    queue = load_json(SCHED, [])
    pending_times: list[datetime] = []
    for item in queue:
        if item.get("status") != "pending":
            continue
        try:
            t = datetime.fromisoformat(item["fire_at_iso"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            pending_times.append(t)
        except Exception:
            continue
    now = datetime.now(timezone.utc)
    latest = max(pending_times) if pending_times else None
    base = latest if latest else now
    nxt = base + timedelta(hours=QUEUE_INTERVAL_HOURS)
    floor = now + timedelta(seconds=QUEUE_MIN_LEAD_SECONDS)
    if nxt < floor:
        nxt = floor
    return nxt, len(pending_times), latest


# ──────────────────────  agent persona helpers  ──────────────────────


_agent_lock = threading.Lock()

VN_HEADER = "## VOICE NEIGHBORHOOD — WHO TO SOUND ADJACENT TO (NOT IMITATE)"
RP_HEADER = "## REACH PATTERNS — VIRALITY-TUNED TWEET TEMPLATES"
EMOJI_RULE_TAG = "**Tweet emoji palette:**"
ABBREV_RULE_TAG = "**Casual abbreviations are part of the brand."
INTENS_RULE_TAG = "**Intensifier vocabulary (sparingly, on-brand):**"


def parse_voice_neighborhood(content: str) -> list[dict]:
    """Parse the VOICE NEIGHBORHOOD section bullets into [{handle, url, note}]."""
    try:
        i = content.index(VN_HEADER)
    except ValueError:
        return []
    # take everything until the next H2 or end
    rest = content[i + len(VN_HEADER):]
    nxt = re.search(r"\n##\s", rest)
    block = rest if nxt is None else rest[:nxt.start()]
    out = []
    seen = set()
    for line in block.splitlines():
        m = re.match(r"\s*-\s+\*\*`@?([A-Za-z0-9_]+)`\*\*\s*[—-]\s*(.+?)\s*$", line)
        if m:
            handle = m.group(1)
            if handle in seen:
                continue
            seen.add(handle)
            out.append({
                "handle": "@" + handle,
                "url": f"https://x.com/{handle}",
                "note": m.group(2).strip(),
            })
    return out


def has_yaml_front_matter(content: str) -> bool:
    if not content.startswith("---"):
        return False
    # ensure a closing --- on its own line within the first ~5000 chars
    end = content.find("\n---", 3)
    return end != -1 and end < 8000


def write_agent_md(content: str) -> None:
    """Atomic write. Ensures YAML front matter is intact before writing."""
    if not has_yaml_front_matter(content):
        raise ValueError("missing or invalid YAML front matter (must start with --- and have a closing ---)")
    tmp = AGENT_MD.with_suffix(AGENT_MD.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(AGENT_MD)


def patch_agent_md(analysis: dict, handle: str) -> tuple[str, list[str]]:
    """
    Merge a voice-mining analysis into the agent .md file. Returns (new_content, diff_summary).

    Mutates in this order:
      - add a VOICE NEIGHBORHOOD bullet (if handle not present)
      - extend rule 9 (abbreviations) with vocab_additions
      - extend rule 8b (intensifiers) with intensifier-flavored vocab (if any)
      - extend rule 20 (emoji palette) with emoji_additions
      - add new template under REACH PATTERNS (template_additions)
    """
    content = AGENT_MD.read_text()
    diff: list[str] = []
    handle_norm = handle.lstrip("@")

    # 1) voice neighborhood bullet
    existing = {p["handle"].lower() for p in parse_voice_neighborhood(content)}
    if f"@{handle_norm.lower()}" not in existing:
        note = (analysis.get("summary") or analysis.get("distinctive") or "voice-mined profile").strip()
        note = re.sub(r"\s+", " ", note)[:240]
        new_bullet = f"- **`@{handle_norm}`** — {note}"
        # insert as last bullet in the VN section, before the `---` separator that follows
        try:
            vn_i = content.index(VN_HEADER)
        except ValueError:
            raise ValueError("VOICE NEIGHBORHOOD section not found in agent file")
        # find end of VN block (next \n--- or next ## )
        tail = content[vn_i:]
        m = re.search(r"\n---\s*\n", tail)
        if m:
            insert_at = vn_i + m.start()
        else:
            m2 = re.search(r"\n##\s", tail[len(VN_HEADER):])
            insert_at = (vn_i + len(VN_HEADER) + m2.start()) if m2 else len(content)
        # ensure exactly one newline before our bullet
        before = content[:insert_at].rstrip() + "\n"
        after = content[insert_at:]
        content = before + new_bullet + "\n" + after
        diff.append(f"added @{handle_norm} to VOICE NEIGHBORHOOD")

    # 2) vocab_additions → rule 9
    vocab = [v.strip() for v in (analysis.get("vocab_additions") or []) if v and isinstance(v, str)]
    vocab = [v for v in vocab if v.lower() not in content.lower()]
    if vocab:
        line_idx = content.find(ABBREV_RULE_TAG)
        if line_idx != -1:
            line_end = content.find("\n", line_idx)
            if line_end != -1:
                addition = ", " + ", ".join(f'"{v}"' for v in vocab[:8])
                # insert right before the trailing period+space+next sentence — simplest: append before newline
                content = content[:line_end] + addition + content[line_end:]
                diff.append(f"vocab+: {', '.join(vocab[:8])}")

    # 3) emoji_additions → rule 20
    emoji = [e.strip() for e in (analysis.get("emoji_additions") or []) if e and isinstance(e, str)]
    emoji = [e for e in emoji if e and e not in content]
    if emoji:
        line_idx = content.find(EMOJI_RULE_TAG)
        if line_idx != -1:
            line_end = content.find("\n", line_idx)
            if line_end != -1:
                addition = ", " + ", ".join(emoji[:6])
                content = content[:line_end] + addition + content[line_end:]
                diff.append(f"emoji+: {' '.join(emoji[:6])}")

    # 4) template_additions → new REACH PATTERN entry
    templates = analysis.get("template_additions") or []
    if templates and isinstance(templates, list):
        # count existing Template N — to pick next index
        used = re.findall(r"###\s+Template\s+(\d+)", content)
        next_n = max((int(x) for x in used), default=10) + 1
        rp_i = content.find(RP_HEADER)
        if rp_i != -1:
            tail = content[rp_i:]
            # insert before the next "\n---" after RP_HEADER
            sep = re.search(r"\n---\s*\n", tail)
            insert_at = (rp_i + sep.start()) if sep else len(content)
            block_lines = []
            for t in templates[:2]:  # cap
                if not isinstance(t, dict):
                    continue
                name = (t.get("name") or "Mined Pattern").strip()
                desc = (t.get("description") or "").strip()
                example = (t.get("example") or "").strip()
                lines = [f"\n### Template {next_n} — {name} (mined from @{handle_norm})"]
                if desc: lines.append(desc)
                if example:
                    for ln in example.splitlines():
                        lines.append(f"> {ln}" if ln.strip() else ">")
                block_lines.append("\n".join(lines))
                next_n += 1
            if block_lines:
                content = content[:insert_at].rstrip() + "\n\n" + "\n\n".join(block_lines) + "\n" + content[insert_at:]
                diff.append(f"reach template+: {len(block_lines)} new (#{next_n - len(block_lines)})")

    return content, diff


def fetch_user_posts(handle: str, n: int = 50) -> list[dict] | None:
    twitter_bin = shutil.which("twitter", path=TW_ENV.get("PATH"))
    if not twitter_bin:
        return None
    handle = handle if handle.startswith("@") else "@" + handle
    cmd = [twitter_bin, "-c", "user-posts", handle, "-n", str(n)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=TW_ENV, timeout=90)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        out = json.loads(proc.stdout)
    except Exception:
        return None
    if isinstance(out, dict):
        out = out.get("posts") or out.get("data") or out.get("tweets") or []
    return out if isinstance(out, list) else None


VOICE_MINE_PROMPT = """You are analyzing the voice of an X (Twitter) user so that another writer can sound *adjacent* to them.

Read the posts below (JSON). Extract patterns. Return STRICT JSON only — no prose, no code fences.

Schema:
{
  "summary": "<one sentence, ≤180 chars, what makes this account's voice distinctive — usable as a single-line neighborhood note>",
  "openers": ["<short opener phrases this account reaches for>", ...],
  "rhythm": "<sentence-level rhythm in one short clause: short/long, em-dash heavy, question-led, etc.>",
  "vocab_additions": ["<short slangy words/phrases this account uses that would extend an Indian-builder writer's vocabulary — max 8, no duplicates of common words>", ...],
  "emoji_additions": ["<single-char emojis this account uses with semantic load — max 6>", ...],
  "template_additions": [
    {"name": "<short Title Case label>", "description": "<one sentence on the pattern>", "example": "<verbatim short example from the posts>"}
  ],
  "distinctive": "<1-3 sentences: what to borrow vs. what NOT to borrow (their personal-brand baggage)>"
}

Rules:
- "template_additions" should be EMPTY if no genuinely novel reach pattern exists.
- Do not include patterns already obvious (e.g. "uses lowercase tweets" — every Indian builder does).
- Quote examples verbatim; do not paraphrase.
- Output JSON only. Start with { and end with }.

Handle: @__HANDLE__

Posts (JSON):
__POSTS_JSON__
"""


def call_claude_plain(prompt: str, timeout: int = 240) -> tuple[bool, str]:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False, "claude CLI not found on PATH"
    try:
        proc = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "claude call timed out"
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()[:600]
    return True, proc.stdout


def extract_json_blob(blob: str) -> dict | None:
    blob = blob.strip()
    blob = re.sub(r"^```(?:json)?\s*", "", blob)
    blob = re.sub(r"\s*```$", "", blob)
    start = blob.find("{")
    end = blob.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(blob[start:end+1])
    except json.JSONDecodeError:
        return None


# ──────────────────────  multipart parser  ──────────────────────


def parse_multipart(body: bytes, boundary: str) -> list[dict]:
    """
    Minimal RFC 7578 multipart/form-data parser. stdlib `cgi` was removed in
    Python 3.13, and email.parser silently re-encodes binary content, so we
    do it ourselves.

    Returns: list of {name, filename, content_type, data} dicts.
    """
    sep = ("--" + boundary).encode()
    # split on \r\n--boundary, but the first boundary has no preceding \r\n
    if body.startswith(sep):
        body = body[len(sep):]
    chunks = body.split(b"\r\n" + sep)

    out = []
    for raw in chunks:
        if not raw or raw in (b"--", b"--\r\n"):
            continue
        # strip leading CRLF after boundary
        raw = raw.lstrip(b"\r\n")
        if raw.startswith(b"--"):
            break  # closing boundary
        head_end = raw.find(b"\r\n\r\n")
        if head_end == -1:
            continue
        head = raw[:head_end].decode("utf-8", "replace")
        data = raw[head_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]

        info = {"data": data, "name": None, "filename": None, "content_type": None}
        for line in head.split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition:"):
                nm = re.search(r'\bname="([^"]*)"', line)
                fn = re.search(r'\bfilename="([^"]*)"', line)
                if nm: info["name"] = nm.group(1)
                if fn: info["filename"] = fn.group(1)
            elif low.startswith("content-type:"):
                info["content_type"] = line.split(":", 1)[1].strip()
        if info["name"]:
            out.append(info)
    return out


# ──────────────────────  scheduler thread  ──────────────────────


_sched_stop = threading.Event()


def _scheduler_loop():
    while not _sched_stop.is_set():
        try:
            queue = load_json(SCHED, [])
            now = datetime.now(timezone.utc)
            changed = False
            for item in queue:
                if item.get("status") != "pending":
                    continue
                try:
                    due = datetime.fromisoformat(item["fire_at_iso"])
                except Exception:
                    item["status"] = "failed"
                    item["error"]  = "invalid fire_at_iso"
                    changed = True
                    continue
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if now >= due:
                    ok, result = post_tweet(
                        item["kind"], item["text"], item.get("target_id"),
                        item.get("image_paths") or [],
                    )
                    if ok:
                        item["status"] = "fired"
                        item["fired_at"] = now_iso()
                        item["result"]   = result
                        log_posted(
                            item["kind"], item["text"], item.get("target_id"),
                            item.get("image_paths") or [], result, source="scheduled",
                        )
                    else:
                        item["status"] = "failed"
                        item["error"]  = result if isinstance(result, str) else json.dumps(result)[:300]
                    changed = True
            if changed:
                save_json(SCHED, queue)
        except Exception as e:
            sys.stderr.write(f"[scheduler] tick error: {e}\n")
        _sched_stop.wait(15)


def start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="dashboard-scheduler")
    t.start()
    return t


# ──────────────────────  HTTP  ──────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = "YagyeshDashboard/0.2"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[server] {self.address_string()} — {fmt % args}\n")

    # ---- response helpers ----

    def _send_json(self, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, mime: str):
        if not path.exists() or not path.is_file():
            self.send_error(404); return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return None

    # ---- routing ----

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path

        if path == "/" or path == "/index.html":
            return self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send_json(200, {"ok": True, "now": now_iso()})

        if path == "/data":
            if not DATA.exists():
                return self._send_json(200, {"empty": True, "message": "no data yet — click refresh."})
            try:
                return self._send_json(200, json.loads(DATA.read_text()))
            except Exception as e:
                return self._send_json(500, {"error": str(e)})

        if path == "/scheduled":
            return self._send_json(200, load_json(SCHED, []))

        if path == "/queue/preview":
            nxt, count, latest = compute_next_queue_slot()
            return self._send_json(200, {
                "next_fire_at_iso":   nxt.isoformat(),
                "pending_count":      count,
                "latest_pending_iso": latest.isoformat() if latest else None,
                "interval_hours":     QUEUE_INTERVAL_HOURS,
            })

        if path == "/history":
            return self._send_json(200, load_json(POSTED, []))

        if path == "/agent":
            if not AGENT_MD.exists():
                return self._send_json(404, {"error": f"agent file not found: {AGENT_MD}"})
            try:
                with _agent_lock:
                    content = AGENT_MD.read_text()
                    mtime = datetime.fromtimestamp(AGENT_MD.stat().st_mtime, timezone.utc).isoformat()
                profiles = parse_voice_neighborhood(content)
                return self._send_json(200, {
                    "path":     str(AGENT_MD),
                    "content":  content,
                    "mtime":    mtime,
                    "profiles": profiles,
                })
            except Exception as e:
                return self._send_json(500, {"error": str(e)})

        if path == "/blog/data":
            try:
                return self._send_json(200, blog_mod.load_state())
            except Exception as e:
                return self._send_json(500, {"error": str(e)})

        if path == "/blog-agent":
            try:
                return self._send_json(200, blog_mod.read_agent())
            except Exception as e:
                return self._send_json(500, {"error": str(e)})

        if path.startswith("/static/"):
            name = path[len("/static/"):]
            mime = (
                "text/css; charset=utf-8" if name.endswith(".css") else
                "application/javascript; charset=utf-8" if name.endswith(".js") else
                "image/svg+xml" if name.endswith(".svg") else
                "application/octet-stream"
            )
            return self._send_file(STATIC / name, mime)

        if path.startswith("/uploads/"):
            name = Path(path[len("/uploads/"):]).name
            ext = name.lower().rsplit(".", 1)[-1]
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
            return self._send_file(UPLOADS / name, mime)

        if path.startswith("/thumbnails/"):
            name = Path(path[len("/thumbnails/"):]).name
            ext = name.lower().rsplit(".", 1)[-1]
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
            return self._send_file(blog_mod.THUMBNAILS_DIR / name, mime)

        self.send_error(404)

    def do_DELETE(self):
        url = urlparse(self.path)
        path = url.path
        if path.startswith("/scheduled/"):
            sid = path[len("/scheduled/"):]
            queue = load_json(SCHED, [])
            for item in queue:
                if item.get("id") == sid and item.get("status") == "pending":
                    item["status"] = "cancelled"
                    item["cancelled_at"] = now_iso()
                    save_json(SCHED, queue)
                    return self._send_json(200, {"ok": True, "id": sid})
            return self._send_json(404, {"ok": False, "error": "not found or already fired"})

        if path.startswith("/blog/projects/"):
            pid = path[len("/blog/projects/"):]
            state = blog_mod.load_state()
            ok = blog_mod.remove_project(state, pid)
            return self._send_json(200 if ok else 404,
                                   {"ok": ok, "id": pid, "error": None if ok else "not found"})

        if path.startswith("/blog/ideas/"):
            iid = path[len("/blog/ideas/"):]
            state = blog_mod.load_state()
            ok = blog_mod.delete_idea(state, iid)
            return self._send_json(200 if ok else 404,
                                   {"ok": ok, "id": iid, "error": None if ok else "not found"})

        self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path

        if path == "/upload":
            return self._handle_upload()

        body = self._read_json_body()
        if body is None:
            return self._send_json(400, {"error": "invalid JSON body"})

        if path == "/refresh":
            ok, log = run_pipeline()
            if not ok:
                return self._send_json(500, {"ok": False, "log": log})
            try:
                payload = json.loads(DATA.read_text())
                payload["_log_tail"] = log[-600:]
                return self._send_json(200, payload)
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e), "log": log})

        if path == "/post":
            kind = body.get("kind")
            text = body.get("text") or ""
            target_id = body.get("target_id")
            image_paths = body.get("image_paths") or []
            ok, result = post_tweet(kind, text, target_id, image_paths)
            if ok:
                log_posted(kind, text, target_id, image_paths, result, source="manual")
            return self._send_json(200 if ok else 400, {"ok": ok, "result": result, "kind": kind})

        if path == "/bookmark":
            tweet_id = str(body.get("id") or "").strip()
            if not re.match(r"^\d{5,25}$", tweet_id):
                return self._send_json(400, {"ok": False, "error": "invalid tweet id"})
            twitter_bin = shutil.which("twitter", path=TW_ENV.get("PATH"))
            if not twitter_bin:
                return self._send_json(500, {"ok": False, "error": "twitter CLI not found"})
            try:
                proc = subprocess.run(
                    [twitter_bin, "bookmark", tweet_id, "--json"],
                    capture_output=True, text=True, env=TW_ENV, timeout=30,
                )
            except subprocess.TimeoutExpired:
                return self._send_json(504, {"ok": False, "error": "timed out"})
            if proc.returncode != 0:
                return self._send_json(400, {
                    "ok": False,
                    "error": ((proc.stderr or proc.stdout).strip() or "bookmark failed")[:400],
                })
            return self._send_json(200, {"ok": True, "id": tweet_id})

        if path == "/agent":
            new_content = body.get("content")
            if not isinstance(new_content, str) or not new_content.strip():
                return self._send_json(400, {"ok": False, "error": "content must be a non-empty string"})
            try:
                with _agent_lock:
                    write_agent_md(new_content)
                    mtime = datetime.fromtimestamp(AGENT_MD.stat().st_mtime, timezone.utc).isoformat()
                return self._send_json(200, {"ok": True, "mtime": mtime, "bytes": len(new_content)})
            except ValueError as e:
                return self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e)})

        if path == "/agent/study":
            handle = (body.get("username") or "").strip()
            if not handle:
                return self._send_json(400, {"ok": False, "error": "username required"})
            handle_norm = handle.lstrip("@")
            if not re.match(r"^[A-Za-z0-9_]{1,20}$", handle_norm):
                return self._send_json(400, {"ok": False, "error": "invalid handle"})

            posts = fetch_user_posts(handle_norm, n=50)
            if not posts:
                return self._send_json(502, {"ok": False, "error": f"no posts fetched for @{handle_norm}"})

            # trim posts payload to keep prompt under budget
            slim = []
            for p in posts[:50]:
                if not isinstance(p, dict):
                    continue
                slim.append({
                    "text":  (p.get("text") or p.get("content") or "")[:600],
                    "likes": p.get("likes") or p.get("favorites") or 0,
                    "rts":   p.get("rts") or p.get("retweets") or 0,
                    "time":  p.get("time") or p.get("created_at"),
                })
            prompt = (
                VOICE_MINE_PROMPT
                .replace("__HANDLE__", handle_norm)
                .replace("__POSTS_JSON__", json.dumps(slim, ensure_ascii=False, indent=1)[:30000])
            )
            ok, out = call_claude_plain(prompt, timeout=180)
            if not ok:
                return self._send_json(502, {"ok": False, "error": "claude call failed", "detail": out})
            analysis = extract_json_blob(out)
            if not analysis:
                return self._send_json(502, {
                    "ok": False, "error": "claude returned malformed JSON",
                    "raw": out[:1200],
                })
            try:
                with _agent_lock:
                    new_content, diff = patch_agent_md(analysis, handle_norm)
                    write_agent_md(new_content)
                    mtime = datetime.fromtimestamp(AGENT_MD.stat().st_mtime, timezone.utc).isoformat()
                return self._send_json(200, {
                    "ok": True,
                    "added_handle": "@" + handle_norm,
                    "diff_summary": diff,
                    "analysis":     analysis,
                    "mtime":        mtime,
                })
            except ValueError as e:
                return self._send_json(400, {"ok": False, "error": str(e), "analysis": analysis})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e), "analysis": analysis})

        if path == "/queue":
            kind = body.get("kind")
            text = (body.get("text") or "").strip()
            target_id = body.get("target_id")
            image_paths = body.get("image_paths") or []
            if not text:
                return self._send_json(400, {"ok": False, "error": "empty text"})
            if kind not in ("post", "reply", "quote"):
                return self._send_json(400, {"ok": False, "error": f"unknown kind: {kind}"})
            with _queue_lock:
                nxt, _, _ = compute_next_queue_slot()
                queue = load_json(SCHED, [])
                entry = {
                    "id": uuid.uuid4().hex[:10],
                    "kind": kind,
                    "text": text,
                    "target_id": target_id,
                    "image_paths": image_paths,
                    "fire_at_iso": nxt.isoformat(),
                    "created_at": now_iso(),
                    "status": "pending",
                    "source": "queue",
                }
                queue.insert(0, entry)
                save_json(SCHED, queue)
            return self._send_json(200, {"ok": True, "scheduled": entry, "interval_hours": QUEUE_INTERVAL_HOURS})

        if path == "/schedule":
            kind = body.get("kind")
            text = (body.get("text") or "").strip()
            target_id = body.get("target_id")
            image_paths = body.get("image_paths") or []
            fire_at_iso = body.get("fire_at_iso")
            if not text:
                return self._send_json(400, {"ok": False, "error": "empty text"})
            if kind not in ("post", "reply", "quote"):
                return self._send_json(400, {"ok": False, "error": f"unknown kind: {kind}"})
            try:
                due = datetime.fromisoformat(fire_at_iso)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
            except Exception:
                return self._send_json(400, {"ok": False, "error": "fire_at_iso must be ISO 8601"})
            if due <= datetime.now(timezone.utc):
                return self._send_json(400, {"ok": False, "error": "fire_at must be in the future"})

            queue = load_json(SCHED, [])
            entry = {
                "id": uuid.uuid4().hex[:10],
                "kind": kind,
                "text": text,
                "target_id": target_id,
                "image_paths": image_paths,
                "fire_at_iso": due.isoformat(),
                "created_at": now_iso(),
                "status": "pending",
            }
            queue.insert(0, entry)
            save_json(SCHED, queue)
            return self._send_json(200, {"ok": True, "scheduled": entry})

        # ── blog ideas routes ──

        if path == "/blog/scrape-medium":
            try:
                state = blog_mod.load_state()
                posts = blog_mod.refresh_medium(state)
                return self._send_json(200, {
                    "ok": True, "posts": posts,
                    "stripped_archive_ideas": True,
                    "state": state,
                })
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/projects":
            project_path = (body.get("path") or "").strip()
            name = (body.get("name") or "").strip() or None
            if not project_path:
                return self._send_json(400, {"ok": False, "error": "path required"})
            state = blog_mod.load_state()
            entry = blog_mod.add_project(state, project_path, name)
            sig = blog_mod.project_signal(entry["path"])
            return self._send_json(200, {"ok": True, "project": entry, "signal": sig})

        if path == "/blog/projects/scan":
            project_id = (body.get("id") or "").strip()
            state = blog_mod.load_state()
            proj = next((p for p in state["projects"] if p["id"] == project_id), None)
            if not proj:
                return self._send_json(404, {"ok": False, "error": "project not found"})
            sig = blog_mod.project_signal(proj["path"])
            return self._send_json(200, {"ok": True, "signal": sig})

        if path == "/blog/generate-ideas":
            state = blog_mod.load_state()
            try:
                ideas = blog_mod.generate_ideas(state)
                return self._send_json(200, {"ok": True, "ideas": ideas, "state": state})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/clear-ideas":
            state = blog_mod.load_state()
            removed = blog_mod.clear_ideas(state, keep_finalized=True)
            return self._send_json(200, {"ok": True, "removed": removed, "state": state})

        if path == "/blog/thumbnail":
            draft_id = (body.get("draft_id") or "").strip()
            if not draft_id:
                return self._send_json(400, {"ok": False, "error": "draft_id required"})
            additional   = (body.get("additional_text") or "").strip()
            ref_b64      = body.get("ref_image_b64") or None
            ref_name     = body.get("ref_image_name") or None
            prompt_over  = body.get("prompt_override") or None
            state = blog_mod.load_state()
            try:
                result = blog_mod.generate_thumbnail(
                    state, draft_id,
                    additional_text=additional,
                    ref_image_b64=ref_b64,
                    ref_image_name=ref_name,
                    prompt_override=prompt_over,
                )
                return self._send_json(200, result)
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/publish":
            draft_id = (body.get("draft_id") or "").strip()
            if not draft_id:
                return self._send_json(400, {"ok": False, "error": "draft_id required"})
            state = blog_mod.load_state()
            try:
                result = blog_mod.publish_draft(state, draft_id)
                return self._send_json(200, result)
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/research-trending":
            state = blog_mod.load_state()
            try:
                ideas = blog_mod.research_trending_ideas(state)
                return self._send_json(200, {"ok": True, "ideas": ideas, "state": state})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/finalize":
            idea_id = (body.get("id") or "").strip()
            state = blog_mod.load_state()
            idea = blog_mod.finalize_idea(state, idea_id)
            if not idea:
                return self._send_json(404, {"ok": False, "error": "idea not found"})
            return self._send_json(200, {"ok": True, "idea": idea})

        if path == "/blog/unfinalize":
            idea_id = (body.get("id") or "").strip()
            state = blog_mod.load_state()
            idea = blog_mod.unfinalize_idea(state, idea_id)
            if not idea:
                return self._send_json(404, {"ok": False, "error": "idea not found"})
            return self._send_json(200, {"ok": True, "idea": idea})

        if path == "/blog/variations":
            idea_id = (body.get("id") or "").strip()
            state = blog_mod.load_state()
            try:
                variations = blog_mod.generate_variations(state, idea_id)
                return self._send_json(200, {
                    "ok": True, "variations": variations,
                    "idea_id": idea_id, "state": state,
                })
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/draft":
            idea_id = (body.get("id") or "").strip()
            override_title = (body.get("title") or "").strip() or None
            state = blog_mod.load_state()
            try:
                draft = blog_mod.generate_draft(state, idea_id, override_title=override_title)
                return self._send_json(200, {
                    "ok": True, "draft": draft, "state": state,
                })
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e)})

        if path == "/blog/draft/save":
            draft_id = (body.get("id") or "").strip()
            content = body.get("content") or ""
            new_title = body.get("title")
            try:
                state = blog_mod.load_state()
                draft = blog_mod.update_draft_content(state, draft_id, content, title=new_title)
                return self._send_json(200, {"ok": True, "draft": draft})
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})

        if path == "/blog/comment":
            draft_id = (body.get("draft_id") or "").strip()
            text = (body.get("text") or "").strip()
            if not text:
                return self._send_json(400, {"ok": False, "error": "empty comment"})
            state = blog_mod.load_state()
            try:
                draft = blog_mod.add_comment_and_revise(state, draft_id, text)
                return self._send_json(200, {"ok": True, "draft": draft})
            except ValueError as e:
                return self._send_json(404, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(502, {"ok": False, "error": str(e), "draft_id": draft_id})

        if path == "/blog-agent":
            new_content = body.get("content")
            if not isinstance(new_content, str) or not new_content.strip():
                return self._send_json(400, {"ok": False, "error": "content required"})
            try:
                meta = blog_mod.write_agent(new_content)
                return self._send_json(200, {"ok": True, **meta})
            except ValueError as e:
                return self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                return self._send_json(500, {"ok": False, "error": str(e)})

        self.send_error(404)

    # ---- multipart upload ----

    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._send_json(400, {"ok": False, "error": "expected multipart/form-data"})

        m = re.search(r'boundary="?([^"; ]+)"?', ctype)
        if not m:
            return self._send_json(400, {"ok": False, "error": "missing multipart boundary"})
        boundary = m.group(1)

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send_json(400, {"ok": False, "error": "empty body"})
        body = self.rfile.read(length)

        parts = parse_multipart(body, boundary)
        saved = []
        for p in parts:
            if p.get("name") != "file":
                continue
            filename = p.get("filename") or ""
            if not filename:
                continue
            ext = Path(filename).suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            name = f"{uuid.uuid4().hex[:12]}{ext}"
            dst  = UPLOADS / name
            dst.write_bytes(p["data"])
            saved.append({
                "path": str(dst),
                "url":  f"/uploads/{name}",
                "name": filename,
                "size": dst.stat().st_size,
            })

        if not saved:
            return self._send_json(400, {"ok": False, "error": "no valid image files"})
        return self._send_json(200, {"ok": True, "files": saved})


# ──────────────────────  main  ──────────────────────


def main():
    if not STATIC.exists():
        sys.stderr.write(f"static/ not found at {STATIC}\n"); sys.exit(1)

    start_scheduler()
    print("[server] scheduler thread started", flush=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"[server] dashboard live at {url}", flush=True)
    print("[server] press Ctrl-C to stop", flush=True)
    if "--no-open" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down")
        _sched_stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
