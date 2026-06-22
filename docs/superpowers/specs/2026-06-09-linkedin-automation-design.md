# LinkedIn Automation Feature — Design Spec

**Date:** 2026-06-09
**Status:** Approved (design), pending implementation
**Author:** Yagyesh + Claude

## Goal

Add a LinkedIn posting workspace to the personalize-x dashboard: mine the user's
own LinkedIn posts + existing X signal to generate genuinely **valuable** post
ideas, draft full posts in a dedicated LinkedIn voice, let the user edit/approve,
and pre-fill the LinkedIn composer (via the cmux inline browser) so the user does
a final review and clicks **Post** themselves.

No filler. Every idea and draft carries a one-line *why this is valuable*.

## Locked decisions

| Decision | Choice |
|----------|--------|
| Publish mechanism | **Pre-fill composer, user clicks Post** (never auto-submit) |
| v1 scope | **Posts-focused**: ideas → full drafts → edit/approve → pre-fill. No feed curation, no reply drafting, no LinkedIn scheduling. |
| Idea signal | **Past LinkedIn posts + existing X signal** (`data/dashboard_data.json`) |
| Voice | **New dedicated** `~/.claude/agents/linkedin-voice.md`, mined from real LinkedIn posts, live-editable |
| I/O transport | **cmux inline browser CLI** (`cmux browser <surface> ...`) |

## cmux browser — verified constraints (drive the whole design)

These were tested live against the logged-in session (profile `bobde-yagyesh`):

1. **Reading is fully headless & reliable.** `navigate`, `snapshot --max-depth N`,
   `get text --selector <css>` work even when the browser pane is off-screen.
   Post bodies scrape from `.feed-shared-update-v2__description` /
   `.update-components-update-v2__commentary`.
2. **Interactive UI mutation needs the pane VISIBLE/active.** A headless `click`
   on "Start a post" returns `OK` but the composer modal never opens;
   `focus-webview` fails with `invalid_state: WebView is hidden`. The CLI exposes
   no force-focus. → The **pre-fill/publish step requires the LinkedIn cmux pane
   to be the on-screen pane.** This matches the "you click Post" decision: the
   user is looking at that pane anyway.
3. **`eval` and `find role` are UNSUPPORTED** (throw `js_error` on WKWebView).
   Use snapshot refs + CSS selectors + `get/click/fill/type` only. Never inject JS.
4. `get text --selector` returns only the FIRST visible match; iterate via
   per-element selectors or repeated scraping for multiple posts.
5. WKWebView returns `not_supported` for viewport/offline/trace/network-route.

## Architecture

Mirrors the existing **blog feature** integration pattern (`blog.py` module +
`/blog/*` routes + `blog_mod` import in `server.py` + dedicated UI screens +
own data file + own agent). LinkedIn follows the same shape.

```
dashboard UI (new "linkedin ideas" + "linkedin drafts" screens, linkedin tab in agent screen)
        │  fetch() → server.py  (new /linkedin/* routes, linkedin_mod import)
        ▼
┌──────────────────────┐     ┌───────────────────────────┐     ┌─────────────────────┐
│ linkedin_cli.py      │     │ linkedin.py (pipeline+state)│ ──► │ claude -p           │
│ cmux browser wrapper │ ◄── │ mine → ideas → draft        │     │ --agent linkedin-   │
│ (read headless,      │     │ merge-preserving writes     │     │ voice (JSON drafts) │
│  pre-fill foreground)│     │ data/linkedin_data.json     │     └─────────────────────┘
└──────────────────────┘     └───────────────────────────┘
        │ reads X signal from existing data/dashboard_data.json
```

### Modules (all Python stdlib, matching existing style)

**`linkedin_cli.py`** — thin wrapper over the cmux browser CLI.
- `_cmux()` → resolve cmux bin (`/Applications/cmux.app/Contents/Resources/bin/cmux`, fallback `which cmux`).
- `resolve_surface()` → find/open a LinkedIn browser surface; cache its ref.
- `is_logged_in()` → navigate `/feed/`, confirm title `Feed | LinkedIn` (not authwall).
- `my_posts(limit=20)` → navigate `/in/<handle>/recent-activity/all/`, scroll N
  times, scrape post bodies. Returns `[{text, ...}]`. **Headless.**
