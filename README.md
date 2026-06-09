# xai-personalize-dashboard

A local, single-page web app that personalizes X (Twitter) for you:

- Curates a **For You** feed from your home timeline, scored against your *actual* interest signature (bookmarks + likes).
- Drafts **posts / replies / quote-tweets** in your own voice via a Claude `--agent` you configure.
- **Schedules** drafts to fire later via a background queue, visualized as a proper timeline.
- **Bookmarks** anything from "For You" in one click. Paste images straight into compose. Background daemon optional.

Built around the [`agent-reach`](https://github.com/8090-inc/agent-reach) `twitter` CLI for I/O and the `claude` CLI for drafting. No paid API keys.

Runs entirely on `127.0.0.1` — your X session cookies, your data, your machine.

```
http://127.0.0.1:7873/
```

## Screens

```
01  for you      — curated home-feed items scored vs your signature (5 pages preloaded)
02  trending     — outside your signature, ranked by engagement
03  drafts       — claude-generated post / reply / quote drafts (edit, post, queue)
04  compose      — write your own; ⌘V to paste images
05  scheduled    — timeline view of the queue, grouped by time bucket
06  history      — last 200 posted actions (manual + scheduled)
07  agent        — edit your voice persona; mine new X profiles into the agent
08  blog ideas   — blog idea workspace
09  blog studio  — long-form blog drafting
10  linkedin ideas — value-post ideas from your LinkedIn posts + X signal
11  linkedin drafts — full posts in your linkedin voice → pre-fill composer (you click Post)
```

## LinkedIn workspace

Posts-focused LinkedIn automation driven by the **cmux inline browser** (no LinkedIn API):

- **Refresh** mines your own LinkedIn posts + your X interest signature into genuinely *valuable* post ideas — each carries a one-line *why this is valuable*. No filler.
- **Write full post** drafts a complete LinkedIn-formatted post in a dedicated `linkedin-voice` agent (mined from your real material — editable in `07 agent`).
- **Open in composer** pre-fills LinkedIn's composer in your cmux pane and stops — **you** review and click **Post**. Nothing is ever auto-submitted.

Config in `~/.agent-reach/env.sh`:

```bash
export LINKEDIN_HANDLE="bobde-yagyesh"           # your /in/<handle>, no leading @
export LINKEDIN_AGENT="linkedin-voice"            # optional (default)
export LINKEDIN_AGENT_MD="$HOME/.claude/agents/linkedin-voice.md"  # optional
```

Requires the `cmux` CLI with a logged-in LinkedIn session. **Publishing needs the LinkedIn cmux pane on-screen** — a headless click can't open the composer (you'll get a `pane_hidden` hint asking you to foreground the pane). Reading is headless. Restarting `run.sh` always reclaims the same port (`7873`) from any older instance, so route-not-found after a rebuild just needs a re-run.

## Quick start

### 1. Install `agent-reach` (provides the `twitter` CLI)

