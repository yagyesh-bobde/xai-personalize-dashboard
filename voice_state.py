"""Machine-managed learned voice state injected into draft prompts.

The eval (eval_engine.py) writes this file automatically; pipeline.py reads it
and appends the formatted blocks to every draft prompt. Lives in data/ (gitignored).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "voice_state.json"

KEYS = ("gold", "anti", "rules")
CAPS = {"gold": 20, "anti": 20, "rules": 12}


def _empty() -> dict:
    return {k: [] for k in KEYS}


def load_state(path=None) -> dict:
    path = path or STATE_PATH
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty()
    return {k: list(data.get(k) or []) for k in KEYS}


def save_state(state: dict, path=None) -> None:
    path = path or STATE_PATH
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({k: list(state.get(k) or []) for k in KEYS},
                            indent=2, ensure_ascii=False))


def merge_state(state: dict, *, gold=None, anti=None, rules=None) -> dict:
    out = {k: list(state.get(k) or []) for k in KEYS}
    for key, new in (("gold", gold), ("anti", anti), ("rules", rules)):
        for item in (new or []):
            item = (item or "").strip()
            if item and item not in out[key]:
                out[key].append(item)
        out[key] = out[key][-CAPS[key]:]
    return out


def format_for_prompt(state: dict) -> str:
    state = {k: list(state.get(k) or []) for k in KEYS}
    if not any(state.values()):
        return ""
    parts = []
    if state["gold"]:
        parts.append("## LEARNED — drafts you've kept (match this texture)\n"
                     + "\n".join(f"- {g}" for g in state["gold"]))
    if state["anti"]:
        parts.append("## LEARNED — drafts I rejected, do NOT write like these\n"
                     + "\n".join(f"- {a}" for a in state["anti"]))
    if state["rules"]:
        parts.append("## LEARNED — extra voice rules\n"
                     + "\n".join(f"- {r}" for r in state["rules"]))
    return "\n\n".join(parts) + "\n"
