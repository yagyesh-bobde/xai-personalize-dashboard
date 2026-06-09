"""linkedin_cli.py — LinkedIn I/O via the cmux inline browser CLI.

Reading is headless & reliable. Interactive posting (prefill_composer) requires
the LinkedIn cmux pane to be the on-screen/active pane. eval/find-role are
UNSUPPORTED on cmux's WKWebView — use snapshot refs + CSS selectors only.
"""
import os, re, shutil, subprocess, time

HANDLE = (os.environ.get("LINKEDIN_HANDLE") or "").strip().lstrip("@")
_DEFAULT_CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"

def cmux_bin() -> str | None:
    if os.path.exists(_DEFAULT_CMUX):
        return _DEFAULT_CMUX
    return shutil.which("cmux")

def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    b = cmux_bin()
    if not b:
        return 127, "", "cmux not found"
    try:
        p = subprocess.run([b, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


_SURFACE = None  # cached "surface:N"

def resolve_surface() -> str | None:
    """Open (or reuse) a LinkedIn browser surface; return its ref."""
    global _SURFACE
    if _SURFACE:
        return _SURFACE
    import json
    rc, out, _ = _run(["--json", "browser", "open", "https://www.linkedin.com/feed/"], timeout=40)
    if rc != 0:
        return None
    try:
        _SURFACE = json.loads(out).get("surface_ref")
    except Exception:
        _SURFACE = None
    return _SURFACE

def _b(surface: str, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    return _run(["browser", surface, *args], timeout=timeout)

def navigate(surface: str, url: str):
    _b(surface, "navigate", url, timeout=40)
    _b(surface, "wait", "--load-state", "complete", "--timeout-ms", "20000", timeout=25)
    # LinkedIn is an SPA — title + feed content render after load-state completes.
    # Give it a short settle so headless reads see real data, not the loading shell.
    time.sleep(2.5)

def get_text(surface: str, css: str) -> str:
    rc, out, _ = _b(surface, "get", "text", "--selector", css)
    return out.strip() if rc == 0 else ""


def is_logged_in() -> bool:
    s = resolve_surface()
    if not s:
        return False
    navigate(s, "https://www.linkedin.com/feed/")
    # title is set late by the SPA; nudge it to settle, then read
    _b(s, "wait", "--text", "Feed", "--timeout-ms", "10000")
    rc, out, _ = _b(s, "get", "title")
    return rc == 0 and "Feed | LinkedIn" in out

def profile() -> dict:
    s = resolve_surface()
    if not s:
        return {}
    navigate(s, "https://www.linkedin.com/in/me/")
    rc, url, _ = _b(s, "get", "url")
    handle = HANDLE
    m = re.search(r"/in/([^/?]+)", url or "")
    if m:
        handle = m.group(1)
    rc2, title, _ = _b(s, "get", "title")
    name = (title.split("|")[0].strip() if rc2 == 0 else "")
    return {"handle": handle, "name": name}

# Markers in the LinkedIn accessibility/DOM text that mean a feed item is NOT
# the user's own original writing (a repost/reaction of someone else's content).
_REPOST_MARKERS = (
    "reposted this", "commented on this", "likes this", "loves this",
    "celebrates this", "supports this", "finds this", "funny", "insightful",
)
# Engagement/UI chrome that trails a post body — cut everything from the first hit.
_TRAILING_CHROME = re.compile(
    r"(Activate to view larger image|visible to anyone|\bLike\b\s+\bComment\b|"
    r"\bRepost\b\s+\bSend\b|Add a comment|Reactions?\b|\d+\s+comments?\b|"
    r"\d+\s+reposts?\b|Show more results)", re.I)
# A "Nd • / Nw • / Nmo • (Edited •)" timestamp that separates the author header
# from the post body.
_TIME_SEP = re.compile(r"\b\d+\s*(?:mo|yr|d|w|h|m|s)\b\s*•\s*(?:Edited\s*•\s*)?", re.I)


def _strip_tags(html: str) -> str:
    import html as _h
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return _h.unescape(t)


def _clean_post(seg: str) -> str:
    """Turn one raw feed-item text segment into just the post body."""
    seg = re.sub(r"\s+", " ", seg).strip()
    m = _TIME_SEP.search(seg)
    if m:                       # drop the "Name • headline Nd • Edited •" header
        seg = seg[m.end():]
    c = _TRAILING_CHROME.search(seg)
    if c:                       # drop the engagement bar and everything after
        seg = seg[:c.start()]
    seg = re.sub(r"…?\s*more$", "", seg).strip()
    seg = re.sub(r"\bhashtag\b", "", seg)
    return seg.strip(" •·–—-").strip()


def my_posts(limit: int = 20) -> list[str]:
    """Scrape the user's OWN original posts from the activity feed.

    cmux WKWebView reads are headless, but `get text --selector` returns only the
    first match, so we pull the whole feed via `get html`, split it into feed
    items by the 'Feed post number N' markers LinkedIn emits, and KEEP ONLY the
    user's own authored items — reposts/reactions of other people's content are
    filtered out (they are NOT the user's voice). Note: LinkedIn's infinite
    scroll only lazy-loads a handful of items when the pane is off-screen, so
    this returns the reachable recent originals, not full history."""
    s = resolve_surface()
    if not s:
        return []
    h = (profile().get("handle") or HANDLE or "me")
    navigate(s, f"https://www.linkedin.com/in/{h}/recent-activity/all/")
    _b(s, "wait", "--text", "Activity", "--timeout-ms", "10000")
    time.sleep(1.5)
    for _ in range(6):          # nudge lazy-load as far as a hidden pane allows
        _b(s, "scroll", "--dy", "2000")
        time.sleep(0.4)
    rc, html, _ = _b(s, "get", "html", "--selector", "main", timeout=25)
    if rc != 0 or not html:
        return []
    text = _strip_tags(html)
    segments = re.split(r"Feed post number \d+", text)[1:]
    seen, posts = set(), []
    for seg in segments:
        head = re.sub(r"\s+", " ", seg)[:90].lower()
        if any(mark in head for mark in _REPOST_MARKERS):
            continue            # someone else's content — skip
        body = _clean_post(seg)
        key = re.sub(r"\s+", " ", body).strip().lower()[:120]
        if len(body) >= 20 and key not in seen:
            seen.add(key)
            posts.append(body)
        if len(posts) >= limit:
            break
    return posts


def prefill_composer(text: str) -> dict:
    """Open LinkedIn composer and fill `text`. Does NOT click Post.
    Requires the LinkedIn cmux pane to be on-screen (headless clicks don't open
    the modal). Returns {ok:bool, reason?:str, hint?:str}."""
    s = resolve_surface()
    if not s:
        return {"ok": False, "reason": "no_surface", "hint": "cmux browser unavailable"}
    navigate(s, "https://www.linkedin.com/feed/")
    rc, snap, _ = _b(s, "snapshot", "--max-depth", "30", timeout=20)
    m = re.search(r'button "Start a post".*?\[ref=(e\d+)\]', snap)
    if not m:
        return {"ok": False, "reason": "no_trigger", "hint": "could not find 'Start a post'"}
    _b(s, "click", m.group(1))
    rc, _, _ = _b(s, "wait", "--text", "talk about", "--timeout-ms", "6000")
    if rc != 0:
        return {"ok": False, "reason": "pane_hidden",
                "hint": "Bring your LinkedIn pane to the front in cmux, then retry."}
    # editor is a contenteditable; target by its placeholder/role container
    rc2, snap2, _ = _b(s, "snapshot", "--max-depth", "40", timeout=20)
    em = re.search(r'(?:textbox|paragraph|generic).*?talk about.*?\[ref=(e\d+)\]', snap2)
    if em:
        _b(s, "click", em.group(1))
    _b(s, "type", "--selector", ".ql-editor", "--text", text)
    return {"ok": True}
