# LinkedIn Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a posts-focused LinkedIn workspace to the dashboard — mine the user's LinkedIn posts + X signal into value-post ideas, draft full posts in a dedicated voice, edit/approve, and pre-fill the LinkedIn composer via the cmux browser for the user to click Post.

**Architecture:** Mirror the existing **blog feature** (`blog.py` + `/blog/*` routes + `blog_mod` import in `server.py` + dedicated UI screens + own data file + own agent). Two new Python modules (`linkedin_cli.py` for cmux browser I/O, `linkedin.py` for pipeline+state), a dedicated `linkedin-voice` claude agent, new server routes, and two new UI screens.

**Tech Stack:** Python 3.10 stdlib only; `cmux browser` CLI for LinkedIn I/O; `claude -p --agent` for drafting; vanilla JS single-page UI.

**Reference design:** `docs/superpowers/specs/2026-06-09-linkedin-automation-design.md` (read it first).

**Critical cmux facts (verified):** reading is headless; interactive clicks need the LinkedIn pane on-screen; `eval`/`find role` throw `js_error` (use snapshot refs + CSS selectors only). cmux bin: `/Applications/cmux.app/Contents/Resources/bin/cmux`.

---

## File Structure

- **Create** `linkedin_cli.py` — cmux browser wrapper: surface resolution, headless reads (login check, my_posts, profile), foreground `prefill_composer`.
- **Create** `linkedin.py` — pipeline + state: mine, read_x_signal, generate_ideas, draft, merge-preserving read/write, agent helpers.
- **Create** `~/.claude/agents/linkedin-voice.md` — dedicated voice agent, seeded from scraped posts.
- **Create** `tests/test_linkedin.py` — unit tests for pure functions (merge-preserving write, json extraction, theme mining).
- **Modify** `server.py` — add `import linkedin as linkedin_mod` + `/linkedin/*` and `/linkedin-agent*` routes (GET/POST/DELETE), following the blog route blocks.
- **Modify** `static/index.html` — add nav items `08 linkedin ideas`, `09 linkedin drafts`; add a LinkedIn tab in the agent screen; add screen containers.
- **Modify** `static/app.js` — add screen renderers + fetch calls for the new endpoints.
- **Modify** `static/style.css` — minimal styles reusing existing card/badge classes.
- **Modify** `README.md` + `SKILL.md` — document the LinkedIn workspace.

---

## Task 1: `linkedin_cli.py` — cmux browser wrapper

**Files:**
- Create: `linkedin_cli.py`

- [ ] **Step 1: Implement cmux resolution + a run helper**

```python
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
```

- [ ] **Step 2: Surface resolution + navigation helpers**

```python
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

def get_text(surface: str, css: str) -> str:
    rc, out, _ = _b(surface, "get", "text", "--selector", css)
    return out.strip() if rc == 0 else ""
```

- [ ] **Step 3: Read functions — login check, profile, my_posts (headless)**

```python
def is_logged_in() -> bool:
    s = resolve_surface()
    if not s:
        return False
    navigate(s, "https://www.linkedin.com/feed/")
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

POST_SELECTORS = [
    ".feed-shared-update-v2__description",
    ".update-components-update-v2__commentary",
    ".feed-shared-inline-show-more-text",
]

def my_posts(limit: int = 20) -> list[str]:
    """Scrape the user's own recent posts. Headless. get text returns the FIRST
    visible match, so we scroll + collect across selectors and dedupe."""
    s = resolve_surface()
    if not s:
        return []
    h = (profile().get("handle") or HANDLE or "me")
    navigate(s, f"https://www.linkedin.com/in/{h}/recent-activity/all/")
    seen, posts = set(), []
    for _ in range(max(1, limit // 2)):
        for css in POST_SELECTORS:
            txt = get_text(s, css)
            key = re.sub(r"\s+", " ", txt).strip().lower()[:120]
            if txt and key not in seen:
                seen.add(key); posts.append(txt.replace("…more", "").strip())
        if len(posts) >= limit:
            break
        _b(s, "scroll", "--dy", "1600")
        _b(s, "wait", "--load-state", "complete", "--timeout-ms", "4000")
    return posts[:limit]
```