- `profile()` → `{handle, headline}` from profile page snapshot/title.
- `prefill_composer(text)` → **foreground path**: navigate `/feed/` → snapshot →
  click "Start a post" ref → `fill`/`type` editor → STOP. Returns
  `{ok:true}` or `{ok:false, reason:"pane_hidden", hint:"..."}` if the modal
  didn't open (detected by waiting for composer text and timing out).
- All read fns degrade gracefully (return `[]`/`None` + stderr note) on failure.

**`linkedin.py`** — pipeline + state (the blog.py analog).
- `mine()` → `linkedin_cli.my_posts()` → store `style_corpus` + derive `themes`
  (recurring topic tokens, like the X interest signature but from own posts).
- `read_x_signal()` → load `data/dashboard_data.json`, pull top keywords/accounts
  + recent posts as cross-pollination material.
- `generate_ideas()` → one `claude -p --agent linkedin-voice` call combining
  themes + X signal → candidate angles, each tagged `source` (`linkedin`/`x-signal`)
  and `why_valuable`.
- `draft(idea_id|idea)` → one `claude -p` call → full LinkedIn-formatted post(s)
  with `why_valuable`. Reuses the `extract_json` / `_claude_json` pattern.
- `read_data()` / `write_data()` → merge-preserving: never clobber drafts whose
  `status` is `approved`/`posted`.
- `refresh()` → mine + generate_ideas (does NOT auto-draft every idea).
- Agent helpers: `read_agent()`, `write_agent()`, `remine_voice()` (rebuild the
  agent .md from scraped posts), mirroring server.py's agent helpers.

**`~/.claude/agents/linkedin-voice.md`** — dedicated voice agent.
- Seeded from real scraped posts (the `→` arrow bullets, em-dashes, short
  declarative lines, structured What-we-need/What-you-get sections, direct CTAs).
- Long-form/professional register, distinct from the lowercase-sloppy X voice.
- Live-editable in the dashboard; "re-mine my posts" rebuilds the gold examples.

### server.py additions (mirror blog routes)

- `import linkedin as linkedin_mod`
- GET `/linkedin/data` → `linkedin_mod.read_data()`
- GET `/linkedin-agent` → read `linkedin-voice.md`
- POST `/linkedin/refresh` → `linkedin_mod.refresh()` (mine + ideas)
- POST `/linkedin/draft` → `{idea_id}` → generate full draft
- POST `/linkedin/draft/save` → `{id, text}` → save edited draft text
- POST `/linkedin/compose` → `{id}` → `linkedin_cli.prefill_composer(text)`;
  returns ok or `pane_hidden` hint
- POST `/linkedin/mark-posted` → `{id}` → set draft status `posted`
- POST `/linkedin-agent` → save agent .md
- POST `/linkedin-agent/remine` → re-mine posts into agent
- DELETE `/linkedin/drafts/<id>` and `/linkedin/ideas/<id>` → discard

### Data file: `data/linkedin_data.json` (gitignored, like other data/)

```jsonc
{
  "generated_at": "ISO8601",
  "profile": { "handle": "bobde-yagyesh", "headline": "..." },
  "style_corpus": ["...scraped past posts..."],
  "themes": ["shipping ai agents", "rn in prod", "..."],
  "ideas": [
    { "id": "i1", "angle": "...", "source": "linkedin|x-signal",
      "why_valuable": "one line", "status": "idea|drafted" }
  ],
  "drafts": [
    { "id": "d1", "idea_id": "i1", "text": "full post text",
      "why_valuable": "one line",
      "status": "draft|approved|posted", "edited": false }
  ]
}
```

### UI screens (vanilla JS, added to existing single-page app)

