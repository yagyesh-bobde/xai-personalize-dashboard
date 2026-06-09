---
name: xai-personalize-dashboard
description: >
  Open the local personalize-x dashboard — a single-page UI that scans your
  X/Twitter signal (bookmarks, likes, home feed, your own posts), curates
  today's most relevant tweets into a "for you" feed scored against your
  interest signature, and drafts posts / replies / quote-tweets in your own
  voice using a configured `claude --agent <name>` persona. Approve any draft
  to post it via the `agent-reach` twitter CLI. One refresh button reruns the
  entire pipeline.

  Triggers: "open dashboard", "x dashboard", "tweet dashboard",
  "what should I tweet today", "refresh my X dashboard", "show today's tweets".
metadata:
  type: skill
---

# xai-personalize-dashboard

Single-binary local web app at `http://127.0.0.1:7873` that:

1. **Reads your Twitter signal** — bookmarks, likes, home feed, and your own recent posts via the `agent-reach` `twitter` CLI.
2. **Builds an interest signature** — top keywords + accounts you bookmark/like, weighted and used to score the home feed.
3. **Curates a "for you" feed** — up to 50 feed items scored against your signature, paginated 10-at-a-time.
4. **Drafts in your voice** — single call to `claude --agent <DASHBOARD_AGENT>` returns JSON with original posts, replies, and quote-tweets across reach templates defined in your agent persona.
5. **Approves & posts** — every draft card has Edit / Post now / Schedule / Queue. Post hits the `twitter post|reply|quote` CLI directly.
6. **Schedules a queue** — background worker fires due posts every ~15s. Timeline view groups by time bucket (within the hour / later today / tomorrow / this week / later).
7. **Bookmark + paste-to-attach** — bookmark any "for you" item in place; paste images straight into compose / draft textareas.

## Run

```bash
~/.claude/skills/xai-personalize-dashboard/run.sh
```

Server boots on port 7873 (override with `DASHBOARD_PORT`). First load shows empty cards — hit **↻ refresh** to run the pipeline (~30–60s).

## Configuration

Set these in `~/.agent-reach/env.sh` (sourced automatically by `run.sh`):

```bash
export TWITTER_AUTH_TOKEN="..."     # required — used by the twitter CLI
export TWITTER_CT0="..."             # required — used by the twitter CLI
export TWITTER_HANDLE="your_handle"  # required — your handle, no leading @
export DASHBOARD_AGENT="voice"       # optional — claude agent name (default: voice)
export DASHBOARD_AGENT_MD="$HOME/.claude/agents/voice.md"  # optional — agent file path
export DASHBOARD_PORT="7873"         # optional — server port
```

## Files

- `pipeline.py` — fetch → score → draft → write `data/dashboard_data.json`
- `server.py` — stdlib HTTP server: `/data`, `/refresh`, `/post`, `/queue`, `/schedule`, `/scheduled`, `/history`, `/bookmark`, `/upload`, `/agent`, `/agent/study`, plus `/linkedin/*` + `/linkedin-agent*`
- `linkedin_cli.py` — LinkedIn I/O via the **cmux inline browser** CLI (read headless; pre-fill composer)
- `linkedin.py` — LinkedIn pipeline + state: mine own posts + X signal → ideas → full drafts → `data/linkedin_data.json`
- `static/index.html` + `style.css` + `app.js` — dashboard UI
- `run.sh` — launches server (also exec'd by this skill)
- `daemon/` — optional launchd auto-start
- `data/` — local-only state (gitignored)

## LinkedIn workspace

Screens **`10 linkedin ideas`** and **`11 linkedin drafts`** (plus a LinkedIn tab in `07 agent`):

1. **Refresh linkedin** mines your own LinkedIn posts (via cmux browser) + your X interest signature, then drafts genuinely *valuable* post ideas — each with a one-line `why_valuable`.
2. **Write full post** turns an idea into a full LinkedIn-formatted draft in your `linkedin-voice`.
3. **Open in composer** pre-fills LinkedIn's composer in your cmux browser pane — it never auto-submits. You review and click **Post**, then **Mark as posted**.

Config (in `~/.agent-reach/env.sh`): `LINKEDIN_HANDLE` (your `/in/<handle>`, no leading `@`), optional `LINKEDIN_AGENT` (default `linkedin-voice`), `LINKEDIN_AGENT_MD`.

> **cmux browser constraints:** reading is headless; **publishing needs the LinkedIn cmux pane on-screen** (a headless click won't open the composer — `/linkedin/compose` returns `{ok:false, reason:"pane_hidden"}` with a hint). LinkedIn's infinite scroll only loads a few items when the pane is hidden, and reposts are filtered out, so `my_posts()` returns the reachable *original* posts (often few) — the voice agent leans on your X signal + real material to compensate.

## Dependencies

- `twitter` CLI from `agent-reach` on PATH
- `cmux` CLI (the cmux.app inline browser) for the LinkedIn workspace, logged into LinkedIn
- `claude` CLI with your voice agents at `~/.claude/agents/<DASHBOARD_AGENT>.md` and `~/.claude/agents/linkedin-voice.md`
- Python 3.10+ (stdlib only)

## Troubleshooting

- **Empty feed / no drafts:** run `twitter status` — your X session may have expired.
- **`claude call timed out`:** the pipeline gives up after 240s. Re-run; if it persists, try `claude --agent <name> -p "hi"` manually.
- **`linkedin load failed: route not found`:** your running server is an older build. Just re-run `run.sh` — it automatically reclaims the same port (`7873`) from the old instance and takes over. No need to hunt for the PID.
- **Port in use:** `run.sh` reclaims `7873` from a previous instance of this server automatically. If a *different* app holds the port, it won't be killed — set `DASHBOARD_PORT=7900 run.sh` instead.
- **LinkedIn `pane_hidden` on Open in composer:** bring your LinkedIn cmux browser pane to the front (it must be on-screen for the composer to open), then click **Open in composer** again.
- **Posted the wrong thing:** the dashboard does not undo. Delete from X directly: `twitter delete <id>`.
