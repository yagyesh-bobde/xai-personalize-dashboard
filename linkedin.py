"""linkedin.py — LinkedIn posts pipeline + state. Mirrors blog.py.

Mines the user's own LinkedIn posts + existing X signal into genuinely valuable
post ideas, drafts full posts in a dedicated `linkedin-voice` agent, lets the
user edit/approve, and pre-fills the LinkedIn composer (the user clicks Post).

Pure helpers (extract_json, mine_themes, merge_data) are unit-tested; browser
I/O (linkedin_cli) and claude calls are live-smoke-verified.
"""
import json, os, re, shutil, subprocess, sys, time
from collections import Counter
from pathlib import Path

import linkedin_cli

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "linkedin_data.json"
X_DATA = ROOT / "data" / "dashboard_data.json"
THUMBNAILS_DIR = ROOT / "data" / "linkedin-thumbnails"

# Fixed base prompt for ChatGPT image generation (the post text is appended).
THUMBNAIL_BASE_PROMPT = (
    "For this post create a linkedin thumbnail which is minimal, with centered text.\n"
    "Dark Theme. Has thumbnail type fancy fonts. BUT doesn't use too many assets.\n"
    "It should be minimal. Aspect ratio: 16:9")
AGENT_NAME = (os.environ.get("LINKEDIN_AGENT") or "linkedin-voice").strip()
AGENT_MD = Path(os.environ.get("LINKEDIN_AGENT_MD") or (Path.home() / ".claude" / "agents" / f"{AGENT_NAME}.md"))

_STOP = set("the a an and or to of in is it for on with my our your this that i we you they are be as at".split())


def extract_json(blob: str) -> dict | None:
    blob = re.sub(r"^```(?:json)?\s*", "", blob.strip())
    blob = re.sub(r"\s*```$", "", blob)
    a, b = blob.find("{"), blob.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        return json.loads(blob[a:b + 1])
    except json.JSONDecodeError:
        return None


def mine_themes(posts: list[str], top: int = 12) -> list[str]:
    c = Counter()
    for p in posts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+]{1,}", (p or "").lower()):
            if w not in _STOP:
                c[w] += 1
    return [w for w, _ in c.most_common(top)]


def _index(drafts):
    return {d.get("id"): d for d in drafts if d.get("id")}


def merge_data(old: dict, new: dict) -> dict:
    """Preserve approved/posted drafts; replace draft-status items with new."""
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


# ────────────────────  claude drafting + pipeline  ────────────────────