- [ ] **Step 4: `prefill_composer` (foreground path — never submits)**

```python
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
```

- [ ] **Step 5: Live smoke check**

Run: `LINKEDIN_HANDLE=bobde-yagyesh python3 -c "import linkedin_cli as l; print('login', l.is_logged_in()); print('profile', l.profile()); p=l.my_posts(6); print('posts', len(p)); print(p[0][:120] if p else 'NONE')"`
Expected: `login True`, a profile dict with handle, `posts >= 1`, a real post body printed.

- [ ] **Step 6: Commit**

```bash
git add linkedin_cli.py
git commit -m "feat(linkedin): cmux browser I/O wrapper (read headless, prefill composer)"
```

---

## Task 2: `linkedin.py` — pipeline + state (pure-function tests first)

**Files:**
- Create: `linkedin.py`
- Create: `tests/test_linkedin.py`

- [ ] **Step 1: Write failing tests for pure helpers**

```python
# tests/test_linkedin.py
import importlib, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import linkedin as L

def test_extract_json_strips_fences():
    assert L.extract_json('```json\n{"a":1}\n```') == {"a": 1}

def test_merge_preserves_approved_and_posted():
    old = {"drafts": [
        {"id": "d1", "text": "x", "status": "approved"},
        {"id": "d2", "text": "y", "status": "posted"},
        {"id": "d3", "text": "z", "status": "draft"},
    ], "ideas": [], "themes": []}
    new = {"drafts": [{"id": "d3", "text": "ZZ", "status": "draft"}],
           "ideas": [], "themes": ["t"]}
    merged = L.merge_data(old, new)
    by = {d["id"]: d for d in merged["drafts"]}
    assert by["d1"]["status"] == "approved"   # preserved
    assert by["d2"]["status"] == "posted"     # preserved
    assert by["d3"]["text"] == "ZZ"           # draft replaced
    assert merged["themes"] == ["t"]

def test_mine_themes_counts_tokens():
    themes = L.mine_themes(["shipping ai agents now", "ai agents are great", "react native rocks"])
    assert "agents" in themes and "ai" in themes
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3 -m pytest tests/test_linkedin.py -v` (or `python3 tests/test_linkedin.py` if pytest absent)
Expected: FAIL / ImportError (linkedin module or functions not defined).

- [ ] **Step 3: Implement `linkedin.py` core (state + pure helpers)**

```python
"""linkedin.py — LinkedIn posts pipeline + state. Mirrors blog.py."""
import json, os, re, shutil, subprocess, sys, time
from collections import Counter
from pathlib import Path
import linkedin_cli

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "linkedin_data.json"
X_DATA = ROOT / "data" / "dashboard_data.json"
AGENT_NAME = (os.environ.get("LINKEDIN_AGENT") or "linkedin-voice").strip()
AGENT_MD = Path(os.environ.get("LINKEDIN_AGENT_MD") or (Path.home()/".claude"/"agents"/f"{AGENT_NAME}.md"))

_STOP = set("the a an and or to of in is it for on with my our your this that i we you they are be as at".split())

def extract_json(blob: str) -> dict | None:
    blob = re.sub(r"^```(?:json)?\s*", "", blob.strip())
    blob = re.sub(r"\s*```$", "", blob)
    a, b = blob.find("{"), blob.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        return json.loads(blob[a:b+1])
    except json.JSONDecodeError:
        return None

def mine_themes(posts: list[str], top: int = 12) -> list[str]:
    c = Counter()
    for p in posts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+]{2,}", (p or "").lower()):
            if w not in _STOP:
                c[w] += 1
    return [w for w, _ in c.most_common(top)]

def _index(drafts): return {d.get("id"): d for d in drafts if d.get("id")}

