"""linkedin_cli.py — LinkedIn I/O via the cmux inline browser CLI.

Reading is headless & reliable. Interactive posting (prefill_composer) requires
the LinkedIn cmux pane to be the on-screen/active pane. eval/find-role are
UNSUPPORTED on cmux's WKWebView — use snapshot refs + CSS selectors only.
"""
import os, re, shutil, subprocess, sys, time

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
    rc, out, err = _run(["--json", "browser", "open", "https://www.linkedin.com/feed/"], timeout=40)
    if rc != 0:
        sys.stderr.write(f"[linkedin_cli] resolve_surface: cmux bin={cmux_bin()!r} rc={rc} "
                         f"err={(err or '')[:300]!r} out={(out or '')[:120]!r}\n")
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


# ────────────────────  ChatGPT thumbnail generation  ────────────────────
# Drives cmux → chatgpt.com (already logged in) to generate a thumbnail image.
# Verified cmux constraints: `press`/`key` and `input_keyboard` throw on
# WKWebView, so we SEND by clicking the "Send prompt" button found via snapshot
# ref (CSS aria-label lookups also fail). The generated <img> src is a
# chatgpt.com backend-api URL that 403s without the session cookies, so we fetch
# it with the browser's cookies (cmux `cookies get`). `fill` cleanly REPLACES
# the composer (plain `type` appends), so we navigate to a fresh chat + fill.

_CHATGPT_SURFACE = None  # cached "surface:N" for the ChatGPT pane (separate from LinkedIn's)
_CHATGPT_IMG_SELECTORS = ('img[alt="Generated image"]', 'main img')


def resolve_chatgpt_surface() -> str | None:
    """Open (or reuse) a ChatGPT browser surface, separate from LinkedIn's."""
    global _CHATGPT_SURFACE
    if _CHATGPT_SURFACE:
        return _CHATGPT_SURFACE
    import json
    rc, out, _ = _run(["--json", "browser", "open", "https://chatgpt.com/"], timeout=40)
    if rc != 0:
        return None
    try:
        _CHATGPT_SURFACE = json.loads(out).get("surface_ref")
    except Exception:
        _CHATGPT_SURFACE = None
    return _CHATGPT_SURFACE


def _close_chatgpt_surface(surface: str | None = None) -> None:
    """Close the ChatGPT browser surface (we open it only to generate a thumbnail;
    leave the user's cmux tidy when done)."""
    global _CHATGPT_SURFACE
    s = surface or _CHATGPT_SURFACE
    if s:
        _run(["close-surface", "--surface", s], timeout=15)
    _CHATGPT_SURFACE = None


def _chatgpt_image_src(surface: str) -> str:
    for sel in _CHATGPT_IMG_SELECTORS:
        rc, out, _ = _b(surface, "get", "attr", "--selector", sel, "--attr", "src")
        src = (out or "").strip()
        if rc == 0 and src.startswith("http"):
            return src
    return ""


def _fetch_with_cookies(surface: str, src: str, out_path) -> bool:
    """Download a chatgpt.com asset using the browser's session cookies.
    The generated-image URL 403s without them. Returns True if a real image
    (PNG/JPEG) was written to out_path."""
    import json, urllib.request
    from pathlib import Path as _P
    b = cmux_bin()
    if not b:
        return False
    try:
        p = subprocess.run([b, "--json", "browser", surface, "cookies", "get",
                            "--url", "https://chatgpt.com"],
                           capture_output=True, text=True, timeout=30)
        jar = json.loads(p.stdout).get("cookies", [])
    except Exception:
        jar = []
    header = "; ".join(f'{c["name"]}={c["value"]}'
                       for c in jar if "chatgpt.com" in c.get("domain", ""))
    req = urllib.request.Request(src, headers={
        "Cookie": header, "User-Agent": "Mozilla/5.0", "Referer": "https://chatgpt.com/"})
    try:
        data = urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        return False
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_jpg = data[:3] == b"\xff\xd8\xff"
    if not data or not (is_png or is_jpg):   # likely an error JSON, not an image
        return False
    _P(out_path).write_bytes(data)
    return True