Follow [`agent-reach`'s install guide](https://github.com/8090-inc/agent-reach). When it's done, `twitter status` must succeed.

### 2. Install `claude` CLI

[claude.ai/code](https://claude.ai/code) — anything that supports `claude --agent <name> -p "..."`.

Create a voice agent at `~/.claude/agents/<name>.md` describing your tone, vocabulary, openers, and reach templates. (See `examples/voice-agent.example.md` for a starting point.)

### 3. Clone this repo into your Claude skills dir

```bash
git clone https://github.com/yagyesh-bobde/xai-personalize-dashboard.git \
  ~/.claude/skills/xai-personalize-dashboard
```

### 4. Configure env vars

Append to `~/.agent-reach/env.sh`:

```bash
export TWITTER_HANDLE="your_handle"                          # required, no leading @
export DASHBOARD_AGENT="voice"                                # claude --agent <name>
export DASHBOARD_AGENT_MD="$HOME/.claude/agents/voice.md"    # path to your agent .md
export DASHBOARD_PORT="7873"                                  # optional
```

### 5. Run

```bash
~/.claude/skills/xai-personalize-dashboard/run.sh
```

Open `http://127.0.0.1:7873/` and hit **↻ refresh pipeline** in the sidebar. First run takes ~30–60s.

## Architecture

```
┌──────────────────────────────────────────────┐
│  browser  ──►  http://127.0.0.1:7873         │
└──────────────────────────┬───────────────────┘
                           │
                           ▼
            ┌──────────────────────────┐
            │   server.py (stdlib)     │  GET /data /scheduled /history
            │                          │  POST /refresh /post /queue
            │                          │       /schedule /bookmark
            │                          │       /upload /agent /agent/study
            │                          │  DELETE /scheduled/<id>
            └────┬──────────────┬──────┘
                 │              │
       ┌─────────┘              └──────────────┐
       ▼                                       ▼
┌──────────────┐                ┌──────────────────────────┐
│ pipeline.py  │                │  twitter CLI             │
│              │                │  bookmarks|favorites     │
│ 1. fetch     │                │  feed|user-posts         │
│ 2. score     │                │  post|reply|quote        │
│ 3. claude    │ ──► claude --agent <name> -p "…"          │
│ 4. write     │       (returns JSON drafts)               │
└──────────────┘                └──────────────────────────┘
       │
       ▼
  data/dashboard_data.json
```

- **`pipeline.py`** — Python stdlib only. Pulls bookmarks, favorites, home feed, your own posts in parallel; computes an interest signature; scores the feed; calls `claude --agent` for drafts; writes `data/dashboard_data.json`.
- **`server.py`** — Single-file stdlib HTTP server. No Flask, no FastAPI. Serves `/static/*`, exposes JSON endpoints, runs the scheduler thread.
- **`static/`** — Single-page vanilla JS UI (no build step). Tailwind-free, hand-rolled CSS in `style.css`.

## Pipeline scoring

```
score = author_signal × 5  +  keyword_overlap  +  log10(likes + 1) × 0.3
        + (+25 boost if you've ever bookmarked this author)
        − dropped if author_score == 0 AND keyword_overlap < 4
```

This kills the random-viral-tweet problem — a tweet from someone you've never engaged with needs strong keyword overlap to show up. Per-author cap of 3 prevents one prolific account from dominating.

## Voice agent

Define your voice once in `~/.claude/agents/<name>.md`:

- **Persona** — who you are, what you ship, what you read.
- **Voice rules** — lowercase opener, specific numbers, no hashtags, etc.
- **Reach templates** — labeled patterns (e.g. "Two-line Aphorism", "Bracketed Label", "Terse Ship Status") that the agent rotates through.
- **VOICE NEIGHBORHOOD** — handles you sound like, with one-line notes each. The agent's "study new profile" feature appends to this section.

The dashboard's `07 agent` screen lets you live-edit this file and re-mine a new X handle into the voice neighborhood without leaving the browser.

## Optional: auto-start at login (macOS)

```bash
~/.claude/skills/xai-personalize-dashboard/daemon/install-daemon.sh
```

Installs a `launchd` agent that boots the server at login, respawns it on crash, and logs to `/tmp/xai-personalize-dashboard.{log,err}`. Uninstall with `daemon/uninstall-daemon.sh`.

### Twice-daily auto-refresh

```bash
~/.claude/skills/xai-personalize-dashboard/daemon/install-refresh-daemon.sh
```

Installs a separate `launchd` agent (`com.xai-personalize.refresh`) that re-runs the pipeline automatically at **08:00 and 20:00 local time** — no need to click "Refresh pipeline". It runs `daemon/refresh.sh` directly (independent of the server) and logs to `/tmp/xai-personalize-refresh.{log,err}`. Uninstall with `daemon/uninstall-refresh-daemon.sh`.

To change the times, edit the `Hour` values in the two `StartCalendarInterval` entries in `daemon/com.xai-personalize.refresh.plist.tpl`, then re-run the installer.

## Security

- The server binds to `127.0.0.1` only — never exposed to the network.
- The `data/` directory is gitignored and contains your raw X data + scheduled queue. Don't commit it.
- Your X auth tokens live in `~/.agent-reach/env.sh`, never in this repo.
- The `/bookmark` endpoint validates the tweet id matches `^\d{5,25}$` before passing to the CLI.
- The pipeline never sends your data anywhere except the `claude` CLI call to draft tweets.

## Project status

Personal project, opinionated, single-user. Feedback / PRs welcome.

## License

MIT.