def merge_data(old: dict, new: dict) -> dict:
    """Preserve approved/posted drafts; replace draft-status items with new."""
    old_by = _index(old.get("drafts", []))
    kept = [d for d in old.get("drafts", []) if d.get("status") in ("approved", "posted")]
    kept_ids = {d["id"] for d in kept}
    for d in new.get("drafts", []):
        if d.get("id") not in kept_ids:
            kept.append(d)
    out = dict(new)
    out["drafts"] = kept
    return out

def read_data() -> dict:
    if DATA.exists():
        try:
            return json.loads(DATA.read_text())
        except Exception:
            pass
    return {"generated_at": None, "profile": {}, "style_corpus": [], "themes": [], "ideas": [], "drafts": []}

def write_data(d: dict):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(d, indent=2))
```

- [ ] **Step 4: Run tests, verify pure-function tests pass**

Run: `python3 -m pytest tests/test_linkedin.py -v`
Expected: PASS (all 3).

- [ ] **Step 5: Add claude drafting + pipeline orchestration**

```python
def _claude_json(prompt: str, timeout: int = 240) -> dict | None:
    cb = shutil.which("claude")
    if not cb:
        sys.stderr.write("[linkedin] claude not found\n"); return None
    cmd = [cb, "-p", prompt, "--agent", AGENT_NAME, "--effort", "medium"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return extract_json(p.stdout) if p.returncode == 0 else None

def read_x_signal() -> dict:
    if not X_DATA.exists():
        return {"top_keywords": [], "top_accounts": [], "recent": []}
    try:
        d = json.loads(X_DATA.read_text())
        sig = d.get("signature") or d.get("public_sig") or {}
        return {"top_keywords": sig.get("top_keywords", [])[:15],
                "top_accounts": sig.get("top_accounts", [])[:10],
                "recent": [t.get("text","") for t in (d.get("mine") or [])[:8]]}
    except Exception:
        return {"top_keywords": [], "top_accounts": [], "recent": []}

IDEAS_SHAPE = '''Return JSON ONLY (start with { end with }):
{"ideas":[{"id":"i1","angle":"<specific post angle>","source":"linkedin|x-signal","why_valuable":"<one line: who benefits and why>"}]}'''

DRAFT_SHAPE = '''Return JSON ONLY (start with { end with }):
{"text":"<the full LinkedIn post, ready to publish>","why_valuable":"<one line>"}'''

def _ideas_prompt(themes, corpus, xsig, n=8) -> str:
    return (
      "You are running as my LinkedIn voice agent (persona loaded).\n"
      "Propose LinkedIn post ideas that are genuinely VALUABLE — teach, share a real lesson, "
      "or give an honest take. No filler, no engagement-bait.\n\n"
      f"## My recurring LinkedIn themes\n{', '.join(themes) or '(none)'}\n\n"
      f"## My recent LinkedIn posts (continue the narrative, don't repeat)\n" +
      "\n".join(f"- {c[:200]}" for c in corpus[:6]) + "\n\n"
      f"## My X/Twitter signal (adapt the best into long-form LinkedIn value posts)\n"
      f"keywords: {', '.join(xsig['top_keywords'])}\nrecent tweets: " +
      " | ".join(xsig['recent'][:5]) + "\n\n"
      f"## Task\nGive exactly {n} distinct ideas. Tag source linkedin or x-signal.\n\n" + IDEAS_SHAPE)

def _draft_prompt(idea: dict, corpus) -> str:
    return (
      "You are running as my LinkedIn voice agent (persona loaded).\n"
      "Write ONE full LinkedIn post for the idea below. Match my style: short declarative "
      "lines, arrow bullets where useful, em-dashes, structured, zero fluff, a concrete takeaway. "
      "Must deliver real value, not be generic.\n\n"
      f"## Idea\nangle: {idea.get('angle')}\nwhy valuable: {idea.get('why_valuable')}\n\n"
      f"## My voice samples\n" + "\n".join(f"- {c[:200]}" for c in corpus[:4]) + "\n\n"
      "## Task\nWrite the post.\n\n" + DRAFT_SHAPE)

def refresh() -> dict:
    posts = linkedin_cli.my_posts(20)
    prof = linkedin_cli.profile()
    themes = mine_themes(posts)
    xsig = read_x_signal()
    res = _claude_json(_ideas_prompt(themes, posts, xsig)) or {"ideas": []}
    ideas = res.get("ideas", [])
    for k, it in enumerate(ideas):
        it.setdefault("id", f"i{int(time.time())}{k}")
        it["status"] = "idea"
    new = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "profile": prof,
           "style_corpus": posts, "themes": themes, "ideas": ideas, "drafts": []}
    merged = merge_data(read_data(), new)
    write_data(merged)
    return merged