def chatgpt_generate_image(prompt: str, out_path, timeout: int = 240) -> dict:
    """Drive cmux → ChatGPT to generate an image and save it to out_path.
    Returns {ok, path?, reason?, hint?}. The send-button click needs the ChatGPT
    pane to be the on-screen cmux pane (headless reads/polling are fine)."""
    s = resolve_chatgpt_surface()
    if not s:
        return {"ok": False, "reason": "no_surface", "hint": "cmux browser unavailable"}
    try:
        # fresh chat → empty composer (plain `type` would append to leftover text)
        _b(s, "navigate", "https://chatgpt.com/", timeout=40)
        _b(s, "wait", "--load-state", "complete", "--timeout-ms", "20000", timeout=25)
        # ChatGPT is a heavy SPA — poll for the composer to mount (don't single-shot)
        composer_ok = False
        for _ in range(15):
            rc, out, _ = _b(s, "get", "attr", "--selector", "#prompt-textarea", "--attr", "id")
            if rc == 0 and (out or "").strip() == "prompt-textarea":
                composer_ok = True
                break
            time.sleep(2)
        if not composer_ok:
            return {"ok": False, "reason": "no_composer",
                    "hint": "ChatGPT composer not found — is chatgpt.com logged in?"}
        one_line = re.sub(r"\s+", " ", prompt).strip()   # newlines would risk an early send
        _b(s, "fill", "--selector", "#prompt-textarea", "--text", one_line)
        # SEND: click the "Send prompt" button by snapshot ref (press/css unsupported)
        rc, snap, _ = _b(s, "snapshot", "--max-depth", "30", timeout=20)
        m = (re.search(r'button "Send prompt".*?\[ref=(e\d+)\]', snap or "")
             or re.search(r'button "Send[^"]*".*?\[ref=(e\d+)\]', snap or ""))
        if not m:
            return {"ok": False, "reason": "no_send", "hint": "could not find ChatGPT send button"}
        _b(s, "click", m.group(1))
        # poll for the generated image (image-gen typically takes 30-90s)
        deadline = time.time() + timeout
        src = ""
        while time.time() < deadline:
            time.sleep(5)
            src = _chatgpt_image_src(s)
            if src:
                break
        if not src:
            return {"ok": False, "reason": "timeout",
                    "hint": "image never rendered (rate-limited / login expired?)"}
        if not _fetch_with_cookies(s, src, out_path):
            return {"ok": False, "reason": "save_failed",
                    "hint": "could not download the generated image"}
        return {"ok": True, "path": str(out_path)}
    finally:
        _close_chatgpt_surface(s)   # always tidy up the GPT browser when done


# ────────────────────  composer  ────────────────────

def _osascript(script: str, timeout: int = 20) -> int:
    try:
        return subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=timeout).returncode
    except Exception:
        return 1


def attach_image_to_composer(png_path: str) -> dict:
    """Best-effort auto-attach: put the PNG on the macOS clipboard and paste it
    into the focused LinkedIn composer. cmux CANNOT inject a Cmd+V into WKWebView
    (press/input_keyboard unsupported), so the paste is an OS-level keystroke via
    System Events, which needs cmux frontmost. We always leave the PNG on the
    clipboard AND on disk, so even if the auto-paste misses, one ⌘V (or a drag)
    finishes it. Returns {clipboard, pasted}."""
    if not os.path.exists(png_path):
        return {"clipboard": False, "pasted": False}
    import json as _json
    set_rc = _osascript(
        f'set the clipboard to (read (POSIX file {_json.dumps(png_path)}) as «class PNGf»)')
    s = resolve_surface()
    if s:
        _b(s, "click", "--selector", ".ql-editor")   # focus the editor (pane on-screen)
    paste_rc = _osascript(
        'tell application "cmux" to activate\n'
        'delay 0.5\n'
        'tell application "System Events" to keystroke "v" using command down')
    return {"clipboard": set_rc == 0, "pasted": set_rc == 0 and paste_rc == 0}


def prefill_composer(text: str, image_path: str | None = None) -> dict:
    """Open LinkedIn composer and fill `text`. Does NOT click Post.
    Requires the LinkedIn cmux pane to be on-screen (headless clicks don't open
    the modal). If `image_path` is given, best-effort attaches it (clipboard
    paste). Returns {ok:bool, reason?:str, hint?:str, thumbnail_*?}."""
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
    result = {"ok": True}
    if image_path:
        att = attach_image_to_composer(image_path)
        result["thumbnail_attached"] = att.get("pasted", False)
        if not att.get("pasted"):
            result["thumbnail_hint"] = (
                "thumbnail is on your clipboard + saved to disk — click into the "
                "composer and press ⌘V, or drag the file in.")
    return result
