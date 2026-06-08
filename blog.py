"""
Blog Ideas backend for the yagyesh-dashboard.

Self-contained helpers used by server.py:
  - storage: blog_state.json (ideas + projects + drafts)
  - sources: Medium RSS scrape, git project signal
  - generation: idea brainstorm, 10 title variations, blog draft, comment-driven revision
  - agent: read/write ~/.claude/agents/blog-writer.md (+ append style notes)

Everything goes through the `claude` CLI with --agent blog-writer for voice consistency.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT       = Path(__file__).resolve().parent
DATA_DIR   = ROOT / "data"
BLOG_STATE = DATA_DIR / "blog_state.json"
BLOG_AGENT_NAME = os.environ.get("BLOG_AGENT", "blog-writer").strip() or "blog-writer"
BLOG_AGENT_MD   = Path(
    os.environ.get("BLOG_AGENT_MD")
    or (Path.home() / ".claude" / "agents" / f"{BLOG_AGENT_NAME}.md")
)
MEDIUM_FEED_URL = os.environ.get("BLOG_MEDIUM_FEED", "https://medium.com/feed/@bobde-yagyesh")

DATA_DIR.mkdir(exist_ok=True)

_state_lock = threading.Lock()
_agent_lock = threading.Lock()


# ────────────────────  storage  ────────────────────

def _default_state() -> dict:
    return {
        "ideas":      [],   # [{id, title, summary, source, source_url, finalized, created_at, project_path?}]
        "projects":   [],   # [{id, path, name, added_at}]
        "drafts":     [],   # [{id, idea_id, title, content, comments[], variations[], versions[], created_at, updated_at}]
        "medium_seen": [],  # [{title, url, date, summary}] — last scrape cache (also feeds ideas)
        "updated_at": None,
    }


def load_state() -> dict:
    with _state_lock:
        try:
            data = json.loads(BLOG_STATE.read_text())
            if not isinstance(data, dict):
                return _default_state()
            base = _default_state()
            base.update(data)
            return base
        except Exception:
            return _default_state()


def clean_draft_preambles(state: dict) -> int:
    """One-shot backfill: strip agent preamble from any existing drafts."""
    changed = 0
    for d in state.get("drafts") or []:
        c = d.get("content") or ""
        cleaned = _strip_draft_preamble(c)
        if cleaned != c:
            d["content"] = cleaned
            changed += 1
    if changed:
        save_state(state)
    return changed


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _state_lock:
        tmp = BLOG_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.replace(BLOG_STATE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_id() -> str:
    return uuid.uuid4().hex[:10]


# ────────────────────  medium scrape  ────────────────────

_RSS_ITEM = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_RSS_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_RSS_LINK = re.compile(r"<link>(.*?)</link>", re.S | re.I)
_RSS_DATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S | re.I)
_RSS_DESC = re.compile(r"<description>(.*?)</description>", re.S | re.I)
_RSS_CONTENT = re.compile(r"<content:encoded>(.*?)</content:encoded>", re.S | re.I)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_HTML_TAGS = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    if not s:
        return ""
    m = _CDATA.search(s)
    if m:
        s = m.group(1)
    s = _HTML_TAGS.sub("", s)
    return html.unescape(s).strip()


def fetch_medium_posts(limit: int = 15) -> list[dict]:
    """Pull the latest Medium posts via RSS. stdlib only, no deps."""
    req = urllib.request.Request(
        MEDIUM_FEED_URL,
        headers={"User-Agent": "yagyesh-dashboard/0.1 (+local)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml = resp.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError(f"medium fetch failed: {e}")

    out = []
    for raw in _RSS_ITEM.findall(xml)[:limit]:
        title = _strip((_RSS_TITLE.search(raw) or [None, ""]).group(1) if _RSS_TITLE.search(raw) else "")
        link  = _strip((_RSS_LINK.search(raw)  or [None, ""]).group(1) if _RSS_LINK.search(raw)  else "")
        date  = _strip((_RSS_DATE.search(raw)  or [None, ""]).group(1) if _RSS_DATE.search(raw)  else "")
        # summary preference: description > first 500 chars of content
        desc_m = _RSS_DESC.search(raw)
        content_m = _RSS_CONTENT.search(raw)
        summary = _strip(desc_m.group(1)) if desc_m else (_strip(content_m.group(1))[:500] if content_m else "")
        summary = re.sub(r"\s+", " ", summary)[:280]
        if not title or not link:
            continue
        out.append({
            "title":   title,
            "url":     link,
            "date":    date,
            "summary": summary,
        })
    return out


# ────────────────────  project signal (git)  ────────────────────

def _git(args: list[str], cwd: Path, timeout: int = 8) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def project_signal(path: str, max_commits: int = 12) -> dict:
    """Lightweight git signal for a project — used as blog-topic seed material."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        return {"path": str(p), "error": "path not found"}

    out = {"path": str(p), "name": p.name}

    # is it a git repo?
    is_git = bool(_git(["rev-parse", "--show-toplevel"], p, timeout=3).strip())
    if not is_git:
        out["error"] = "not a git repo"
        return out

    log = _git(["log", "--pretty=%h|%ad|%s", "--date=short", f"-n{max_commits}"], p)
    out["recent_commits"] = [
        {"hash": h, "date": d, "subject": s}
        for line in log.strip().splitlines()
        if "|" in line and len(line.split("|", 2)) == 3
        for h, d, s in [line.split("|", 2)]
    ]

    changed = _git(["diff", "--name-only", "HEAD~10..HEAD"], p) or _git(["diff", "--name-only", "HEAD~5..HEAD"], p)
    out["recent_files"] = [ln for ln in changed.splitlines() if ln.strip()][:30]

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], p).strip()
    if branch:
        out["branch"] = branch

    # surface README h1 if present
    for fn in ("README.md", "Readme.md", "readme.md"):
        rd = p / fn
        if rd.exists():
            try:
                first = rd.read_text("utf-8", "ignore").splitlines()
                for line in first[:30]:
                    if line.startswith("# "):
                        out["readme_h1"] = line[2:].strip()[:140]
                        break
            except Exception:
                pass
            break

    return out