def draft(idea_id: str) -> dict:
    data = read_data()
    idea = next((i for i in data.get("ideas", []) if i.get("id") == idea_id), None)
    if not idea:
        return {"error": "idea not found"}
    res = _claude_json(_draft_prompt(idea, data.get("style_corpus", [])))
    if not res or not res.get("text"):
        return {"error": "draft generation failed"}
    d = {"id": f"d{int(time.time())}", "idea_id": idea_id, "text": res["text"],
         "why_valuable": res.get("why_valuable", idea.get("why_valuable", "")),
         "status": "draft", "edited": False}
    data["drafts"].append(d); idea["status"] = "drafted"; write_data(data)
    return d
```

- [ ] **Step 6: Add state-mutation + agent helpers**

```python
def save_draft_text(draft_id: str, text: str) -> dict:
    data = read_data()
    for d in data.get("drafts", []):
        if d.get("id") == draft_id:
            d["text"] = text; d["edited"] = True; d["status"] = "approved"
            write_data(data); return d
    return {"error": "draft not found"}

def mark_posted(draft_id: str) -> dict:
    data = read_data()
    for d in data.get("drafts", []):
        if d.get("id") == draft_id:
            d["status"] = "posted"; write_data(data); return d
    return {"error": "draft not found"}

def discard_draft(draft_id: str) -> dict:
    data = read_data()
    data["drafts"] = [d for d in data.get("drafts", []) if d.get("id") != draft_id]
    write_data(data); return {"ok": True}

def discard_idea(idea_id: str) -> dict:
    data = read_data()
    data["ideas"] = [i for i in data.get("ideas", []) if i.get("id") != idea_id]
    write_data(data); return {"ok": True}

def compose(draft_id: str) -> dict:
    data = read_data()
    d = next((x for x in data.get("drafts", []) if x.get("id") == draft_id), None)
    if not d:
        return {"ok": False, "reason": "not_found"}
    return linkedin_cli.prefill_composer(d["text"])

def read_agent() -> dict:
    if not AGENT_MD.exists():
        return {"error": f"agent file not found: {AGENT_MD}", "content": ""}
    return {"path": str(AGENT_MD), "content": AGENT_MD.read_text()}

def write_agent(content: str) -> dict:
    AGENT_MD.parent.mkdir(parents=True, exist_ok=True)
    AGENT_MD.write_text(content); return {"ok": True}

def remine_voice() -> dict:
    """Rebuild the GOLD EXAMPLES block in the agent file from scraped posts."""
    posts = linkedin_cli.my_posts(15)
    if not posts:
        return {"ok": False, "reason": "no posts scraped"}
    block = "## GOLD EXAMPLES — real LinkedIn posts I wrote. Absorb the voice; do NOT copy.\n\n" + \
            "\n\n".join(f"---\n{p}" for p in posts[:10])
    content = AGENT_MD.read_text() if AGENT_MD.exists() else "# linkedin-voice\n\n"
    if "## GOLD EXAMPLES" in content:
        content = re.sub(r"## GOLD EXAMPLES.*?(?=\n## |\Z)", block + "\n", content, flags=re.S)
    else:
        content += "\n\n" + block + "\n"
    AGENT_MD.write_text(content)
    return {"ok": True, "count": len(posts[:10])}

if __name__ == "__main__":
    print(json.dumps(refresh(), indent=2)[:2000])