- **`08 linkedin ideas`** — mined themes chips + idea cards (angle, `why_valuable`,
  source badge LI/X). Each idea: **Write full post** button → calls `/linkedin/draft`.
  Sidebar "↻ refresh linkedin" → `/linkedin/refresh`.
- **`09 linkedin drafts`** — full-post cards: editable textarea, char count,
  `why_valuable` line. Buttons: **Open in composer** (`/linkedin/compose`),
  **Mark as posted** (`/linkedin/mark-posted`), **Discard**. If `/linkedin/compose`
  returns `pane_hidden`, show the "bring your LinkedIn cmux pane to the front, then
  retry" hint inline.
- **`07 agent`** — add a LinkedIn tab/toggle: edit `linkedin-voice.md`, "re-mine".

## Error handling

- cmux bin missing / not logged in → endpoints return a clear actionable message;
  UI surfaces it (no silent empty states).
- `prefill_composer` pane-hidden → explicit `pane_hidden` reason + hint, not a crash.
- claude call failure/timeout → reuse existing `_claude_json` None-handling; UI
  shows "draft generation failed, retry".
- Merge-preserving writes protect approved/posted drafts across refreshes.
- We NEVER click Post programmatically. `mark-posted` is a manual user confirm.

## Testing

- `linkedin_cli.my_posts()` against live session returns ≥1 post body.
- `linkedin_cli.is_logged_in()` true for current session.
- `prefill_composer` opens + fills the composer when the pane is foregrounded;
  returns `pane_hidden` cleanly when hidden.
- `linkedin.refresh()` emits a schema-valid `data/linkedin_data.json` with themes
  + ideas, every idea carrying `why_valuable`.
- `linkedin.draft()` produces a full post with `why_valuable`; JSON parses.
- Merge-preserving write keeps an `approved` draft after a `refresh()`.
- Server routes return expected shapes (smoke test each new endpoint).

## Out of scope (v1)

LinkedIn feed curation/scoring, comment/reply drafting, LinkedIn-native
scheduling, auto-clicking Post, multi-image upload to LinkedIn.

## Open follow-ups (post-v1)

- Optional: detect "posted" automatically by polling the user's activity page.
- Optional: schedule LinkedIn drafts (needs a foreground-aware queue runner).
- README + SKILL.md updates documenting the LinkedIn workspace.

## Build addendum (2026-06-09, post-implementation)

Findings that emerged during implementation/verification and changed the build:

1. **Repost-dominated activity → `my_posts()` filters reposts.** The user's
   recent LinkedIn activity is almost entirely *reposts* of other people. The
   original CSS-selector scrape both (a) only returned the first match and
   (b) would have captured reposted *third-party* content as if it were the
   user's voice. `my_posts()` now pulls the whole feed via `get html`, splits on
   `Feed post number N`, and **keeps only the user's own authored items** —
   reposts/reactions are dropped. Honest result today: ~0 reachable originals.
2. **Headless infinite-scroll limit.** LinkedIn lazy-loads only a handful of
   activity items when the cmux pane is off-screen (IntersectionObserver
   doesn't fire for hidden webviews). So deep post history isn't headlessly
   reachable; `my_posts()` returns the reachable recent originals, not all.
3. **Voice grounded in X signal + verified material.** Because LinkedIn originals
   are sparse, the `linkedin-voice` agent is anchored on the user's real,
   verified material (projects, stack, honest takes) plus the X interest
   signature — NOT on reposted third-party content. The earlier auto-mined gold
   example (a reposted hiring post) was removed and relabeled as a *format*
   template only.
4. **`read_x_signal()` key fix.** The live `data/dashboard_data.json` uses
   `interest_signature` (top_keywords/accounts/hashtags), not `signature`/`mine`.
   `read_x_signal()` reads the correct keys and also pulls in-voice draft posts.
5. **Single-port guarantee.** `server.py` now reclaims its port on startup
   (kills a prior instance of *this* server holding the port) so re-running
   `run.sh` after a rebuild always takes over the same port — fixing the
   "route not found — older build" stale-server failure mode.