# ────────────────────  claude CLI bridge  ────────────────────

def call_claude_agent(prompt: str, agent: str = BLOG_AGENT_NAME, timeout: int = 300) -> tuple[bool, str]:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False, "claude CLI not found on PATH"
    try:
        proc = subprocess.run(
            [claude_bin, "--agent", agent, "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "claude call timed out"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:800]
    return True, proc.stdout


def _strip_draft_preamble(text: str) -> str:
    """Trim any conversational scratch the agent left before the first H1.

    The blog-writer agent is told to start with `# ` but occasionally narrates
    ('Now I have plenty of real material — writing the revision.') before the
    title. The dashboard's draft box must contain ONLY the publishable body.
    """
    if not text:
        return text
    s = text.strip()

    # 1. peel surrounding ```markdown / ``` code fences if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
        s = s.strip()

    # 2. drop anything before the first H1 (line beginning with '# ')
    m = re.search(r"^#\s+\S", s, re.M)
    if m:
        s = s[m.start():]

    # 3. drop trailing fence if any survived
    s = re.sub(r"\n```\s*$", "", s).strip()
    return s


def extract_json(blob: str) -> dict | list | None:
    blob = (blob or "").strip()
    blob = re.sub(r"^```(?:json)?\s*", "", blob)
    blob = re.sub(r"\s*```$", "", blob)
    # try whole-string first
    try:
        return json.loads(blob)
    except Exception:
        pass
    # locate the largest {…} or […] span
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = blob.find(opener), blob.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(blob[i:j+1])
            except Exception:
                continue
    return None


# ────────────────────  prompts  ────────────────────

IDEA_PROMPT = """You are brainstorming NEW blog ideas for Yagyesh Bobde (bobde-yagyesh.medium.com).

Audience: STAFF/SENIOR engineers, founding-engineer indies, AI-tooling power users running multi-agent setups. They've already shipped to production. They are reading on Medium to learn a specific tactic from someone deeper in the trenches than them.

────────────────────────────────────────────────────────
YAGYESH'S ACTUAL STACK (use these as ground-truth seed material — most ideas should be anchored here)
────────────────────────────────────────────────────────

TERMINAL / WORKSPACE
- **cmux** is his primary terminal now (NOT Warp anymore — he migrated away). cmux supports sessions, multiple panes, persistent layouts. Each project gets its own session; each agent run gets its own pane.
- Was on Warp before — switched. The reasons for switching, the workflows that didn't translate, the cmux-only patterns he relies on now — all blog-worthy.

PRIMARY DEV LOOP — Claude Code first, everything else supporting
- Daily driver: **Claude Code** (CLI) with a curated `~/.claude` setup he maintains as a SEPARATE GITHUB REPO (`.claude` is its own repo, version-controlled, portable across machines, shared as a system).
- Custom **skills** he authored: yagyesh-dashboard (the X-personalize app he ships from), agent-reach (X automation skill — bookmarks/likes/feed scrape + reply/quote drafting in his voice), caveman (token-compression mode), batch-commit, frontend-design, code-review, blog-writer, react-native-bare-production, react-native-2026-updates, impeccable, taste-skill, redesign-skill, etc.
- Custom **agents** (sub-agents under .claude/agents): blog-writer, yagyesh (X voice), claude-code-guide, react-native-expert, gilfoyle, qa-ux-hacker, etc.
- Uses **Gemini 3 / Antigravity** specifically when he needs pixel-perfect Figma → code matching (Antigravity's vision is better for design parity). NOT for general coding — for design-matching only.

MCPs he ACTUALLY runs (not generic MCP takes — these specific ones with specific workflows):
- **Firebase MCP** — pulls Crashlytics traces / non-fatals / fatal counts directly into Claude Code sessions to triage RN crashes without leaving the terminal.
- **Mixpanel MCP** — diffs client-side events vs. backend events to find observability GAPS (the "we're firing this event on client but it never lands in BQ" class of bugs). This is a non-obvious workflow most people don't know is possible.
- **Notion MCP** — reads/writes the docs system he maintains per-project.
- He's intentionally trimmed his MCP list — opinions on which ones earn their keep, which ones bloat the context window, which ones are wrappers around APIs that should just be HTTP tools.

PROJECT — KAVANA (heykavana.com — primary product, React Native bare workflow)
- Recent release shipped MAJOR crash-rate improvement — they **rewrote the Razorpay bridging natively** (replaced JS bridge / community SDK with a native Android/iOS module). This single change killed a huge slice of payment-flow crashes.
- Multiple other native bridge / native module rewrites. Hermes upgrades. Fabric migration considerations. Reanimated 4 worklets. MMKV migration. OEM-specific bugs (Samsung/Xiaomi blur/scroll/keyboard). App Store rejection sagas.
- His Kavana release cadence + the polish-commits + worktree pattern.

DOCS DISCIPLINE
- He maintains a **structured docs/ tree inside every project** (issues/, solutions/, decisions/, ADRs) specifically so any AI agent he hands off to has full context. Treating context as a first-class artifact — not just code. The docs structure IS the workflow.

PRODUCT SIDE — the X / blog stack
- **yagyesh-dashboard** — local-first single-page app he built (the one currently rendering this idea grid). Scans his X bookmarks + likes + home feed + own posts, scores tweets against his interest signature, drafts replies/quotes/posts in his voice using `claude --agent yagyesh`, posts via the agent-reach CLI. Refresh button reruns the whole pipeline.
- **agent-reach** skill — gives any agent eyes on 17 platforms (Twitter/X, Reddit, YouTube, etc.). He built X automation on top of it.
- **Lead generation pipeline** using an agent harness (local-lead-scraper skill) — scrapes Google Maps by niche+city, audits websites for outdated tech, enriches with emails, ranks for cold outreach.

VOICE / DIRECTION
- He ships in public. Builds tools FOR HIMSELF first, then writes about the system.
- Treats Claude Code like a dev team, not a chatbot — one pane per agent persona, parallel sessions per feature.
- Believes in **memetic-engineering-by-tooling** — if a workflow can't be a skill, it's not a workflow.

────────────────────────────────────────────────────────
ALREADY-PUBLISHED TITLES (DO NOT propose, paraphrase, or "Part 2" these unless the new angle is meaningfully deeper):
────────────────────────────────────────────────────────
__PAST_TITLES__

────────────────────────────────────────────────────────
PROJECT SIGNALS (recent commits / files / readme — raw seed material):
────────────────────────────────────────────────────────
__PROJECTS__

────────────────────────────────────────────────────────
HARD BANS — IDEAS THAT WILL BE REJECTED
────────────────────────────────────────────────────────
DO NOT generate ideas in these shapes — they are too generic / too shallow / overused / not on-brand for this audience:

1. **Tiny-component / "I built X in N lines" posts.** No: "Notion-style auto-resizing textarea in 40 lines", "Image cropper in 80 lines", "I built a swipe gesture in 60 lines". Reason: trivial code, no real story, beginner energy.

2. **Generic UI-bug posts.** No: "5 React Navigation stack bugs", "modal bleed on chat screen", "swipe-fade jank on intro reveal". Reason: every RN dev has had these. Not interesting unless tied to a deeper systems insight.

3. **Surface-level tool reviews / "Forget X" hot takes.** No: "Forget Cursor — terminal is where work happens", "Antigravity after 30 days — honest review", "MCP is bloated duct tape". Reason: tired format. Only acceptable if the comparison includes a specific workflow that breaks under one tool and not the other, with receipts.

4. **Basic worktree / plan-mode / batch-commit content** unless it's about a non-obvious failure mode at scale (50+ commits in, race conditions between worktrees, etc).

5. **Generic "5 things to do as an indie dev"** advice posts. He's past that audience.

6. **"How I stopped doing X" without a real production receipt.**

7. **Caveman / token-compression takes** — already published. Don't go near.

────────────────────────────────────────────────────────
WHAT GOOD LOOKS LIKE — SEEDS WORTH PURSUING
────────────────────────────────────────────────────────
Target one idea from EACH of these buckets when possible:

A. **Native bridge / native module rewrite stories** — Razorpay native bridging killing payment crashes at Kavana, OEM-specific JNI fixes, KeyChain/Keystore native code, Notification channel native config. Concrete crash-rate numbers, before/after stack traces.

B. **MCP-driven debugging workflows** — Firebase MCP for crashanalytics triage, Mixpanel MCP for client↔backend event gap detection, Notion MCP for context-loading. The specific PROMPT patterns that make these workflows actually faster than the dashboard UI.

C. **Multi-agent / multi-pane orchestration in cmux** — one pane per feature, one pane per persona (blog-writer / qa-ux-hacker / code-review), session-per-project setup, how panes share or DON'T share context. Why he left Warp.

D. **.claude as a portable system** — versioning `.claude/` as its own repo, skill design discipline, agent definitions, what gets promoted from a one-off prompt to a skill vs. an agent, the diff between memory/skill/agent/command.

E. **Docs-as-context discipline** — the docs/ tree inside Kavana, structured ADRs, issues/, solutions/. Why treating docs as agent-fuel changes engineering hygiene.

F. **Product built on agent infra (meta)** — yagyesh-dashboard self-hosting his content workflow, agent-reach as a sensor net, lead-scraper as outbound automation. Building products by composing skills, not microservices.

G. **Crashlytics / Mixpanel observability** stories tied to specific commits in Kavana that moved the needle.

H. **Real Kavana production engineering wins** — Hermes V1 adoption, Fabric/New Architecture migration receipts, MMKV migration, App Store rejection appeals, Android 14/15-specific fixes, foreground service notifications, biometric prompt edge cases.

────────────────────────────────────────────────────────
TITLE PATTERNS HE USES
────────────────────────────────────────────────────────
- Bold-claim+clause: "I Rewrote Razorpay Natively — Crash Rate Dropped 60%."
- "How I [verb] [specific outcome] using [specific tool/MCP]"
- "The [N] [specific MCPs / panes / bridges] That [outcome]"
- "Why I Left [Tool] for [Tool] After [Time]"
- "What [Specific MCP/Skill] Actually Replaces In My Stack"

NO clickbait. NO "secret hack". NO "you won't believe". NO listicles without a real story underneath.

────────────────────────────────────────────────────────
OUTPUT
────────────────────────────────────────────────────────
Generate EXACTLY 12 fresh ideas. Each must:
- Hit one of buckets A–H above (mention which in the angle).
- Cite a SPECIFIC tool/MCP/project/native-module/pane-setup name — never "an AI tool" or "an MCP".
- Be at staff-level depth — not "here's how to start" but "here's the failure mode I hit at scale and what I did".
- Be a TITLE he'd click himself.

Return STRICT JSON only — no prose, no fences:
[
  {"title": "...", "angle": "<one-sentence pitch — must name the bucket (A–H) and the specific tool/project>", "source": "project|workflow|tool-review|opinion|production-fix", "source_ref": "<project path OR null>"},
  ...
]
"""

VARIATIONS_PROMPT = """Generate 10 distinct title variations for the following blog idea.

Original idea: __TITLE__
Angle: __ANGLE__

Audience: working engineers / indie builders / AI-tooling enthusiasts. NOT beginners.

Each variation must:
- match one of Yagyesh's title patterns (bold-claim+clause, "How I X in Y", "X vs Y honest breakdown", "[N] [things] that [action]", "Why your X [problem]", "I made X do Y").
- be a fully-formed clickable title (Title Case, em dash optional).
- avoid clickbait fluff ("you won't believe", "secret hack", etc.).
- vary in angle/emphasis — not just paraphrases of the same words.

Return STRICT JSON only — no prose, no fences:
{"variations": ["...", "...", ... 10 items]}
"""

DRAFT_PROMPT = """Write a full Medium blog post draft for the following topic, in Yagyesh's voice.

Title (use this — or a refined variant from variations if listed below): __TITLE__
Angle: __ANGLE__
Variations available (you may pick one if it's a clearer title): __VARIATIONS__

Context to draw from (cite real specifics, do NOT invent projects/numbers):
- Past blog titles: __PAST_TITLES__
- Project signals: __PROJECTS__

Constraints — load-bearing:
- 600–1200 words.
- BLOG ANATOMY: H1 → italic source line → opening → 3–7 H2 sections → close.
- Voice rules from the blog-writer agent. Em dashes, fragments, no AI-tells.
- Audience is working engineers / indie builders — NOT beginners. No setup-from-scratch paragraphs.
- Use specific tool / project / number — never "an AI assistant" or "a few sessions".
- Close with imperative + CTA, rhetorical question, "Stop X. Start Y." directive, or `*Keep learning & keep building* ✌️` signoff.

Output ONLY the markdown body — H1 first line, italic `*Source: ...*` second, content after. No preamble. No code fences around the whole thing.
"""

REVISE_PROMPT = """Revise the following blog draft based on the comments below.

Comments are the source of truth — apply them all.

Each comment is one of:
- a line-level edit ("rephrase the H2", "this paragraph is mid", "drop the third bullet")
- a style note ("more em dashes", "less hedging", "don't open with a question")
- a fact correction ("the gratitude app rejection was 3 times not 2")

Apply line edits literally. Apply style notes throughout the draft. Apply fact corrections.

After the revised draft (markdown), output two appended sections:

## Revision Notes
[3-6 bullets — what changed and why. The dashboard strips this before publishing.]

## Agent Update Suggestions
[STRICT JSON array — style rules worth promoting into the agent .md. Empty array if none. Format:
[{"rule": "<one sentence permanent voice rule>", "rationale": "<why — usually 'user repeated this 3+ times'>"}]
Output an empty array `[]` if no rules to promote.]

---
DRAFT:
__DRAFT__

---
COMMENTS:
__COMMENTS__
"""


# ────────────────────  ops  ────────────────────

def refresh_medium(state: dict) -> list[dict]:
    posts = fetch_medium_posts(limit=15)
    state["medium_seen"] = posts
    # Cleanup: published posts must never appear as suggestion-ideas.
    # If any earlier build seeded them, strip them out on every refresh.
    state["ideas"] = [i for i in state["ideas"] if i.get("source") != "medium-archive"]
    save_state(state)
    return posts


def add_project(state: dict, path: str, name: str | None = None) -> dict:
    path_norm = str(Path(path).expanduser())
    for p in state["projects"]:
        if p["path"] == path_norm:
            return p
    entry = {
        "id":       short_id(),
        "path":     path_norm,
        "name":     name or Path(path_norm).name,
        "added_at": now_iso(),
    }
    state["projects"].append(entry)
    save_state(state)
    return entry


def remove_project(state: dict, project_id: str) -> bool:
    before = len(state["projects"])
    state["projects"] = [p for p in state["projects"] if p["id"] != project_id]
    changed = len(state["projects"]) != before
    if changed:
        save_state(state)
    return changed


def generate_ideas(state: dict) -> list[dict]:
    past_titles = [p.get("title") for p in (state.get("medium_seen") or []) if p.get("title")][:15]
    projects_ctx = []
    for proj in state["projects"][:6]:
        sig = project_signal(proj["path"])
        projects_ctx.append({
            "name":     proj.get("name"),
            "path":     sig.get("path"),
            "branch":   sig.get("branch"),
            "readme":   sig.get("readme_h1"),
            "commits":  (sig.get("recent_commits") or [])[:8],
            "files":    (sig.get("recent_files") or [])[:12],
            "error":    sig.get("error"),
        })

    prompt = (
        IDEA_PROMPT
        .replace("__PAST_TITLES__", json.dumps(past_titles, ensure_ascii=False, indent=1))
        .replace("__PROJECTS__",   json.dumps(projects_ctx, ensure_ascii=False, indent=1)[:8000])
    )
    ok, out = call_claude_agent(prompt)
    if not ok:
        raise RuntimeError(f"claude call failed: {out}")
    parsed = extract_json(out)
    if not isinstance(parsed, list):
        raise RuntimeError("claude returned malformed JSON for ideas")

    new_ideas = []
    for item in parsed[:12]:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        new_ideas.append({
            "id":         short_id(),
            "title":      str(item["title"]).strip()[:240],
            "angle":      str(item.get("angle") or "").strip()[:400],
            "source":     str(item.get("source") or "ai").strip()[:40],
            "source_ref": str(item.get("source_ref") or "")[:240] or None,
            "finalized":  False,
            "created_at": now_iso(),
        })
    # wipe previous non-finalized ideas on every fresh generation — keep finalized only
    kept = [i for i in state["ideas"] if i.get("finalized")]
    state["ideas"] = new_ideas + kept
    state["ideas"] = state["ideas"][:80]
    save_state(state)
    return new_ideas


RESEARCH_PROMPT = """You are scouting trending posts on **Reddit** and **X (Twitter)** for fresh blog seed material for Yagyesh Bobde.

Use the **agent-reach** skill (already installed in this Claude session) to search both platforms. Hit them in this order:

REDDIT — use agent-reach reddit search across these subreddits:
- r/reactnative, r/expo, r/ClaudeAI, r/ClaudeCode, r/cursor, r/LocalLLaMA, r/ChatGPTCoding, r/programming, r/SaaS, r/indiehackers, r/sideproject, r/devops, r/learnprogramming (only for finding what beginners are confused about, NOT to copy their level)

X / TWITTER — use agent-reach twitter search for posts (last 7 days, min 50 likes) on:
- "claude code", "claude code skills", "claude code agents", "MCP server", "cursor vs claude", "antigravity", "gemini 3", "react native crash", "razorpay native", "hermes v1", "fabric react native", "expo vs bare", "mixpanel", "firebase crashlytics", "warp terminal", "cmux", "tmux ai"

TARGET: surface posts that are **getting traction** (>200 upvotes on reddit, >100 likes on X, OR strong discussion in comments) AND are about topics Yagyesh actually writes on — see his stack below.

────────────────────────────────────────────────────────
YAGYESH'S STACK (only surface ideas in this lane):
────────────────────────────────────────────────────────
- Terminal: **cmux** (migrated from Warp).
- Primary loop: **Claude Code** with custom skills + sub-agents, `.claude/` as own GitHub repo.
- MCPs he uses daily: Firebase MCP (Crashlytics triage), Mixpanel MCP (client↔backend event gap diffing), Notion MCP (project docs).
- Gemini 3 / Antigravity for Figma→code pixel matching.
- Kavana — React Native bare workflow, native Razorpay bridge rewrite recently killed payment crashes, OEM-specific bug fixing, Hermes/Fabric/MMKV/Reanimated 4 production work.
- Builds tools for himself first: yagyesh-dashboard (X-personalize), agent-reach (multi-platform sensor), lead scraper.
- Audience: staff engineers, founding-engineer indies, AI-tooling power users — NOT beginners.

────────────────────────────────────────────────────────
ALREADY-PUBLISHED titles (DO NOT propose anything that overlaps):
────────────────────────────────────────────────────────
__PAST_TITLES__

────────────────────────────────────────────────────────
RULES
────────────────────────────────────────────────────────
- IGNORE anything generic ("how I made $5k with AI", "5 prompts to be productive", "ChatGPT vs Claude", "X new feature dropped" without analysis).
- IGNORE anything about Cursor/Windsurf/Lovable unless it's a deep workflow comparison with Claude Code.
- IGNORE crypto, NFTs, motivational content.
- IGNORE beginner content (basic tutorials, "I learned X in 24 hours").
- PREFER threads that have a specific failure mode + a specific fix.
- PREFER posts with concrete numbers, specific MCP names, specific RN/native module names.
- Generate ideas that REFRAME the trending discussion through Yagyesh's stack — e.g. if a Reddit thread is debating "MCPs are overhyped" → angle could be "I killed 6 MCPs this month. The 3 I kept earn their keep — receipts."

────────────────────────────────────────────────────────
OUTPUT
────────────────────────────────────────────────────────
Return EXACTLY 8 ideas. Each MUST cite the source post (reddit URL or X URL) in source_ref, NOT a project path.

Return STRICT JSON only — no prose, no fences:
[
  {
    "title": "<click-worthy title in Yagyesh's voice>",
    "angle": "<one sentence — what's trending + how Yagyesh's stack reframes it>",
    "source": "trending",
    "source_ref": "<reddit or x URL of the trending post that seeded this idea>"
  },
  ...
]
"""


def research_trending_ideas(state: dict) -> list[dict]:
    """Scout Reddit + X via agent-reach for trending posts in Yagyesh's lane, then reframe as ideas."""
    past_titles = [p.get("title") for p in (state.get("medium_seen") or []) if p.get("title")][:15]
    prompt = RESEARCH_PROMPT.replace(
        "__PAST_TITLES__",
        json.dumps(past_titles, ensure_ascii=False, indent=1),
    )
    # use the general claude agent (NOT blog-writer) so agent-reach skill is accessible
    ok, out = call_claude_agent(prompt, agent="claude", timeout=420)
    if not ok:
        raise RuntimeError(f"claude call failed: {out}")
    parsed = extract_json(out)
    if not isinstance(parsed, list):
        raise RuntimeError("claude returned malformed JSON for trending research")

    new_ideas = []
    for item in parsed[:8]:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        new_ideas.append({
            "id":         short_id(),
            "title":      str(item["title"]).strip()[:240],
            "angle":      str(item.get("angle") or "").strip()[:400],
            "source":     "trending",
            "source_ref": str(item.get("source_ref") or "")[:400] or None,
            "finalized":  False,
            "created_at": now_iso(),
        })
    # append to existing list (don't wipe — research is additive)
    state["ideas"] = new_ideas + state["ideas"]
    state["ideas"] = state["ideas"][:80]
    save_state(state)
    return new_ideas


PUBLISHED_DRAFTS_DIR = DATA_DIR / "published-drafts"
THUMBNAILS_DIR       = DATA_DIR / "thumbnails"
THUMBNAIL_REFS_DIR   = DATA_DIR / "thumbnail-refs"


THUMBNAIL_BASE_PROMPT = """For this blog create a medium thumbnail which is minimal, with centered text.
Aspect ratio: 16:9"""


THUMBNAIL_AGENT_TASK = """You have the **browser-harness** skill installed. Use it to drive the user's already-running Chrome (CDP) to generate an image on ChatGPT, then save that image to disk.

────────────────────────────────────────
TASK
────────────────────────────────────────
1. Open / focus a tab at https://chatgpt.com. The user is already signed in.
2. Start a new chat. Make sure the model selected supports image generation (the default GPT model does; if image-gen is gated to a specific picker option like "Create image", use that).
3. Paste this exact prompt into the chat composer (preserve newlines):

──── BEGIN PROMPT ────
__PROMPT__
──── END PROMPT ────

4. __REF_INSTRUCTION__
5. Send the message (Enter or the send button).
6. Wait up to 180 seconds for the generated image to render in the assistant response. Poll the DOM — the image typically appears as an `<img>` inside the latest assistant message, with a src that's either a blob:, data:, or a signed cloudfront/openai URL.
7. Download the **highest-resolution** version of that image to this exact absolute path:
   __OUT_PATH__
   Strategies, in order of preference:
   a. If the page exposes a download button on the image (eyedropper / arrow-down icon), trigger it and intercept the download, save to the target path.
   b. Else, fetch the resolved image `src` via the browser context (page.evaluate fetch → arrayBuffer → base64 → write through CDP, or use the browser-harness file-write helper).
   c. Else, right-click → "Save image as…" via CDP input events. Last resort.

────────────────────────────────────────
OUTPUT — load-bearing
────────────────────────────────────────
On success, your VERY LAST line must be exactly:
THUMBNAIL_SAVED: __OUT_PATH__

On failure (rate-limited, login expired, selector broke, image never rendered), your VERY LAST line must be:
THUMBNAIL_FAILED: <one-line reason>

No other lines after the marker. Other output above the marker is fine and useful for debugging."""


def _b64_to_image_file(b64: str, out_dir: Path, suggested_name: str | None = None) -> Path:
    """Decode a data URL or raw base64 and write to disk. Returns the path."""
    import base64
    out_dir.mkdir(exist_ok=True)
    mime = "image/png"
    data = b64
    if b64.startswith("data:"):
        header, _, payload = b64.partition(",")
        m = re.match(r"data:([^;]+);base64", header)
        if m: mime = m.group(1)
        data = payload
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
    name = _slugify(suggested_name or f"ref-{short_id()}")
    out_path = out_dir / f"{name}-{short_id()}.{ext}"
    out_path.write_bytes(base64.b64decode(data))
    return out_path


def generate_thumbnail(state: dict, draft_id: str, additional_text: str = "",
                       ref_image_b64: str | None = None,
                       ref_image_name: str | None = None,
                       prompt_override: str | None = None) -> dict:
    """Drive a claude-with-browser-harness agent to generate a thumbnail on ChatGPT."""
    draft = next((d for d in state["drafts"] if d["id"] == draft_id), None)
    if not draft:
        raise ValueError("draft not found")
    title = (draft.get("title") or "untitled").strip()

    # build the prompt that goes into chatgpt
    if prompt_override and prompt_override.strip():
        base = prompt_override.strip()
    else:
        base_parts = [THUMBNAIL_BASE_PROMPT, "", f"Blog title: {title}"]
        idea_id = draft.get("idea_id")
        idea_obj = next((i for i in state["ideas"] if i["id"] == idea_id), None) if idea_id else None
        if idea_obj and idea_obj.get("angle"):
            base_parts.append(f"Angle: {idea_obj['angle']}")
        base = "\n".join(base_parts)
    parts = [base]
    if additional_text.strip():
        parts.extend(["", "Additional direction:", additional_text.strip()])
    chatgpt_prompt = "\n".join(parts)

    # save ref image to disk if attached
    ref_path: Path | None = None
    if ref_image_b64:
        ref_path = _b64_to_image_file(ref_image_b64, THUMBNAIL_REFS_DIR, ref_image_name or "ref")

    # output location
    THUMBNAILS_DIR.mkdir(exist_ok=True)
    slug = _slugify(title)
    out_path = THUMBNAILS_DIR / f"{slug}-{short_id()}.png"

    ref_instr = (
        f"Attach the reference image at `{ref_path}` to the chat composer "
        f"(use the paperclip/attach button — drag-drop into the composer also works in CDP). "
        f"Wait for the upload thumbnail to appear before sending."
    ) if ref_path else "(No reference image attached — skip this step.)"

    agent_task = (
        THUMBNAIL_AGENT_TASK
        .replace("__PROMPT__",          chatgpt_prompt)
        .replace("__OUT_PATH__",        str(out_path))
        .replace("__REF_INSTRUCTION__", ref_instr)
    )

    # browser automation can be slow — give it real time
    ok, output = call_claude_agent(agent_task, agent="claude", timeout=900)
    if not ok:
        raise RuntimeError(f"claude call failed: {output[:600]}")

    last = (output or "").strip().splitlines()
    marker = next((ln for ln in reversed(last) if ln.startswith("THUMBNAIL_SAVED:") or ln.startswith("THUMBNAIL_FAILED:")), "")
    if marker.startswith("THUMBNAIL_FAILED:"):
        raise RuntimeError(marker.split(":", 1)[1].strip() or "agent reported failure")
    if not marker.startswith("THUMBNAIL_SAVED:"):
        raise RuntimeError(f"agent did not emit a marker. tail: {(output or '')[-400:]}")
    claimed = marker.split(":", 1)[1].strip()
    saved_path = Path(claimed) if claimed else out_path
    if not saved_path.exists() or saved_path.stat().st_size == 0:
        raise RuntimeError(f"agent claimed save at {saved_path} but file is missing or empty")

    # persist on draft
    draft["thumbnail_path"]   = str(saved_path)
    draft["thumbnail_prompt"] = chatgpt_prompt
    draft["thumbnail_ref"]    = str(ref_path) if ref_path else None
    draft["thumbnail_at"]     = now_iso()
    save_state(state)

    return {
        "ok":             True,
        "path":           str(saved_path),
        "url":            f"/thumbnails/{saved_path.name}",
        "prompt":         chatgpt_prompt,
        "ref_path":       str(ref_path) if ref_path else None,
        "draft_id":       draft_id,
        "generated_at":   draft["thumbnail_at"],
    }





def _slugify(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s\-]", "", text.lower()).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:maxlen].strip("-") or "untitled"


def publish_draft(state: dict, draft_id: str) -> dict:
    """Archive a draft to disk + mark the idea as published.

    Returns a dict the frontend can act on:
      { path, slug, title, content, idea_id, published_at }

    The frontend handles clipboard copy + medium.com/new-story open.
    No external API call — Medium has no draft-create endpoint without OAuth.
    """
    draft = next((d for d in state["drafts"] if d["id"] == draft_id), None)
    if not draft:
        raise ValueError("draft not found")
    if not (draft.get("content") or "").strip():
        raise ValueError("draft is empty — nothing to publish")

    title = (draft.get("title") or "untitled").strip()
    content = _strip_draft_preamble(draft["content"])
    ts = datetime.now(timezone.utc)
    slug = _slugify(title)
    fname = f"{ts.strftime('%Y-%m-%d')}-{slug}.md"

    PUBLISHED_DRAFTS_DIR.mkdir(exist_ok=True)
    out_path = PUBLISHED_DRAFTS_DIR / fname

    # write a clean markdown file — no YAML front matter (medium paste-flow doesn't want it)
    out_path.write_text(content + "\n", encoding="utf-8")

    idea_id = draft.get("idea_id")
    idea = next((i for i in state["ideas"] if i["id"] == idea_id), None) if idea_id else None
    if idea is not None:
        idea["published"] = True
        idea["published_at"] = now_iso()
        idea["published_path"] = str(out_path)

    draft["published_at"] = now_iso()
    draft["published_path"] = str(out_path)
    save_state(state)

    return {
        "ok":            True,
        "path":          str(out_path),
        "slug":          slug,
        "title":         title,
        "content":       content,
        "idea_id":       idea_id,
        "draft_id":      draft_id,
        "published_at":  draft["published_at"],
        "medium_url":    "https://medium.com/new-story",
    }


def clear_ideas(state: dict, keep_finalized: bool = True) -> int:
    """Wipe ideas (and their orphaned drafts). Returns count removed."""
    before = len(state["ideas"])
    if keep_finalized:
        kept_ideas = [i for i in state["ideas"] if i.get("finalized")]
    else:
        kept_ideas = []
    kept_ids = {i["id"] for i in kept_ideas}
    state["ideas"] = kept_ideas
    state["drafts"] = [d for d in state["drafts"] if d.get("idea_id") in kept_ids]
    removed = before - len(kept_ids)
    save_state(state)
    return removed


def ideas_from_medium(state: dict) -> list[dict]:
    """Inject medium scraped items directly as seed ideas (lighter than calling claude)."""
    out = []
    existing_titles = {i["title"].lower() for i in state["ideas"]}
    for p in state.get("medium_seen") or []:
        t = (p.get("title") or "").strip()
        if not t or t.lower() in existing_titles:
            continue
        out.append({
            "id":         short_id(),
            "title":      t,
            "angle":      p.get("summary") or "",
            "source":     "medium-archive",
            "source_ref": p.get("url"),
            "finalized":  False,
            "created_at": now_iso(),
        })
    if out:
        state["ideas"] = out + state["ideas"]
        state["ideas"] = state["ideas"][:80]
        save_state(state)
    return out


def finalize_idea(state: dict, idea_id: str) -> dict | None:
    for i in state["ideas"]:
        if i["id"] == idea_id:
            i["finalized"] = True
            save_state(state)
            return i
    return None


def unfinalize_idea(state: dict, idea_id: str) -> dict | None:
    for i in state["ideas"]:
        if i["id"] == idea_id:
            i["finalized"] = False
            save_state(state)
            return i
    return None


def delete_idea(state: dict, idea_id: str) -> bool:
    before = len(state["ideas"])
    state["ideas"] = [i for i in state["ideas"] if i["id"] != idea_id]
    changed = before != len(state["ideas"])
    # also nuke drafts attached to this idea
    state["drafts"] = [d for d in state["drafts"] if d.get("idea_id") != idea_id]
    if changed:
        save_state(state)
    return changed


def generate_variations(state: dict, idea_id: str) -> list[str]:
    idea = next((i for i in state["ideas"] if i["id"] == idea_id), None)
    if not idea:
        raise ValueError("idea not found")
    prompt = (
        VARIATIONS_PROMPT
        .replace("__TITLE__", idea["title"])
        .replace("__ANGLE__", idea.get("angle") or "")
    )
    ok, out = call_claude_agent(prompt, timeout=180)
    if not ok:
        raise RuntimeError(f"claude call failed: {out}")
    parsed = extract_json(out) or {}
    variations = parsed.get("variations") if isinstance(parsed, dict) else None
    if not isinstance(variations, list):
        raise RuntimeError("claude returned malformed JSON for variations")
    variations = [str(v).strip() for v in variations if str(v).strip()][:10]
    # cache on the idea for quick re-show
    idea["variations"] = variations
    save_state(state)
    return variations


def _get_or_create_draft(state: dict, idea_id: str) -> dict:
    for d in state["drafts"]:
        if d.get("idea_id") == idea_id:
            return d
    idea = next((i for i in state["ideas"] if i["id"] == idea_id), None)
    if not idea:
        raise ValueError("idea not found")
    draft = {
        "id":         short_id(),
        "idea_id":    idea_id,
        "title":      idea["title"],
        "content":    "",
        "comments":   [],
        "versions":   [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    state["drafts"].append(draft)
    return draft


def generate_draft(state: dict, idea_id: str, override_title: str | None = None) -> dict:
    idea = next((i for i in state["ideas"] if i["id"] == idea_id), None)
    if not idea:
        raise ValueError("idea not found")
    title = (override_title or idea["title"]).strip()

    past_titles = [p.get("title") for p in (state.get("medium_seen") or []) if p.get("title")][:10]
    projects_ctx = []
    for proj in state["projects"][:4]:
        sig = project_signal(proj["path"])
        projects_ctx.append({
            "name":    proj.get("name"),
            "branch":  sig.get("branch"),
            "readme":  sig.get("readme_h1"),
            "commits": (sig.get("recent_commits") or [])[:6],
        })

    prompt = (
        DRAFT_PROMPT
        .replace("__TITLE__",       title)
        .replace("__ANGLE__",       idea.get("angle") or "")
        .replace("__VARIATIONS__",  json.dumps(idea.get("variations") or [], ensure_ascii=False))
        .replace("__PAST_TITLES__", json.dumps(past_titles, ensure_ascii=False))
        .replace("__PROJECTS__",    json.dumps(projects_ctx, ensure_ascii=False)[:6000])
    )
    ok, out = call_claude_agent(prompt, timeout=360)
    if not ok:
        raise RuntimeError(f"claude call failed: {out}")

    draft = _get_or_create_draft(state, idea_id)
    # archive previous content as a version snapshot
    if draft["content"]:
        draft["versions"].append({
            "content":  draft["content"],
            "saved_at": draft.get("updated_at") or now_iso(),
            "source":   "auto-archive",
        })
        draft["versions"] = draft["versions"][-10:]
    draft["title"]      = title
    draft["content"]    = _strip_draft_preamble(out)
    draft["updated_at"] = now_iso()
    save_state(state)
    return draft


def update_draft_content(state: dict, draft_id: str, content: str, title: str | None = None) -> dict:
    for d in state["drafts"]:
        if d["id"] == draft_id:
            if d["content"] and content != d["content"]:
                d["versions"].append({
                    "content":  d["content"],
                    "saved_at": d.get("updated_at") or now_iso(),
                    "source":   "manual-edit",
                })
                d["versions"] = d["versions"][-10:]
            d["content"] = content
            if title is not None:
                d["title"] = title.strip()
            d["updated_at"] = now_iso()
            save_state(state)
            return d
    raise ValueError("draft not found")


def add_comment_and_revise(state: dict, draft_id: str, comment_text: str) -> dict:
    """Append a comment, then call claude to revise the draft + merge agent updates."""
    draft = next((d for d in state["drafts"] if d["id"] == draft_id), None)
    if not draft:
        raise ValueError("draft not found")
    comment = {
        "id":   short_id(),
        "text": comment_text.strip(),
        "ts":   now_iso(),
    }
    draft["comments"].append(comment)
    save_state(state)

    if not draft["content"].strip():
        return draft  # nothing to revise yet — comment is parked

    comments_blob = "\n\n".join(
        f"[{c['ts']}] {c['text']}" for c in draft["comments"][-20:]
    )
    prompt = (
        REVISE_PROMPT
        .replace("__DRAFT__",    draft["content"])
        .replace("__COMMENTS__", comments_blob)
    )
    ok, out = call_claude_agent(prompt, timeout=360)
    if not ok:
        raise RuntimeError(f"claude call failed: {out}")

    # split out the trailing meta-sections from the body
    body, agent_updates = _split_revision_output(out)

    # archive prev content
    draft["versions"].append({
        "content":  draft["content"],
        "saved_at": draft.get("updated_at") or now_iso(),
        "source":   "pre-revision",
    })
    draft["versions"] = draft["versions"][-10:]
    draft["content"]    = _strip_draft_preamble(body)
    draft["updated_at"] = now_iso()
    save_state(state)

    applied = []
    if agent_updates:
        applied = apply_agent_updates(agent_updates, source_comment=comment_text)

    draft["last_applied_agent_updates"] = applied
    save_state(state)
    return draft


def _split_revision_output(raw: str) -> tuple[str, list[dict]]:
    """Strip the trailing `## Revision Notes` + `## Agent Update Suggestions` sections."""
    # Find Agent Update Suggestions first — JSON lives under it.
    aus_re = re.search(r"\n##+\s*Agent Update Suggestions\s*\n", raw, re.I)
    agent_updates: list[dict] = []
    if aus_re:
        tail = raw[aus_re.end():]
        parsed = extract_json(tail)
        if isinstance(parsed, list):
            agent_updates = [
                {"rule": str(x.get("rule") or "").strip(),
                 "rationale": str(x.get("rationale") or "").strip()}
                for x in parsed
                if isinstance(x, dict) and x.get("rule")
            ]
        raw = raw[:aus_re.start()]

    rn_re = re.search(r"\n##+\s*Revision Notes\s*\n", raw, re.I)
    if rn_re:
        raw = raw[:rn_re.start()]
    return raw.rstrip(), agent_updates


# ────────────────────  agent file  ────────────────────

def read_agent() -> dict:
    with _agent_lock:
        if not BLOG_AGENT_MD.exists():
            return {"path": str(BLOG_AGENT_MD), "content": "", "mtime": None, "error": "file not found"}
        content = BLOG_AGENT_MD.read_text("utf-8", "ignore")
        mtime   = datetime.fromtimestamp(BLOG_AGENT_MD.stat().st_mtime, timezone.utc).isoformat()
        return {"path": str(BLOG_AGENT_MD), "content": content, "mtime": mtime}


def write_agent(content: str) -> dict:
    if not content.startswith("---"):
        raise ValueError("agent file must start with --- (YAML front matter)")
    if "\n---" not in content[3:8000]:
        raise ValueError("agent file missing closing --- for YAML front matter")
    with _agent_lock:
        tmp = BLOG_AGENT_MD.with_suffix(BLOG_AGENT_MD.suffix + ".tmp")
        tmp.write_text(content)
        tmp.replace(BLOG_AGENT_MD)
        mtime = datetime.fromtimestamp(BLOG_AGENT_MD.stat().st_mtime, timezone.utc).isoformat()
    return {"path": str(BLOG_AGENT_MD), "mtime": mtime, "bytes": len(content)}


STYLE_NOTES_MARKER = "## STYLE NOTES (LIVE — merged from user comments over time)"


def apply_agent_updates(updates: list[dict], source_comment: str = "") -> list[dict]:
    """Append agent-update rules under the STYLE NOTES marker. Returns the ones that were applied (new only)."""
    if not updates:
        return []
    with _agent_lock:
        if not BLOG_AGENT_MD.exists():
            return []
        content = BLOG_AGENT_MD.read_text("utf-8", "ignore")
        if STYLE_NOTES_MARKER not in content:
            return []
        existing_norm = re.sub(r"\s+", " ", content.lower())
        applied = []
        new_lines: list[str] = []
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for u in updates:
            rule = u.get("rule", "").strip()
            rationale = u.get("rationale", "").strip()
            if not rule:
                continue
            if rule.lower() in existing_norm:
                continue
            applied.append({"rule": rule, "rationale": rationale, "applied_at": now_iso()})
            note = f"- **[{ts}]** {rule}"
            if rationale:
                note += f"  _(why: {rationale})_"
            new_lines.append(note)
        if not new_lines:
            return []
        addition = "\n" + "\n".join(new_lines) + "\n"
        # insert right after the marker line (and the existing trailing HTML comment if present)
        idx = content.find(STYLE_NOTES_MARKER)
        # find end of line + skip the next blank/comment line
        line_end = content.find("\n", idx)
        if line_end == -1:
            line_end = len(content)
        # try to skip past existing HTML comment after marker
        rest = content[line_end:]
        m = re.search(r"<!--.*?-->", rest, re.S)
        if m and m.start() < 200:
            insert_pos = line_end + m.end()
        else:
            insert_pos = line_end
        new_content = content[:insert_pos] + addition + content[insert_pos:]
        tmp = BLOG_AGENT_MD.with_suffix(BLOG_AGENT_MD.suffix + ".tmp")
        tmp.write_text(new_content)
        tmp.replace(BLOG_AGENT_MD)
        return applied