```

- [ ] **Step 7: Run full test file**

Run: `python3 -m pytest tests/test_linkedin.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add linkedin.py tests/test_linkedin.py
git commit -m "feat(linkedin): pipeline + state (mine, ideas, draft, merge-preserving)"
```

---

## Task 3: `linkedin-voice` agent file (mine posts, then author)

**Files:**
- Create: `~/.claude/agents/linkedin-voice.md`

- [ ] **Step 1: Scrape current posts to ground the voice**

Run: `LINKEDIN_HANDLE=bobde-yagyesh python3 -c "import linkedin_cli as l, json; print(json.dumps(l.my_posts(15)))"`
Capture the real posts (arrow bullets `→`, em-dashes, short declarative lines, structured sections, direct CTAs).

- [ ] **Step 2: Write the agent file**

Author `~/.claude/agents/linkedin-voice.md` with YAML frontmatter (`name: linkedin-voice`, `description`, `tools: Read, Write, Edit, Bash, WebFetch`) and sections:
- **Persona** — who Yagyesh is on LinkedIn (indie/AI builder, ships RN + AI agents).
- **Voice rules** — long-form but tight; short declarative lines; arrow bullets `→`; em-dashes; structured (problem → what I did → takeaway); concrete numbers; no hashtags, no engagement-bait, no "Agree?" closers; deliver a real lesson every time.
- **GOLD EXAMPLES** — the scraped posts from Step 1, verbatim.
- **JSON-only output reminder** for pipeline calls.

- [ ] **Step 3: Verify the agent loads**

Run: `claude --agent linkedin-voice -p "reply with the single word: ready" --effort medium`
Expected: output contains `ready`.

- [ ] **Step 4: Commit** (agent lives in `~/.claude/agents`, outside repo — note it in README instead; nothing to commit here. Skip.)

---

## Task 4: Wire server routes (mirror blog routes)

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Read the blog route blocks for the exact pattern**

Read `server.py` lines around the `/blog/data`, `/blog-agent`, `/blog/projects/`, `/blog/ideas/` handlers in `do_GET`, `do_POST`, `do_DELETE`, and how `blog_mod` is imported. Replicate the structure.

- [ ] **Step 2: Add import + GET routes**

Add near other imports: `import linkedin as linkedin_mod`. In `do_GET`, mirroring blog:
```python
if path == "/linkedin/data":
    return self._send_json(200, linkedin_mod.read_data())
if path == "/linkedin-agent":
    return self._send_json(200, linkedin_mod.read_agent())
```

- [ ] **Step 3: Add POST routes**

In `do_POST`, after reading JSON body (use existing `self._read_json_body()`):
```python
if path == "/linkedin/refresh":
    return self._send_json(200, linkedin_mod.refresh())
if path == "/linkedin/draft":
    b = self._read_json_body() or {}
    return self._send_json(200, linkedin_mod.draft(b.get("idea_id","")))
if path == "/linkedin/draft/save":
    b = self._read_json_body() or {}
    return self._send_json(200, linkedin_mod.save_draft_text(b.get("id",""), b.get("text","")))
if path == "/linkedin/compose":
    b = self._read_json_body() or {}
    return self._send_json(200, linkedin_mod.compose(b.get("id","")))
if path == "/linkedin/mark-posted":
    b = self._read_json_body() or {}
    return self._send_json(200, linkedin_mod.mark_posted(b.get("id","")))
if path == "/linkedin-agent":
    b = self._read_json_body() or {}
    return self._send_json(200, linkedin_mod.write_agent(b.get("content","")))
if path == "/linkedin-agent/remine":
    return self._send_json(200, linkedin_mod.remine_voice())
```

- [ ] **Step 4: Add DELETE routes**

In `do_DELETE`, mirroring `/blog/ideas/`:
```python
if path.startswith("/linkedin/drafts/"):
    return self._send_json(200, linkedin_mod.discard_draft(path.rsplit("/",1)[-1]))
if path.startswith("/linkedin/ideas/"):
    return self._send_json(200, linkedin_mod.discard_idea(path.rsplit("/",1)[-1]))