def _claude_json(prompt: str, timeout: int = 240) -> dict | None:
    cb = shutil.which("claude")
    if not cb:
        sys.stderr.write("[linkedin] claude not found\n")
        return None
    cmd = [cb, "-p", prompt, "--agent", AGENT_NAME, "--effort", "medium"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return extract_json(p.stdout) if p.returncode == 0 else None


def read_x_signal() -> dict:
    """Pull the user's X interest signature from the existing dashboard data.
    The X pipeline writes `interest_signature` (top_keywords/accounts/hashtags)
    and AI-drafted `drafts.posts` in his voice — both are useful thematic signal."""
    empty = {"top_keywords": [], "top_accounts": [], "top_hashtags": [], "recent": []}
    if not X_DATA.exists():
        return empty
    try:
        d = json.loads(X_DATA.read_text())
        sig = d.get("interest_signature") or d.get("signature") or d.get("public_sig") or {}
        drafts = d.get("drafts") or {}
        posts = drafts.get("posts", []) if isinstance(drafts, dict) else []
        recent = [p.get("text", "") for p in posts if isinstance(p, dict)][:6]
        return {"top_keywords": (sig.get("top_keywords") or [])[:15],
                "top_accounts": (sig.get("top_accounts") or [])[:10],
                "top_hashtags": (sig.get("top_hashtags") or [])[:10],
                "recent": recent}
    except Exception:
        return empty


IDEAS_SHAPE = '''Return JSON ONLY (start with { end with }):
{"ideas":[{"id":"i1","angle":"<specific post angle>","source":"linkedin|x-signal","why_valuable":"<one line: who benefits and why>"}]}'''

DRAFT_SHAPE = '''Return JSON ONLY (start with { end with }):
{"text":"<the full LinkedIn post, ready to publish>","why_valuable":"<one line>"}'''


def _ideas_prompt(themes, corpus, xsig, n=8) -> str:
    li_block = ("\n".join(f"- {c[:200]}" for c in corpus[:6])
                if corpus else "(few/no original LinkedIn posts — lean on my X signal "
                "and the real projects/takes in your persona)")
    return (
        "You are running as my LinkedIn voice agent (persona + my real material loaded).\n"
        "Propose LinkedIn post ideas that are genuinely VALUABLE — teach, share a real lesson, "
        "or give an honest take grounded in MY real work. No filler, no engagement-bait, no generic advice.\n\n"
        f"## My recurring LinkedIn themes\n{', '.join(themes) or '(none yet)'}\n\n"
        f"## My recent original LinkedIn posts (continue the narrative, don't repeat)\n{li_block}\n\n"
        f"## My X/Twitter signal (my real interests — adapt the best into long-form LinkedIn value posts)\n"
        f"keywords: {', '.join(xsig.get('top_keywords', []))}\n"
        f"hashtags: {', '.join(xsig.get('top_hashtags', []))}\n"
        f"accounts I engage: {', '.join(xsig.get('top_accounts', []))}\n"
        "recent posts in my voice: " + (" | ".join(xsig.get('recent', [])[:5]) or "(none)") + "\n\n"
        f"## Task\nGive exactly {n} distinct ideas, each grounded in my real projects/stack/takes. "
        "Tag source 'linkedin' (continues my LinkedIn narrative) or 'x-signal' (adapted from my X interests).\n\n"
        + IDEAS_SHAPE)


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
    data["drafts"].append(d)
    idea["status"] = "drafted"
    write_data(data)
    return d


# ────────────────────  state mutation  ────────────────────

def save_draft_text(draft_id: str, text: str) -> dict:
    data = read_data()
    for d in data.get("drafts", []):
        if d.get("id") == draft_id:
            d["text"] = text
            d["edited"] = True
            d["status"] = "approved"
            write_data(data)
            return d
    return {"error": "draft not found"}


def mark_posted(draft_id: str) -> dict:
    data = read_data()
    for d in data.get("drafts", []):
        if d.get("id") == draft_id:
            d["status"] = "posted"
            write_data(data)
            return d
    return {"error": "draft not found"}


def discard_draft(draft_id: str) -> dict:
    data = read_data()
    data["drafts"] = [d for d in data.get("drafts", []) if d.get("id") != draft_id]
    write_data(data)
    return {"ok": True}


def discard_idea(idea_id: str) -> dict:
    data = read_data()
    data["ideas"] = [i for i in data.get("ideas", []) if i.get("id") != idea_id]
    write_data(data)
    return {"ok": True}


def compose(draft_id: str) -> dict:
    data = read_data()
    d = next((x for x in data.get("drafts", []) if x.get("id") == draft_id), None)
    if not d:
        return {"ok": False, "reason": "not_found"}
    thumb = d.get("thumbnail_path")
    if thumb and not os.path.exists(thumb):
        thumb = None
    return linkedin_cli.prefill_composer(d["text"], image_path=thumb)


# ────────────────────  thumbnail generation  ────────────────────

def generate_thumbnail(draft_id: str, timeout: int = 240) -> dict:
    """Generate a 16:9 LinkedIn thumbnail for a draft via ChatGPT (cmux-driven)
    and persist it on the draft. Returns {ok, path?, url?, error?}."""
    data = read_data()
    d = next((x for x in data.get("drafts", []) if x.get("id") == draft_id), None)
    if not d:
        return {"ok": False, "error": "draft not found"}
    prompt = THUMBNAIL_BASE_PROMPT + "\n\nPost:\n" + (d.get("text") or "").strip()
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = THUMBNAILS_DIR / f"{draft_id}-{int(time.time())}.png"
    res = linkedin_cli.chatgpt_generate_image(prompt, out_path, timeout=timeout)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("hint") or res.get("reason") or "generation failed"}
    d["thumbnail_path"] = str(out_path)
    d["thumbnail_prompt"] = prompt
    d["thumbnail_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_data(data)
    return {"ok": True, "path": str(out_path),
            "url": f"/linkedin-thumbnails/{out_path.name}",
            "draft_id": draft_id, "generated_at": d["thumbnail_at"]}


# ────────────────────  agent file  ────────────────────

def read_agent() -> dict:
    if not AGENT_MD.exists():
        return {"error": f"agent file not found: {AGENT_MD}", "content": ""}
    return {"path": str(AGENT_MD), "content": AGENT_MD.read_text()}


def write_agent(content: str) -> dict:
    AGENT_MD.parent.mkdir(parents=True, exist_ok=True)
    AGENT_MD.write_text(content)
    return {"ok": True}


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