```

- [ ] **Step 5: Smoke test the server boots + routes respond**

Run: `python3 -c "import ast; ast.parse(open('server.py').read()); print('server.py parses')"`
Then start server briefly and curl `/linkedin/data`:
Run: `DASHBOARD_PORT=7899 ./run.sh & sleep 4; curl -s localhost:7899/linkedin/data | head -c 200; curl -s localhost:7899/linkedin-agent | head -c 120; kill %1`
Expected: JSON from `/linkedin/data` (empty-state shape) and agent content/err.

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "feat(linkedin): server routes (/linkedin/* + /linkedin-agent*)"
```

---

## Task 5: UI screens (mirror blog screens)

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`

- [ ] **Step 1: Read how blog screens are built**

In `static/app.js` find the blog screen renderers and nav wiring; in `index.html` find the blog nav `<li>` and screen `<section>`. Replicate naming conventions.

- [ ] **Step 2: Add nav + screen containers in index.html**

Add nav entries `08 linkedin ideas` and `09 linkedin drafts` next to the blog entries, and two `<section>` containers with matching ids (e.g. `#screen-linkedin-ideas`, `#screen-linkedin-drafts`). Add a LinkedIn tab toggle inside the existing agent screen.

- [ ] **Step 3: Add renderers + fetches in app.js**

- `renderLinkedinIdeas()` — `GET /linkedin/data`; render theme chips + idea cards (angle, why_valuable, source badge). "Write full post" → `POST /linkedin/draft {idea_id}` then re-render. Sidebar "↻ refresh linkedin" → `POST /linkedin/refresh`.
- `renderLinkedinDrafts()` — draft cards: editable textarea (char count), why_valuable line. "Open in composer" → `POST /linkedin/compose {id}`; on `{ok:false,reason:"pane_hidden"}` show the inline hint. "Save" → `POST /linkedin/draft/save`. "Mark as posted" → `POST /linkedin/mark-posted`. "Discard" → `DELETE /linkedin/drafts/<id>`.
- LinkedIn agent tab — `GET/POST /linkedin-agent`, "re-mine" → `POST /linkedin-agent/remine`.

- [ ] **Step 4: Minimal styles**

Reuse existing card/badge/button classes. Add only a `.source-badge` variant if needed.

- [ ] **Step 5: Manual UI verification**

Run the server, open `http://127.0.0.1:7873/`, confirm the two new screens render, "refresh linkedin" populates ideas, "write full post" creates a draft, "open in composer" returns the pane-hidden hint (or pre-fills when the LinkedIn pane is foregrounded).

- [ ] **Step 6: Commit**

```bash
git add static/
git commit -m "feat(linkedin): dashboard UI (ideas + drafts screens, agent tab)"
```

---

## Task 6: Docs

**Files:**
- Modify: `README.md`, `SKILL.md`

- [ ] **Step 1: Document the LinkedIn workspace**

Add a "LinkedIn workspace" section to README: env vars (`LINKEDIN_HANDLE`, `LINKEDIN_AGENT`, `LINKEDIN_AGENT_MD`), the cmux dependency, the pre-fill/you-click-Post flow + the "keep the LinkedIn pane on-screen to publish" note, and the new screens. Add the new endpoints + files to SKILL.md.

- [ ] **Step 2: Commit**

```bash
git add README.md SKILL.md
git commit -m "docs(linkedin): document LinkedIn workspace + cmux dependency"
```

---

## Self-Review notes

- **Spec coverage:** cmux wrapper (T1) ✓, pipeline/ideas/draft/merge (T2) ✓, voice agent (T3) ✓, server routes (T4) ✓, UI screens + agent tab (T5) ✓, pre-fill/you-click + pane_hidden (T1/T4/T5) ✓, docs (T6) ✓.
- **Naming consistency:** `read_data/write_data/merge_data`, `refresh/draft/save_draft_text/mark_posted/discard_draft/discard_idea/compose`, `read_agent/write_agent/remine_voice`, `prefill_composer`, `my_posts/profile/is_logged_in` — used consistently across server routes and UI fetches.
- **TDD adaptation:** pure functions (extract_json, merge_data, mine_themes) are unit-tested; browser I/O and claude calls are live-smoke-verified (can't be meaningfully unit-tested).
