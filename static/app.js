// xai-personalize-dashboard — front-end

const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));

const els = {
  refresh:        $("#refresh-btn"),
  updated:        $("#updated"),
  serverDot:      $("#server-dot"),
  serverStatus:   $("#server-status"),
  userHandle:     $("#user-handle"),
  composeAs:      $("#compose-as"),

  sigMeta:        $("#sig-meta"),
  sigBmAuth:      $("#sig-bookmark-authors"),
  sigFavAuth:     $("#sig-fav-authors"),
  sigKw:          $("#sig-keywords"),
  sigCounts:      $("#sig-counts"),

  listExplore:    $("#list-explore"),
  listTrending:   $("#list-trending"),
  listPosts:      $("#list-posts"),
  listReplies:    $("#list-replies"),
  listQuotes:     $("#list-quotes"),
  listScheduled:  $("#list-scheduled"),
  listHistory:    $("#list-history"),

  composeCard:    $("#compose-card"),
  composeText:    $("#compose-text"),
  composeImages:  $("#compose-images"),
  composeCount:   $("#compose-count"),

  sectionTitle:   $("#section-title"),
  sectionSub:     $("#section-sub"),

  toast:          $("#toast"),
  overlay:        $("#overlay"),

  schedModal:     $("#schedule-modal"),
  schedWhen:      $("#schedule-when"),
  schedQuick:     $("#schedule-quick"),
  schedPreview:   $("#schedule-preview"),
  schedConfirm:   $("#schedule-confirm"),
  schedCancel:    $("#schedule-cancel"),
  schedClose:     $("#schedule-close"),
};

const SECTION_META = {
  foryou:    { title: "for you",    sub: "curated against your signature" },
  trending:  { title: "trending",   sub: "outside your signature — by engagement" },
  drafts:    { title: "drafts",     sub: "post / reply / quote suggestions" },
  compose:   { title: "compose",    sub: "write your own — image attach + schedule supported" },
  scheduled: { title: "scheduled",  sub: "background queue · cancel any time" },
  history:   { title: "history",    sub: "last 200 posted actions" },
  agent:     { title: "agent",      sub: "edit your voice persona · mine new profiles" },
  blog:      { title: "blog ideas", sub: "medium archive + project repos → topics → variations → draft → comment-driven revision" },
  studio:    { title: "blog studio", sub: "finalized blogs · drafts + titles auto-generated on finalize · pick a title · revise via comments · version history" },
  "linkedin-ideas":  { title: "linkedin ideas",  sub: "your linkedin posts + X signal → valuable post ideas → full drafts" },
  "linkedin-drafts": { title: "linkedin drafts", sub: "edit · save · pre-fill the linkedin composer — you click Post" },
  evals:     { title: "evals", sub: "what the daily eval learned from your kept vs discarded drafts" },
  analytics: { title: "analytics", sub: "what's working on X — by engagement rate, format, timing, keywords" },
};

const PAGE_SIZE = 10;
const INITIAL_PAGES = 5;

const state = {
  data: null,
  scheduled: [],
  history: [],
  composeImages: [],          // [{path, url, name}]
  scheduleContext: null,      // {kind, getText, target_id, image_paths, onDone}
  queuePreview: null,         // {next_fire_at_iso, pending_count, interval_hours}
  // feed pagination — how many items currently shown per feed
  visible: { explore: PAGE_SIZE * INITIAL_PAGES, trending: PAGE_SIZE * INITIAL_PAGES },
};

// queue buttons that should display the next-slot label
const queueButtons = new Set();   // <button data-queue-label>...</button>

function formatQueueTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "+3h";
  const sameDay = d.toDateString() === new Date().toDateString();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return sameDay ? `${hh}:${mm}` : `${d.getMonth()+1}/${d.getDate()} ${hh}:${mm}`;
}

function updateQueueButtons() {
  const iso = state.queuePreview && state.queuePreview.next_fire_at_iso;
  const label = iso ? `queue → ${formatQueueTime(iso)}` : "queue +3h";
  queueButtons.forEach(btn => {
    const span = btn.querySelector("[data-queue-label]");
    if (span) span.textContent = label;
  });
}

async function fetchQueuePreview() {
  try {
    const res = await fetch("/queue/preview", { cache: "no-store" });
    if (!res.ok) return;
    state.queuePreview = await res.json();
    updateQueueButtons();
  } catch { /* offline — leave label as-is */ }
}

// ─────────────────── small helpers ───────────────────

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value") n.value = v;
    else n.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach(c => {
    if (c == null) return;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return n;
}
const chip  = (t) => el("span", { class: "chip" }, t);
const empty = (msg) => el("div", { class: "empty" }, msg);

function toast(msg, kind = "") {
  els.toast.textContent = msg;
  els.toast.className = `toast show ${kind}`;
  setTimeout(() => { els.toast.className = "toast"; }, 3200);
}

function setOverlay(on) { els.overlay.classList.toggle("hidden", !on); }

function tweetUrl(author, id) {
  if (!author || !id) return null;
  return `https://x.com/${String(author).replace(/^@/, "")}/status/${id}`;
}

function relTime(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60)    return "just now";
  if (diff < 3600)  return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return d.toLocaleString();
}

function countdownStr(iso) {
  const target = new Date(iso).getTime();
  const diff = (target - Date.now()) / 1000;
  if (diff <= 0) return "due now";
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = Math.floor(diff % 60);
  if (d > 0) return `T-${d}d ${h.toString().padStart(2,"0")}h`;
  if (h > 0) return `T-${h}h ${m.toString().padStart(2,"0")}m`;
  if (m > 0) return `T-${m}m ${s.toString().padStart(2,"0")}s`;
  return `T-${s}s`;
}

// ─────────────────── server health ───────────────────

let _pingMisses = 0;
async function pingServer({ forceShow = false } = {}) {
  let ok = false;
  try {
    const r = await fetch("/healthz", { cache: "no-store" });
    ok = r.ok;
  } catch { ok = false; }
  if (ok) {
    _pingMisses = 0;
    els.serverDot.classList.remove("offline");
    els.serverStatus.textContent = "online";
    els.serverStatus.className = "ok";
    return true;
  }
  _pingMisses += 1;
  if (_pingMisses >= 2 || forceShow) {
    els.serverDot.classList.add("offline");
    els.serverStatus.textContent = "offline";
    els.serverStatus.className = "offline";
  }
  return false;
}
setInterval(() => pingServer(), 20000);

// ─────────────────── section routing ───────────────────

function showSection(name) {
  $$(".section").forEach(s => s.classList.toggle("hidden", s.id !== `section-${name}`));
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.section === name));
  // keep the active tab visible: open the accordion if it lives behind it
  if (HIDDEN_SECTIONS.has(name)) setHiddenNav(true);
  const meta = SECTION_META[name] || { title: name, sub: "" };
  els.sectionTitle.textContent = meta.title;
  els.sectionSub.textContent = meta.sub;
  if (name === "scheduled") loadScheduled();
  if (name === "history")   loadHistory();
  if (name === "agent")     loadAgent();
  if (name === "blog")      loadBlog();
  if (name === "studio")    loadStudio();
  if (name === "linkedin-ideas")  loadLinkedin();
  if (name === "linkedin-drafts") loadLinkedin();
  if (name === "evals") loadEvals();
  if (name === "analytics") loadAnalytics();
  if (name === "drafts" || name === "compose") fetchQueuePreview();
}

$$(".nav-item").forEach(n => {
  n.addEventListener("click", (e) => {
    e.preventDefault();
    showSection(n.dataset.section);
    history.replaceState(null, "", `#${n.dataset.section}`);
  });
});

// ─────────────────── hidden-tabs accordion ───────────────────
const navHiddenToggle = $("#nav-hidden-toggle");
const navHiddenPanel  = $("#nav-hidden");
// sections that live behind the collapsed "hidden tabs" accordion
const HIDDEN_SECTIONS = new Set(
  navHiddenPanel ? $$(".nav-item", navHiddenPanel).map(n => n.dataset.section) : []
);
function setHiddenNav(open) {
  if (!navHiddenPanel || !navHiddenToggle) return;
  navHiddenPanel.hidden = !open;
  navHiddenToggle.setAttribute("aria-expanded", open ? "true" : "false");
}
navHiddenToggle?.addEventListener("click", () => setHiddenNav(navHiddenPanel.hidden));

// ─────────────────── interest signature ───────────────────

function renderSignature(sig, counts) {
  els.sigBmAuth.innerHTML = "";
  (sig.bookmark_authors || []).slice(0, 10).forEach(a => els.sigBmAuth.appendChild(chip(a)));
  els.sigFavAuth.innerHTML = "";
  (sig.fav_authors || []).slice(0, 8).forEach(a => els.sigFavAuth.appendChild(chip(a)));
  els.sigKw.innerHTML = "";
  (sig.top_keywords || []).slice(0, 14).forEach(k => els.sigKw.appendChild(chip(k)));
  els.sigMeta.textContent = `${sig.sample_size || 0} bookmarks + likes scanned`;
  els.sigCounts.innerHTML = "";
  Object.entries(counts || {}).forEach(([k, v]) => {
    els.sigCounts.appendChild(el("div", {}, [el("span", {}, k), el("span", {}, String(v))]));
  });
}

// ─────────────────── feed cards (for you / trending) ───────────────────

function fmtCount(n) {
  n = Number(n) || 0;
  if (n >= 1_000_000) return `${(n/1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 10_000)    return `${Math.round(n/1000)}K`;
  if (n >= 1_000)     return `${(n/1000).toFixed(1)}K`;
  return String(n);
}

function avatarFor(author) {
  const name = String(author || "").replace(/^@/, "");
  const letter = (name[0] || "?").toUpperCase();
  return el("div", { class: "avatar", title: author || "" }, letter);
}

function makeTweetCard(t, scoreKey) {
  const url = tweetUrl(t.author, t.id);
  const handle = t.author || "@unknown";
  const displayName = handle.replace(/^@/, "");

  const headRight = el("span", { class: "head-right" });
  if (t[scoreKey] != null) headRight.appendChild(el("span", { class: "score-pill" }, `score ${t[scoreKey]}`));

  const headChildren = [
    el("span", { class: "author" }, displayName),
    el("span", { class: "handle" }, handle.startsWith("@") ? handle : `@${handle}`),
  ];
  if (t.time) {
    headChildren.push(el("span", { class: "dot" }, "·"));
    headChildren.push(el("span", { class: "time" }, t.time));
  }
  headChildren.push(headRight);

  const bookmarkBtn = el("span", { class: "act bookmark", title: "bookmark on x.com" }, [
    el("span", { class: "ico" }, "🔖"), el("span", {}, "save"),
  ]);
  bookmarkBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (bookmarkBtn.classList.contains("done") || bookmarkBtn.classList.contains("busy")) return;
    if (!t.id) { toast("missing tweet id", "error"); return; }
    bookmarkBtn.classList.add("busy");
    const lbl = bookmarkBtn.querySelector("span:last-child");
    const prev = lbl.textContent;
    lbl.textContent = "saving…";
    try {
      const res = await fetch("/bookmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: t.id }),
      });
      const data = await res.json();
      if (data.ok) {
        bookmarkBtn.classList.remove("busy");
        bookmarkBtn.classList.add("done");
        lbl.textContent = "saved";
        toast("bookmarked ✓", "ok");
      } else {
        bookmarkBtn.classList.remove("busy");
        lbl.textContent = prev;
        toast(`bookmark failed: ${data.error || "?"}`, "error");
      }
    } catch (err) {
      bookmarkBtn.classList.remove("busy");
      lbl.textContent = prev;
      toast(`error: ${err.message}`, "error");
    }
  });

  const actions = el("div", { class: "actions-row" }, [
    el("span", { class: "act reply", title: "replies" }, [
      el("span", { class: "ico" }, "💬"), el("span", {}, fmtCount(t.replies)),
    ]),
    el("span", { class: "act rt", title: "reposts" }, [
      el("span", { class: "ico" }, "↻"), el("span", {}, fmtCount(t.rts)),
    ]),
    el("span", { class: "act like", title: "likes" }, [
      el("span", { class: "ico" }, "♥"), el("span", {}, fmtCount(t.likes)),
    ]),
    bookmarkBtn,
    el("span", { class: "act open", title: "open on x.com" }, [
      el("span", { class: "ico" }, "↗"), el("span", {}, "open"),
    ]),
  ]);

  const card = el("div", { class: "tweet" }, [
    avatarFor(handle),
    el("div", { class: "body" }, [
      el("div", { class: "head" }, headChildren),
      el("div", { class: "text" }, t.text),
      actions,
    ]),
  ]);

  if (url) {
    const open = (e) => {
      // ignore clicks on inner links
      if (e.target.closest("a")) return;
      window.open(url, "_blank", "noopener,noreferrer");
    };
    card.addEventListener("click", open);
  }
  return card;
}

function renderFeed(list, container, scoreKey = "score", which = "explore") {
  container.innerHTML = "";
  if (!list || !list.length) {
    container.appendChild(empty("nothing here — try refresh."));
    updateLoadMore(which, 0, 0);
    return;
  }
  const total = list.length;
  const shown = Math.min(state.visible[which] || PAGE_SIZE * INITIAL_PAGES, total);
  list.slice(0, shown).forEach(t => container.appendChild(makeTweetCard(t, scoreKey)));
  updateLoadMore(which, shown, total);
}

function updateLoadMore(which, shown, total) {
  const wrap   = document.getElementById(`loadmore-${which}-wrap`);
  const status = document.getElementById(`loadmore-${which}-status`);
  const btn    = document.getElementById(`loadmore-${which}`);
  if (!wrap || !btn || !status) return;
  if (!total) { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");
  const pages = Math.max(1, Math.ceil(shown / PAGE_SIZE));
  status.textContent = `${shown} of ${total} · ${pages} page${pages === 1 ? "" : "s"} loaded`;
  if (shown >= total) {
    btn.disabled = true;
    btn.querySelector("span:last-child").textContent = "all caught up";
  } else {
    btn.disabled = false;
    btn.querySelector("span:last-child").textContent = "load more";
  }
}

function loadMore(which) {
  const list = which === "explore" ? (state.data?.explore || []) : (state.data?.trending || []);
  if (!list.length) return;
  state.visible[which] = Math.min((state.visible[which] || 0) + PAGE_SIZE, list.length);
  const container = which === "explore" ? els.listExplore : els.listTrending;
  const scoreKey  = which === "explore" ? "score" : "trend_score";
  renderFeed(list, container, scoreKey, which);
}

// ─────────────────── draft cards ───────────────────

function makeDraftCard(draft, kind) {
  const wrap = el("div", { class: "draft", "data-id": draft.id });
  let imagePaths = [];   // local state for THIS card
  const originalText = draft.text || "";
  const sendFeedback = (action) => {
    fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        action,
        original_text: originalText,
        final_text: textarea.value,
        target_author: draft.target_author || null,
        target_text: draft.target_text || null,
      }),
    }).catch(() => {});   // best-effort; never block the UI
  };

  if (draft.template) wrap.appendChild(el("span", { class: "template-tag" }, draft.template));

  if (kind !== "post" && draft.target_text) {
    const turl = tweetUrl(draft.target_author, draft.target_id);
    const target = el("div", { class: "target" }, [
      el("span", { class: "target-author" }, (draft.target_author || "@?") + ": "),
      el("span", {}, draft.target_text),
    ]);
    if (turl) {
      target.appendChild(document.createTextNode(" "));
      target.appendChild(el("a", {
        class: "open-link", href: turl, target: "_blank", rel: "noopener noreferrer", title: "open original"
      }, "↗"));
    }
    wrap.appendChild(target);
  }

  const textarea = el("textarea", { rows: "3" });
  textarea.value = draft.text || "";
  wrap.appendChild(textarea);

  const tray = el("div", { class: "image-tray" });
  wrap.appendChild(tray);

  const attachUploadedToDraft = (u) => {
    if (imagePaths.length >= 4) return;
    imagePaths.push(u.path);
    tray.appendChild(renderThumb(u, () => {
      imagePaths = imagePaths.filter(p => p !== u.path);
    }));
  };
  attachClipboardImages(textarea, tray, {
    getCount: () => imagePaths.length,
    max: 4,
    onAttach: attachUploadedToDraft,
  });

  const counter = el("span", { class: "char-count" });
  const updateCount = () => {
    const n = textarea.value.length;
    counter.textContent = `${n} / 280`;
    counter.classList.toggle("over", n > 280);
    counter.classList.toggle("warn", n > 240 && n <= 280);
  };
  textarea.addEventListener("input", updateCount);
  updateCount();

  // ── image upload ──
  const uploadLabel = el("label", { class: "btn ghost upload-btn", title: "attach image (up to 4)" });
  const fileInput = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", multiple: "" , hidden: "" });
  fileInput.style.display = "none";
  uploadLabel.appendChild(fileInput);
  uploadLabel.appendChild(el("span", { class: "btn-key" }, "⊕"));
  uploadLabel.appendChild(el("span", {}, "image"));
  fileInput.addEventListener("change", async (ev) => {
    const files = Array.from(ev.target.files || []);
    if (!files.length) return;
    const uploaded = await uploadFiles(files);
    uploaded.forEach(u => {
      if (imagePaths.length >= 4) return;
      imagePaths.push(u.path);
      tray.appendChild(renderThumb(u, () => {
        imagePaths = imagePaths.filter(p => p !== u.path);
      }));
    });
    fileInput.value = "";
  });

  const postLabel = kind === "reply" ? "post reply" : kind === "quote" ? "post quote" : "post now";
  const postBtn = el("button", { class: "btn ghost", title: "post immediately (skip queue)" }, [
    el("span", { class: "btn-key" }, "↪"),
    el("span", {}, postLabel),
  ]);
  const schedBtn = el("button", { class: "btn ghost", title: "pick a custom time" }, [
    el("span", { class: "btn-key" }, "⏱"),
    el("span", {}, "schedule…"),
  ]);
  const queueBtn = el("button", { class: "btn primary queue-btn", title: "queue 3h after the latest pending item" }, [
    el("span", { class: "btn-key" }, "⏎"),
    el("span", { "data-queue-label": "" }, "queue +3h"),
  ]);
  queueButtons.add(queueBtn);
  const discardBtn = el("button", { class: "btn ghost danger", title: "discard" }, "discard");
  const markBtn = el("button", { class: "btn ghost", title: "I posted this manually elsewhere" }, [
    el("span", { class: "btn-key" }, "✓"),
    el("span", {}, "mark posted"),
  ]);
  const likeBtn = el("button", { class: "btn ghost", title: "good draft — keep as a positive example" }, [
    el("span", { class: "btn-key" }, "♥"),
    el("span", {}, "like"),
  ]);

  const allBtns = [postBtn, schedBtn, queueBtn, discardBtn, markBtn, likeBtn];
  const setDisabled = (v) => allBtns.forEach(b => b.disabled = v);

  const doPost = async () => {
    const text = textarea.value.trim();
    if (!text)            { toast("empty draft", "error"); return; }
    if (text.length > 280){ toast("too long", "error"); return; }
    setDisabled(true);
    postBtn.querySelector("span:last-child").textContent = "posting…";
    try {
      const res = await fetch("/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, text, target_id: draft.target_id || null, image_paths: imagePaths }),
      });
      const data = await res.json();
      if (data.ok) {
        wrap.classList.add("posted");
        textarea.disabled = true;
        sendFeedback("post");
        toast(`${kind} posted ✓`, "ok");
      } else {
        toast(`failed: ${typeof data.result === "string" ? data.result : "see server log"}`, "error");
        setDisabled(false);
        postBtn.querySelector("span:last-child").textContent = postLabel;
      }
    } catch (e) {
      const alive = await pingServer({ forceShow: true });
      toast(alive ? `network error: ${e.message}` : "server offline — run run.sh", "error");
      setDisabled(false);
      postBtn.querySelector("span:last-child").textContent = postLabel;
    }
  };

  const doQueue = async () => {
    const text = textarea.value.trim();
    if (!text)            { toast("empty draft", "error"); return; }
    if (text.length > 280){ toast("too long", "error"); return; }
    setDisabled(true);
    const lbl = queueBtn.querySelector("[data-queue-label]");
    const prev = lbl.textContent;
    lbl.textContent = "queueing…";
    try {
      const res = await fetch("/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, text, target_id: draft.target_id || null, image_paths: imagePaths }),
      });
      const data = await res.json();
      if (data.ok) {
        wrap.classList.add("scheduled-state");
        textarea.disabled = true;
        const t = data.scheduled && data.scheduled.fire_at_iso;
        toast(`queued ✓ → ${t ? new Date(t).toLocaleString() : "+3h"}`, "ok");
        fetchQueuePreview();
        loadScheduled();
      } else {
        toast(`queue failed: ${data.error || "?"}`, "error");
        setDisabled(false);
        lbl.textContent = prev;
      }
    } catch (e) {
      toast(`error: ${e.message}`, "error");
      setDisabled(false);
      lbl.textContent = prev;
    }
  };

  const doSchedule = () => {
    const text = textarea.value.trim();
    if (!text) { toast("empty draft", "error"); return; }
    openSchedule({
      kind, text, target_id: draft.target_id || null, image_paths: imagePaths,
      onDone: () => {
        wrap.classList.add("scheduled-state");
        textarea.disabled = true;
        setDisabled(true);
        fetchQueuePreview();
      },
    });
  };

  postBtn.addEventListener("click", doPost);
  queueBtn.addEventListener("click", doQueue);
  schedBtn.addEventListener("click", doSchedule);
  // Discard and mark-posted both retire the draft: record the eval signal,
  // persist its removal from dashboard_data.json (so it doesn't return on
  // reload/refresh), then drop the card from the UI.
  const retireDraft = async (signal, doneToast) => {
    setDisabled(true);
    sendFeedback(signal);                          // eval signal (best-effort)
    if (!(await removeDraftServerSide(kind, draft.id))) { setDisabled(false); return; }
    queueButtons.delete(queueBtn);
    dropDraftCard(wrap, kind, draft.id);
    toast(doneToast, "ok");
  };
  discardBtn.addEventListener("click", () => retireDraft("discard", "discarded"));
  markBtn.addEventListener("click", () => retireDraft("mark_posted", "marked as posted ✓"));
  likeBtn.addEventListener("click", () => {
    sendFeedback("like");
    likeBtn.classList.add("done");
    toast("saved as a good example ♥", "ok");
  });

  wrap.appendChild(el("div", { class: "actions" }, [uploadLabel, counter, discardBtn, likeBtn, markBtn, postBtn, schedBtn, queueBtn]));
  // ensure label reflects current preview the moment the card mounts
  updateQueueButtons();
  return wrap;
}

function renderThumb(uploaded, onRemove) {
  const t = el("div", { class: "thumb" });
  t.appendChild(el("img", { src: uploaded.url, alt: uploaded.name }));
  const x = el("button", { class: "thumb-x", title: "remove" }, "×");
  x.addEventListener("click", () => {
    onRemove?.();
    t.remove();
  });
  t.appendChild(x);
  return t;
}

async function uploadFiles(files) {
  const fd = new FormData();
  files.forEach(f => fd.append("file", f));
  try {
    const res = await fetch("/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!data.ok) { toast(`upload failed: ${data.error || "?"}`, "error"); return []; }
    return data.files || [];
  } catch (e) {
    toast(`upload error: ${e.message}`, "error");
    return [];
  }
}

// pull image File objects out of a clipboard or drag-drop event
function extractImageFiles(e) {
  const out = [];
  const items = e.clipboardData?.items || e.dataTransfer?.items || [];
  for (const it of items) {
    if (it.kind === "file" && /^image\//.test(it.type)) {
      const f = it.getAsFile();
      if (f) out.push(f);
    }
  }
  // some browsers expose files directly
  const direct = e.clipboardData?.files || e.dataTransfer?.files;
  if (direct && direct.length) {
    for (const f of direct) {
      if (/^image\//.test(f.type) && !out.find(x => x.size === f.size && x.name === f.name)) {
        out.push(f);
      }
    }
  }
  return out;
}

/**
 * Wire paste-image + drop-image upload onto an element (usually a textarea).
 * - getCount() / getMax(): cap check (default max 4)
 * - tray: container the new thumbs are appended into
 * - onAttach(uploaded): receives the uploaded record {path, url, name}
 *   → caller pushes the path into its own state + renders the remove handler
 */
function attachClipboardImages(targetEl, tray, { getCount, max = 4, onAttach }) {
  const handle = async (e) => {
    const files = extractImageFiles(e);
    if (!files.length) return;
    e.preventDefault();
    const room = Math.max(0, max - (getCount?.() ?? 0));
    if (!room) { toast(`max ${max} images`, "error"); return; }
    const toUpload = files.slice(0, room);
    const uploaded = await uploadFiles(toUpload);
    uploaded.forEach(u => onAttach(u));
    if (uploaded.length) toast(`attached ${uploaded.length} image${uploaded.length === 1 ? "" : "s"} ✓`, "ok");
  };
  targetEl.addEventListener("paste", handle);
  targetEl.addEventListener("drop", handle);
  targetEl.addEventListener("dragover", (e) => {
    if ((e.dataTransfer?.items || []).length) e.preventDefault();
  });
}

// kind ("post"/"reply"/"quote") → key inside data.drafts and the count badges
const DRAFT_KIND_KEY = { post: "posts", reply: "replies", quote: "quotes" };

function removeDraftFromState(kind, id) {
  const key = DRAFT_KIND_KEY[kind];
  const drafts = state.data && state.data.drafts;
  if (key && drafts && Array.isArray(drafts[key])) {
    drafts[key] = drafts[key].filter(d => String(d.id) !== String(id));
  }
}

function decrementDraftCount(kind) {
  const dec = (sel) => {
    const node = document.querySelector(`[data-count="${sel}"]`);
    if (node) node.textContent = String(Math.max(0, (parseInt(node.textContent, 10) || 0) - 1));
  };
  dec("drafts");
  if (DRAFT_KIND_KEY[kind]) dec(DRAFT_KIND_KEY[kind]);
}

// Persist a draft removal server-side so it doesn't return on reload/refresh.
async function removeDraftServerSide(kind, id) {
  try {
    const res = await fetch("/draft/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, id }),
    });
    const data = await res.json();
    if (!data.ok) { toast(`failed: ${data.error || "?"}`, "error"); return false; }
    return true;
  } catch (e) {
    toast(`error: ${e.message}`, "error");
    return false;
  }
}

// Remove a draft card from the DOM + in-memory state and fix the counts.
function dropDraftCard(wrap, kind, id) {
  removeDraftFromState(kind, id);
  const container = wrap.parentElement;
  wrap.remove();
  if (container && !container.querySelector(".draft")) {
    container.appendChild(empty(`no ${kind} drafts — refresh to generate.`));
  }
  decrementDraftCount(kind);
}

function renderDrafts(drafts) {
  const sections = [
    { container: els.listPosts,   list: drafts.posts,   kind: "post"  },
    { container: els.listReplies, list: drafts.replies, kind: "reply" },
    { container: els.listQuotes,  list: drafts.quotes,  kind: "quote" },
  ];
  for (const { container, list, kind } of sections) {
    container.innerHTML = "";
    if (!list || !list.length) {
      container.appendChild(empty(`no ${kind} drafts — refresh to generate.`));
      continue;
    }
    list.forEach(d => container.appendChild(makeDraftCard(d, kind)));
  }
}

// ─────────────────── tabs inside drafts ───────────────────

$$(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    $$(".tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    els.listPosts.classList.toggle("hidden", tab !== "posts");
    els.listReplies.classList.toggle("hidden", tab !== "replies");
    els.listQuotes.classList.toggle("hidden", tab !== "quotes");
  });
});

// ─────────────────── compose ───────────────────

function setupCompose() {
  els.composeText.addEventListener("input", () => {
    const n = els.composeText.value.length;
    els.composeCount.textContent = `${n} / 280`;
    els.composeCount.classList.toggle("over", n > 280);
    els.composeCount.classList.toggle("warn", n > 240 && n <= 280);
  });

  const attachUploadedToCompose = (u) => {
    if (state.composeImages.length >= 4) return;
    state.composeImages.push(u);
    els.composeImages.appendChild(renderThumb(u, () => {
      state.composeImages = state.composeImages.filter(x => x.path !== u.path);
    }));
  };

  $("input[data-upload-target=compose]").addEventListener("change", async (ev) => {
    const files = Array.from(ev.target.files || []);
    if (!files.length) return;
    const uploaded = await uploadFiles(files);
    uploaded.forEach(attachUploadedToCompose);
    ev.target.value = "";
  });

  attachClipboardImages(els.composeText, els.composeImages, {
    getCount: () => state.composeImages.length,
    max: 4,
    onAttach: attachUploadedToCompose,
  });

  const clearCompose = () => {
    els.composeText.value = "";
    els.composeCount.textContent = "0 / 280";
    state.composeImages = [];
    els.composeImages.innerHTML = "";
  };

  $("[data-compose-post]").addEventListener("click", async () => {
    const text = els.composeText.value.trim();
    if (!text)             { toast("empty tweet", "error"); return; }
    if (text.length > 280) { toast("too long", "error"); return; }
    try {
      const res = await fetch("/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "post", text, image_paths: state.composeImages.map(i => i.path) }),
      });
      const data = await res.json();
      if (data.ok) {
        toast("posted ✓", "ok");
        clearCompose();
      } else {
        toast(`failed: ${typeof data.result === "string" ? data.result : "?"}`, "error");
      }
    } catch (e) {
      toast(`error: ${e.message}`, "error");
    }
  });

  // register compose queue button so live label updates pick it up
  const composeQueueBtn = $("[data-compose-queue]");
  if (composeQueueBtn) queueButtons.add(composeQueueBtn);

  $("[data-compose-queue]")?.addEventListener("click", async () => {
    const text = els.composeText.value.trim();
    if (!text)             { toast("empty tweet", "error"); return; }
    if (text.length > 280) { toast("too long", "error"); return; }
    const lbl = composeQueueBtn.querySelector("[data-queue-label]");
    const prev = lbl ? lbl.textContent : "";
    if (lbl) lbl.textContent = "queueing…";
    composeQueueBtn.disabled = true;
    try {
      const res = await fetch("/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "post", text, image_paths: state.composeImages.map(i => i.path) }),
      });
      const data = await res.json();
      if (data.ok) {
        const t = data.scheduled && data.scheduled.fire_at_iso;
        toast(`queued ✓ → ${t ? new Date(t).toLocaleString() : "+3h"}`, "ok");
        clearCompose();
        fetchQueuePreview();
        loadScheduled();
      } else {
        toast(`queue failed: ${data.error || "?"}`, "error");
      }
    } catch (e) {
      toast(`error: ${e.message}`, "error");
    } finally {
      composeQueueBtn.disabled = false;
      if (lbl) lbl.textContent = prev;
      updateQueueButtons();
    }
  });

  $("[data-schedule=compose]").addEventListener("click", () => {
    const text = els.composeText.value.trim();
    if (!text) { toast("empty tweet", "error"); return; }
    openSchedule({
      kind: "post", text, target_id: null,
      image_paths: state.composeImages.map(i => i.path),
      onDone: () => {
        els.composeText.value = "";
        els.composeCount.textContent = "0 / 280";
        state.composeImages = [];
        els.composeImages.innerHTML = "";
      },
    });
  });
}

// ─────────────────── schedule modal ───────────────────

function openSchedule(ctx) {
  state.scheduleContext = ctx;
  const now = new Date();
  now.setMinutes(now.getMinutes() + 60);
  els.schedWhen.value = toLocalInputValue(now);
  updateSchedPreview();
  els.schedModal.classList.remove("hidden");
}

function closeSchedule() {
  els.schedModal.classList.add("hidden");
  state.scheduleContext = null;
}

function toLocalInputValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function updateSchedPreview() {
  const v = els.schedWhen.value;
  if (!v) { els.schedPreview.textContent = ""; return; }
  const d = new Date(v);
  if (isNaN(d)) { els.schedPreview.textContent = "invalid time"; return; }
  els.schedPreview.textContent = `fires ${countdownStr(d.toISOString())} → ${d.toLocaleString()}`;
}

els.schedWhen.addEventListener("input", updateSchedPreview);
els.schedClose.addEventListener("click", closeSchedule);
els.schedCancel.addEventListener("click", closeSchedule);

$$("#schedule-quick button").forEach(b => {
  b.addEventListener("click", () => {
    const mins = parseInt(b.dataset.mins, 10);
    const d = new Date(Date.now() + mins * 60_000);
    els.schedWhen.value = toLocalInputValue(d);
    updateSchedPreview();
  });
});

els.schedConfirm.addEventListener("click", async () => {
  const ctx = state.scheduleContext;
  if (!ctx) return;
  const v = els.schedWhen.value;
  if (!v) { toast("pick a time", "error"); return; }
  const d = new Date(v);
  if (isNaN(d) || d.getTime() <= Date.now()) { toast("must be in the future", "error"); return; }
  try {
    const res = await fetch("/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: ctx.kind, text: ctx.text, target_id: ctx.target_id,
        image_paths: ctx.image_paths || [],
        fire_at_iso: d.toISOString(),
      }),
    });
    const data = await res.json();
    if (data.ok) {
      toast(`scheduled ✓ — ${d.toLocaleString()}`, "ok");
      closeSchedule();
      ctx.onDone?.();
      loadScheduled();
      fetchQueuePreview();
    } else {
      toast(`failed: ${data.error || "?"}`, "error");
    }
  } catch (e) {
    toast(`error: ${e.message}`, "error");
  }
});

// ─────────────────── scheduled list ───────────────────

async function loadScheduled() {
  try {
    const res = await fetch("/scheduled");
    const list = await res.json();
    state.scheduled = list || [];
    renderScheduled();
    fetchQueuePreview();
  } catch (e) {
    els.listScheduled.innerHTML = "";
    els.listScheduled.appendChild(empty("could not load scheduled queue."));
  }
}

function bucketLabelFor(iso, nowMs) {
  const t = new Date(iso).getTime();
  if (isNaN(t)) return { key: "unknown", label: "no fire time", order: 9 };
  const diff = t - nowMs; // ms in future
  if (diff <= 60_000)          return { key: "due",   label: "due now",      order: 0 };
  if (diff < 3_600_000)        return { key: "soon",  label: `within the hour`, order: 1 };

  const fire  = new Date(t);
  const today = new Date(nowMs);
  const sameDay = fire.toDateString() === today.toDateString();
  const tom   = new Date(today); tom.setDate(tom.getDate() + 1);
  const sameDayTom = fire.toDateString() === tom.toDateString();

  const time = fire.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  if (sameDay)    return { key: "today",    label: `later today`,             order: 2 };
  if (sameDayTom) return { key: "tomorrow", label: `tomorrow · ${time}`,      order: 3 };

  const days = Math.round((new Date(fire.toDateString()) - new Date(today.toDateString())) / 86_400_000);
  if (days < 7) {
    const day = fire.toLocaleDateString([], { weekday: "long" });
    return { key: "week", label: `${day} · ${time}`, order: 4 };
  }
  return {
    key: "later",
    label: fire.toLocaleDateString([], { month: "short", day: "numeric" }) + " · " + time,
    order: 5,
  };
}

function renderScheduled() {
  els.listScheduled.innerHTML = "";
  const list = state.scheduled;
  if (!list || !list.length) {
    els.listScheduled.appendChild(empty("nothing scheduled."));
    updateNavCount("scheduled", 0);
    return;
  }

  const pending = list.filter(i => i.status === "pending").sort((a,b) =>
    new Date(a.fire_at_iso) - new Date(b.fire_at_iso));
  const archived = list.filter(i => i.status !== "pending").sort((a,b) =>
    new Date(b.created_at) - new Date(a.created_at));

  const timeline = el("div", { class: "timeline" });
  const nowMs = Date.now();

  // group pending by bucket
  const groups = new Map();
  for (const item of pending) {
    const b = bucketLabelFor(item.fire_at_iso, nowMs);
    if (!groups.has(b.key)) groups.set(b.key, { ...b, items: [] });
    groups.get(b.key).items.push(item);
  }
  const orderedGroups = [...groups.values()].sort((a, b) => a.order - b.order);

  if (!orderedGroups.length && !archived.length) {
    els.listScheduled.appendChild(empty("nothing scheduled."));
    updateNavCount("scheduled", 0);
    return;
  }

  for (const g of orderedGroups) {
    timeline.appendChild(el("div", { class: `timeline-group-head ${g.key}` }, [
      el("span", { class: "tl-marker" }, "▸"),
      el("span", { class: "tl-label" }, g.label),
      el("span", { class: "tl-count" }, String(g.items.length)),
    ]));
    g.items.forEach(item => timeline.appendChild(makeTimelineItem(item, "pending")));
  }

  if (archived.length) {
    timeline.appendChild(el("div", { class: "timeline-group-head archived" }, [
      el("span", { class: "tl-marker" }, "—"),
      el("span", { class: "tl-label" }, "archived"),
      el("span", { class: "tl-count" }, String(archived.length)),
    ]));
    archived.forEach(item => timeline.appendChild(makeTimelineItem(item, item.status)));
  }

  els.listScheduled.appendChild(timeline);
  updateNavCount("scheduled", pending.length);
}

function makeTimelineItem(item, state_) {
  const node = el("div", { class: `tl-item ${state_}`, "data-sched-id": item.id });

  const dot = el("span", { class: "tl-dot" });
  if (state_ === "pending") dot.classList.add("pulse");

  const headLeft = el("div", { class: "tl-head-left" }, [
    el("span", { class: `kind-pill ${item.kind}` }, item.kind),
    el("span", {
      class: `tl-when ${item.status === "pending" ? "" : item.status}`,
      "data-countdown": item.fire_at_iso,
      "data-status": item.status,
    }, item.status === "pending" ? countdownStr(item.fire_at_iso) : `▸ ${item.status}`),
  ]);

  const headRight = el("div", { class: "tl-head-right" }, [
    el("span", { class: "tl-when-abs" }, item.fire_at_iso ? new Date(item.fire_at_iso).toLocaleString([], {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    }) : ""),
  ]);

  if (state_ === "pending") {
    const cancelBtn = el("button", { class: "btn ghost danger tl-cancel" }, "cancel");
    cancelBtn.addEventListener("click", async () => {
      cancelBtn.disabled = true;
      try {
        const res = await fetch(`/scheduled/${item.id}`, { method: "DELETE" });
        const data = await res.json();
        if (data.ok) { toast("cancelled", "ok"); loadScheduled(); fetchQueuePreview(); }
        else         { toast(`failed: ${data.error || "?"}`, "error"); cancelBtn.disabled = false; }
      } catch (e) { toast(e.message, "error"); cancelBtn.disabled = false; }
    });
    headRight.appendChild(cancelBtn);
  }

  const head = el("div", { class: "tl-item-head" }, [headLeft, headRight]);
  const text = el("div", { class: "tl-text" }, item.text);

  const subMeta = [];
  subMeta.push(`created ${relTime(item.created_at)}`);
  if (item.target_id) subMeta.push(`↳ target ${item.target_id.slice(-8)}`);
  if (item.image_paths && item.image_paths.length) subMeta.push(`📷 ${item.image_paths.length}`);
  if (item.error) subMeta.push(`⚠ ${item.error}`);
  const sub = el("div", { class: `tl-sub ${item.error ? "has-error" : ""}` }, subMeta.join(" · "));

  const body = el("div", { class: "tl-body" }, [head, text, sub]);

  node.appendChild(dot);
  node.appendChild(body);
  return node;
}

// live countdown — tick every second
setInterval(() => {
  $$(".tl-when[data-status=pending], .tl-when[data-status='']").forEach(el => {
    const iso = el.dataset.countdown;
    if (iso) el.textContent = countdownStr(iso);
  });
}, 1000);

// ─────────────────── history ───────────────────

async function loadHistory() {
  try {
    const res = await fetch("/history");
    const list = await res.json();
    state.history = list || [];
    renderHistory();
  } catch {
    els.listHistory.innerHTML = "";
    els.listHistory.appendChild(empty("could not load history."));
  }
}

async function loadEvals() {
  const summaryEl = $("#evals-summary");
  const stateEl = $("#evals-state");
  const runsEl = $("#evals-runs");
  summaryEl.textContent = "loading…";
  let data;
  try {
    data = await (await fetch("/evals")).json();
  } catch (e) {
    summaryEl.textContent = "failed to load evals.";
    return;
  }
  const s = data.summary || { good: 0, bad: 0, by_kind: {}, since_last: 0 };

  summaryEl.innerHTML = "";
  summaryEl.append(
    el("div", { class: "eval-stat good" }, [el("b", {}, String(s.good)), el("span", {}, "kept / good")]),
    el("div", { class: "eval-stat bad" }, [el("b", {}, String(s.bad)), el("span", {}, "discarded")]),
    el("div", { class: "eval-stat" }, [el("b", {}, String(s.since_last)), el("span", {}, "new since last eval")]),
  );

  // current learned state
  const st = data.state || { gold: [], anti: [], rules: [] };
  stateEl.innerHTML = "";
  const stateBlock = (title, items, cls) => {
    if (!items || !items.length) return;
    stateEl.appendChild(el("h4", { class: "evals-h" }, title));
    stateEl.appendChild(el("ul", { class: (`eval-list ${cls}`).trim() },
      items.map(i => el("li", {}, i))));
  };
  stateBlock("currently rewarding (gold)", st.gold, "good");
  stateBlock("currently avoiding (anti)", st.anti, "bad");
  stateBlock("extra rules", st.rules, "");
  if (!st.gold.length && !st.anti.length && !st.rules.length) {
    stateEl.appendChild(empty("nothing learned yet — discard/keep some drafts, then run an eval."));
  }

  // run history (already newest-first from the server)
  runsEl.innerHTML = "";
  const runs = data.runs || [];
  if (!runs.length) {
    runsEl.appendChild(empty("no eval runs yet."));
  } else {
    runs.forEach(r => {
      const card = el("div", { class: "eval-run" + (r.reverted ? " reverted" : "") });
      card.appendChild(el("div", { class: "eval-run-head" }, [
        el("span", {}, relTime(r.ts)),
        el("span", { class: "eval-run-counts" },
          `${r.counts?.good ?? 0} good · ${r.counts?.bad ?? 0} bad`),
      ]));
      card.appendChild(el("p", { class: "eval-conclusion" }, r.conclusion || "(no conclusion)"));
      const added = r.added || {};
      const addedBlock = (label, items) => {
        if (!items || !items.length) return;
        card.appendChild(el("div", { class: "eval-added" }, [
          el("span", { class: "eval-added-label" }, label),
          el("ul", {}, items.map(i => el("li", {}, i))),
        ]));
      };
      addedBlock("+ gold", added.gold);
      addedBlock("+ anti", added.anti);
      addedBlock("+ rules", added.rules);
      if (r.reverted) {
        card.appendChild(el("span", { class: "eval-reverted-tag" }, "reverted"));
      } else {
        const rev = el("button", { class: "btn ghost danger" }, "revert this run");
        rev.addEventListener("click", async () => {
          rev.disabled = true;
          const res = await (await fetch("/evals/revert", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: r.id }),
          })).json();
          if (res.ok) { toast("reverted ✓", "ok"); loadEvals(); }
          else { toast(`revert failed: ${res.error || "?"}`, "error"); rev.disabled = false; }
        });
        card.appendChild(rev);
      }
      runsEl.appendChild(card);
    });
  }
}

// ─────────────────── analytics (13) ───────────────────

// ─────────────────── analytics screen ───────────────────
const AN_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const anPct = v => (v * 100).toFixed(1) + "%";
const anNum = n => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(Math.round(n || 0));

function anHeader(title, note) {
  return el("div", { class: "an-h" }, [
    el("span", { class: "an-h-mark" }, "//"),
    el("span", { class: "an-h-title" }, title),
    note ? el("span", { class: "an-h-note" }, note) : null,
  ]);
}

function anSection(title, note, content) {
  return el("div", { class: "an-sec" }, [anHeader(title, note), content]);
}

function anStat(num, label) {
  return el("div", { class: "an-stat" }, [
    el("div", { class: "an-stat-num" }, num),
    el("div", { class: "an-stat-label" }, label),
  ]);
}

function anThemeCol(label, items, tone) {
  const tags = (items && items.length ? items : ["nothing clear yet"])
    .map(t => el("span", { class: "an-tag " + tone }, t));
  return el("div", { class: "an-theme-col" }, [
    el("div", { class: "an-theme-h" }, [el("span", { class: "an-dot " + tone }), el("span", {}, label)]),
    el("div", { class: "an-theme-tags" }, tags),
  ]);
}

function anCallout(tag, text) {
  return el("div", { class: "an-callout" }, [
    el("span", { class: "an-callout-tag" }, tag),
    el("span", { class: "an-callout-text" }, text),
  ]);
}

// vertical bar chart (timing) — bar height ∝ engagement rate, best bucket highlighted
function anVBars(title, obj, opts = {}) {
  const entries = Object.entries(obj || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  const max = Math.max(0.0001, ...entries.map(([, v]) => v.avg_eng_rate));
  const chart = el("div", { class: "an-vchart" }, entries.map(([k, v]) => {
    const h = Math.max(3, Math.round(v.avg_eng_rate / max * 100));
    const tip = `${opts.tip ? opts.tip(k) : k} · ${anPct(v.avg_eng_rate)} eng · ${anNum(v.avg_views)} views · n=${v.count}`;
    return el("div", { class: "an-vcol" + (v.avg_eng_rate === max ? " best" : ""), title: tip }, [
      el("div", { class: "an-vbar-track" }, [el("div", { class: "an-vbar-fill", style: `height:${h}%` })]),
      el("div", { class: "an-vcol-label" }, opts.axis ? opts.axis(k) : k),
    ]);
  }));
  return el("div", { class: "an-panel" }, [el("div", { class: "an-fmt-h" }, title), chart]);
}

// horizontal comparison bars within a small format panel
function anHBars(title, obj, order) {
  let entries = Object.entries(obj || {});
  if (order) entries.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  else entries.sort((a, b) => b[1].avg_eng_rate - a[1].avg_eng_rate);
  const max = Math.max(0.0001, ...entries.map(([, v]) => v.avg_eng_rate));
  const rows = entries.map(([k, v]) => {
    const w = Math.max(6, Math.round(v.avg_eng_rate / max * 100));
    return el("div", { class: "an-hrow" }, [
      el("span", { class: "an-hrow-label" }, k.replace(/_/g, " ")),
      el("div", { class: "an-hbar-track" }, [el("div", { class: "an-hbar-fill", style: `width:${w}%` })]),
      el("span", { class: "an-hrow-val" }, anPct(v.avg_eng_rate)),
      el("span", { class: "an-hrow-n" }, "n=" + v.count),
    ]);
  });
  return el("div", { class: "an-panel an-fmt" }, [el("div", { class: "an-fmt-h" }, title), ...rows]);
}

function anPostCard(c, tone) {
  return el("a", {
    class: "an-post", href: `https://x.com/i/status/${c.id}`, target: "_blank", rel: "noopener noreferrer",
  }, [
    el("div", { class: "an-post-top" }, [
      el("span", { class: "an-post-eng " + tone }, anPct(c.eng_rate)),
      el("span", { class: "an-post-kind" }, c.kind),
      el("span", { class: "an-post-views" }, anNum(c.views) + " views"),
      el("span", { class: "an-post-open" }, "↗"),
    ]),
    el("div", { class: "an-post-text" }, c.text || "(no text)"),
  ]);
}

function anPostCol(label, arr, tone) {
  const cards = (arr && arr.length ? arr : []).map(c => anPostCard(c, tone));
  return el("div", { class: "an-postcol" }, [
    el("div", { class: "an-fmt-h" }, label),
    ...(cards.length ? cards : [el("div", { class: "an-post-empty" }, "not enough reach to rank yet")]),
  ]);
}

async function loadAnalytics() {
  const meta = $("#analytics-meta");
  const body = $("#analytics-body");
  body.innerHTML = "";
  body.appendChild(el("div", { class: "an-loading" }, "reading your signal…"));

  let d;
  try { d = await (await fetch("/analytics")).json(); }
  catch { body.innerHTML = ""; body.appendChild(el("div", { class: "an-empty" }, "failed to load analytics.")); return; }

  if (!d || !d.generated_at) {
    meta.textContent = "";
    body.innerHTML = "";
    body.appendChild(el("div", { class: "an-empty" }, [
      el("div", { class: "an-empty-big" }, "no analysis yet"),
      el("div", {}, "hit run analysis now — it deepens with each daily run."),
    ]));
    return;
  }

  meta.innerHTML = "";
  meta.append(
    el("b", {}, String(d.n_posts)), el("span", {}, "posts"),
    el("i", {}, "·"), el("b", {}, d.window_days + "d"), el("span", {}, "window"),
    el("i", {}, "·"), el("span", {}, "updated " + relTime(d.generated_at)),
    el("i", {}, "·"), el("span", { class: "an-meta-note" }, "engagement rate · replies excluded"),
  );

  body.innerHTML = "";
  const ov = d.overall || {};

  // ── glance strip ──
  body.appendChild(el("div", { class: "an-stats" }, [
    anStat(anPct(ov.avg_eng_rate || 0), "avg engagement"),
    anStat(anNum(ov.avg_views || 0), "avg reach"),
    anStat(String(d.n_posts), "posts analyzed"),
    anStat(String((d.keywords || []).length), "signal keywords"),
  ]));

  // ── what's working (LLM read) ──
  const ins = d.insights;
  if (ins) {
    const panel = el("div", { class: "an-insights" });
    panel.appendChild(el("div", { class: "an-themes" }, [
      anThemeCol("resonating", ins.themes_working, "good"),
      anThemeCol("falling flat", ins.themes_flat, "bad"),
    ]));
    const calls = [];
    if (ins.timing_insight) calls.push(anCallout("timing", ins.timing_insight));
    if (ins.format_insight) calls.push(anCallout("format", ins.format_insight));
    if (calls.length) panel.appendChild(el("div", { class: "an-callouts" }, calls));
    if (ins.recommendations?.length) {
      panel.appendChild(el("div", { class: "an-recs-label" }, "do next"));
      panel.appendChild(el("ol", { class: "an-recs" },
        ins.recommendations.map(r => el("li", { class: "an-rec" }, r))));
    }
    body.appendChild(anSection("what's working", "model read on your last " + d.n_posts + " posts", panel));
  }

  // ── best times ──
  const times = el("div", { class: "an-times" }, [
    anVBars("by hour", d.breakdowns.hour, {
      axis: k => Number(k) % 3 === 0 ? String(k).padStart(2, "0") : "",
      tip: k => String(k).padStart(2, "0") + ":00",
    }),
    anVBars("by weekday", d.breakdowns.weekday, { axis: k => AN_WD[k] || k, tip: k => AN_WD[k] || k }),
  ]);
  body.appendChild(anSection("best times", "engagement by when you post · local time", times));

  // ── format ──
  const fmt = el("div", { class: "an-fmt-grid" }, [
    anHBars("post type", d.breakdowns.type),
    anHBars("media", d.breakdowns.media),
    anHBars("links", d.breakdowns.link),
    anHBars("length", d.breakdowns.length, ["short", "medium", "long"]),
  ]);
  body.appendChild(anSection("format", "what shape of post lands", fmt));

  // ── keywords ──
  if (d.keywords?.length) {
    const keys = el("div", { class: "an-keys" }, d.keywords.slice(0, 18).map(k => {
      const cls = k.lift >= 1.2 ? " strong" : k.lift < 1 ? " weak" : "";
      return el("span", { class: "an-key" + cls, title: `${anPct(k.avg_eng_rate)} eng · n=${k.support}` }, [
        el("span", { class: "an-key-tok" }, k.token),
        el("span", { class: "an-key-lift" }, k.lift + "×"),
      ]);
    }));
    body.appendChild(anSection("keywords", "avg engagement lift vs your baseline", el("div", { class: "an-panel" }, keys)));
  }

  // ── top / bottom posts ──
  body.appendChild(anSection("top & bottom posts", "ranked by engagement rate (min reach applied)",
    el("div", { class: "an-posts-grid" }, [
      anPostCol("top performers", d.top, "good"),
      anPostCol("lowest performers", d.bottom, "bad"),
    ])));
}

function renderHistory() {
  els.listHistory.innerHTML = "";
  const list = state.history;
  if (!list || !list.length) {
    els.listHistory.appendChild(empty("nothing posted yet."));
    updateNavCount("history", 0);
    return;
  }
  list.forEach(h => {
    const card = el("div", { class: "hist-card" }, [
      el("div", {}, [el("span", { class: "kind-pill" }, h.kind)]),
      el("div", {}, [
        el("div", {}, h.text),
        el("div", { class: "meta" }, [
          el("span", {}, `${relTime(h.posted_at)} · via ${h.source}`),
          h.tweet_url ? el("a", { href: h.tweet_url, target: "_blank", rel: "noopener noreferrer" }, " ↗ open") : null,
        ]),
      ]),
      el("div", {}),
    ]);
    els.listHistory.appendChild(card);
  });
  updateNavCount("history", list.length);
}

// ─────────────────── nav counts ───────────────────

function updateNavCount(key, n) {
  const node = document.querySelector(`[data-count="${key}"]`);
  if (node) node.textContent = String(n);
}

// ─────────────────── main render ───────────────────

function render(data) {
  state.data = data;
  if (data.empty) {
    [els.listExplore, els.listTrending, els.listPosts, els.listReplies, els.listQuotes].forEach(c => {
      c.innerHTML = ""; c.appendChild(empty(data.message || "no data — refresh."));
    });
    document.getElementById("loadmore-explore-wrap")?.classList.add("hidden");
    document.getElementById("loadmore-trending-wrap")?.classList.add("hidden");
    els.updated.textContent = "—";
    return;
  }
  if (data.user) {
    els.userHandle.textContent = data.user;
    els.composeAs.textContent = data.user;
  }
  renderSignature(data.interest_signature || {}, data.counts);
  renderFeed(data.explore  || [], els.listExplore,  "score",       "explore");
  renderFeed(data.trending || [], els.listTrending, "trend_score", "trending");
  renderDrafts(data.drafts || {});

  const drafts = data.drafts || {};
  updateNavCount("foryou",   (data.explore  || []).length);
  updateNavCount("trending", (data.trending || []).length);
  updateNavCount("drafts",   (drafts.posts || []).length + (drafts.replies || []).length + (drafts.quotes || []).length);
  updateNavCount("posts",    (drafts.posts   || []).length);
  updateNavCount("replies",  (drafts.replies || []).length);
  updateNavCount("quotes",   (drafts.quotes  || []).length);

  els.updated.textContent = data.generated_at
    ? `${relTime(data.generated_at)}${data.elapsed_seconds ? ` (${data.elapsed_seconds}s)` : ""}`
    : "never";
}

async function load() {
  try {
    const res = await fetch("/data");
    const data = await res.json();
    render(data);
  } catch (e) {
    toast(`load failed: ${e.message}`, "error");
  }
  loadScheduled();
  loadHistory();
  fetchQueuePreview();
}

async function refresh() {
  els.refresh.disabled = true;
  setOverlay(true);
  try {
    const res = await fetch("/refresh", { method: "POST" });
    const data = await res.json();
    if (data.ok === false) { toast("refresh failed — see terminal", "error"); console.error(data); }
    else                   { render(data); toast("refreshed ✓", "ok"); }
  } catch (e) {
    const alive = await pingServer({ forceShow: true });
    toast(alive ? `refresh error: ${e.message}` : "server offline — run run.sh", "error");
  } finally {
    setOverlay(false);
    els.refresh.disabled = false;
  }
  loadScheduled();
  loadHistory();
}

els.refresh.addEventListener("click", refresh);

document.getElementById("loadmore-explore")?.addEventListener("click", () => loadMore("explore"));
document.getElementById("loadmore-trending")?.addEventListener("click", () => loadMore("trending"));

// ─────────────────── agent (07) ───────────────────

const agentEls = {
  textarea: $("#agent-textarea"),
  save:     $("#agent-save"),
  discard:  $("#agent-discard"),
  bytes:    $("#agent-bytes"),
  mtime:    $("#agent-mtime"),
  path:     $("#agent-path"),
  dirty:    $("#agent-dirty"),
  profiles: $("#agent-profiles"),
  studyInp: $("#study-handle"),
  studyGo:  $("#study-go"),
  studyLog: $("#study-log"),
  studyRes: $("#study-result"),
};

const agentState = { saved: "", mtime: "", path: "", loading: false };

function setAgentDirty(dirty) {
  agentEls.dirty.classList.toggle("hidden", !dirty);
  agentEls.save.disabled = !dirty;
}

function renderAgentProfiles(profiles) {
  agentEls.profiles.innerHTML = "";
  if (!profiles || !profiles.length) {
    agentEls.profiles.appendChild(empty("no profiles parsed — check VOICE NEIGHBORHOOD section."));
    return;
  }
  profiles.forEach(p => {
    const handleSans = p.handle.replace(/^@/, "");
    const card = el("div", { class: "profile-card" }, [
      el("div", { class: "profile-head" }, [
        el("span", { class: "profile-handle" }, p.handle),
        el("a", { class: "open-link", href: p.url, target: "_blank", rel: "noopener noreferrer", title: "open on x.com" }, "↗"),
      ]),
      el("div", { class: "profile-note" }, p.note || ""),
      el("div", { class: "profile-actions" }, [
        (() => {
          const b = el("button", { class: "btn ghost", title: "re-mine this profile and merge fresh patterns" }, [
            el("span", { class: "btn-key" }, "↻"), el("span", {}, "re-study"),
          ]);
          b.addEventListener("click", () => runStudy(handleSans, b));
          return b;
        })(),
      ]),
    ]);
    agentEls.profiles.appendChild(card);
  });
}

async function loadAgent() {
  if (agentState.loading) return;
  agentState.loading = true;
  try {
    const res = await fetch("/agent", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) { toast(`agent load failed: ${data.error || res.status}`, "error"); return; }
    agentState.saved = data.content || "";
    agentState.mtime = data.mtime || "";
    agentState.path  = data.path  || "";
    agentEls.textarea.value = agentState.saved;
    agentEls.path.textContent = data.path || "";
    agentEls.mtime.textContent = data.mtime ? relTime(data.mtime) : "—";
    agentEls.bytes.textContent = `${agentEls.textarea.value.length} bytes`;
    renderAgentProfiles(data.profiles || []);
    setAgentDirty(false);
  } catch (e) {
    toast(`agent load error: ${e.message}`, "error");
  } finally {
    agentState.loading = false;
  }
}

function setupAgent() {
  agentEls.textarea.addEventListener("input", () => {
    const v = agentEls.textarea.value;
    agentEls.bytes.textContent = `${v.length} bytes`;
    setAgentDirty(v !== agentState.saved);
  });

  agentEls.discard.addEventListener("click", () => {
    if (agentEls.textarea.value === agentState.saved) return;
    agentEls.textarea.value = agentState.saved;
    agentEls.bytes.textContent = `${agentState.saved.length} bytes`;
    setAgentDirty(false);
    toast("changes discarded", "");
  });

  agentEls.save.addEventListener("click", async () => {
    const content = agentEls.textarea.value;
    if (content === agentState.saved) return;
    agentEls.save.disabled = true;
    const lbl = agentEls.save.querySelector("span:last-child");
    const prev = lbl.textContent; lbl.textContent = "saving…";
    try {
      const res = await fetch("/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await res.json();
      if (data.ok) {
        agentState.saved = content;
        agentState.mtime = data.mtime || agentState.mtime;
        agentEls.mtime.textContent = data.mtime ? relTime(data.mtime) : agentEls.mtime.textContent;
        setAgentDirty(false);
        toast("saved ✓", "ok");
        // re-parse profiles from new content via reload
        loadAgent();
      } else {
        toast(`save failed: ${data.error || "?"}`, "error");
        agentEls.save.disabled = false;
      }
    } catch (e) {
      toast(`save error: ${e.message}`, "error");
      agentEls.save.disabled = false;
    } finally {
      lbl.textContent = prev;
    }
  });

  agentEls.studyGo.addEventListener("click", () => {
    const h = (agentEls.studyInp.value || "").trim().replace(/^@/, "");
    if (!h) { toast("enter a handle", "error"); return; }
    runStudy(h, agentEls.studyGo);
  });
  agentEls.studyInp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") agentEls.studyGo.click();
  });

  // ── agent screen tab toggle: x voice ⇄ linkedin voice ──
  $$("#agent-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.agentTab;
      $$("#agent-tabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
      $("#agent-pane-x")?.classList.toggle("hidden", tab !== "x");
      $("#agent-pane-linkedin")?.classList.toggle("hidden", tab !== "linkedin");
      if (tab === "linkedin") loadLinkedinAgent();
    });
  });
}

async function runStudy(handle, triggerBtn) {
  const log = agentEls.studyLog;
  const result = agentEls.studyRes;
  log.classList.remove("hidden");
  result.classList.add("hidden");
  result.innerHTML = "";
  log.textContent = `▸ studying @${handle}\n  fetching ~50 posts via twitter CLI…\n  → calling claude for voice analysis…\n  → merging into the agent .md…\n  (this can take 30–90s — do not refresh)\n`;
  setOverlay(true);
  if (triggerBtn) triggerBtn.disabled = true;
  try {
    const res = await fetch("/agent/study", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: handle }),
    });
    const data = await res.json();
    if (!data.ok) {
      log.textContent += `\n✗ failed: ${data.error || res.status}`;
      if (data.detail) log.textContent += `\n  detail: ${data.detail}`;
      if (data.raw)    log.textContent += `\n  raw claude output:\n${data.raw}`;
      toast(`study failed: ${data.error || "?"}`, "error");
      return;
    }
    const diff = (data.diff_summary || []).map(l => `  + ${l}`).join("\n") || "  (no changes — already merged?)";
    log.textContent += `\n✓ merged @${handle}\n${diff}`;
    const a = data.analysis || {};
    result.classList.remove("hidden");
    result.innerHTML = "";
    result.appendChild(el("div", { class: "study-result-head" }, `▸ analysis · @${handle}`));
    if (a.summary)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "summary"), el("span", { class: "v" }, a.summary)]));
    if (a.rhythm)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "rhythm"), el("span", { class: "v" }, a.rhythm)]));
    if (a.openers && a.openers.length)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "openers"), el("span", { class: "v" }, a.openers.join(" · "))]));
    if (a.vocab_additions && a.vocab_additions.length)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "vocab+"), el("span", { class: "v" }, a.vocab_additions.join(", "))]));
    if (a.emoji_additions && a.emoji_additions.length)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "emoji+"), el("span", { class: "v" }, a.emoji_additions.join(" "))]));
    if (a.distinctive)
      result.appendChild(el("div", { class: "study-result-row" }, [el("span", { class: "k" }, "distinctive"), el("span", { class: "v" }, a.distinctive)]));
    toast(`merged into the agent .md ✓`, "ok");
    agentEls.studyInp.value = "";
    loadAgent();
  } catch (e) {
    log.textContent += `\n✗ network error: ${e.message}`;
    toast(`error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    if (triggerBtn) triggerBtn.disabled = false;
  }
}

// ─────────────────── blog ideas (08) ───────────────────

async function blogJson(res) {
  // tolerant of non-JSON (old server returning HTML 404 for new routes)
  const ct = (res.headers.get("Content-Type") || "").toLowerCase();
  if (!ct.includes("json")) {
    if (res.status === 404) {
      throw new Error("route not found — your server is running an older build. restart it: kill the python process on :7873 and re-run run.sh");
    }
    const head = (await res.text()).slice(0, 120);
    throw new Error(`unexpected ${res.status} response (${ct || "no content-type"}): ${head}`);
  }
  try {
    return await res.json();
  } catch (e) {
    const inner = String(e && e.message || e).replace(/^(malformed JSON from server: )+/i, "");
    throw new Error(`malformed JSON from server: ${inner.slice(0, 200)}`);
  }
}


const blogEls = {
  scrape:      $("#blog-scrape"),
  generate:    $("#blog-generate"),
  research:    $("#blog-research"),
  clear:       $("#blog-clear"),
  editAgent:   $("#blog-edit-agent"),
  ideaGrid:    $("#idea-grid"),
  emptyState:  $("#blog-empty"),
  workspaceWrap: $("#blog-workspace-wrap"),
  navCount:    document.querySelector('[data-count="blog"]'),
  projects:    $("#projects-list"),
  projectPath: $("#project-path"),
  projectName: $("#project-name"),
  projectAdd:  $("#project-add-btn"),
  archiveList: $("#archive-list"),
  archiveCount: $("#archive-count"),

  agentText:    $("#blog-agent-textarea"),
  agentSave:    $("#blog-agent-save"),
  agentDiscard: $("#blog-agent-discard"),
  agentBytes:   $("#blog-agent-bytes"),
  agentMtime:   $("#blog-agent-mtime"),
  agentPath:    $("#blog-agent-path"),
  agentDirty:   $("#blog-agent-dirty"),
  agentBlock:   $("#blog-agent-block"),
};

const blogState = {
  ideas:        [],
  projects:     [],
  drafts:       [],
  medium_seen:  [],
  finalizedId:  null,
  activeTab:    "variations",  // variations | draft
  agentSaved:   "",
  agentMtime:   "",
  agentPath:    "",
  loading:      false,
};

function blogIdeaById(id)  { return blogState.ideas.find(i => i.id === id); }
function blogDraftForIdea(id) { return blogState.drafts.find(d => d.idea_id === id); }

function setBlogAgentDirty(dirty) {
  blogEls.agentDirty.classList.toggle("hidden", !dirty);
  blogEls.agentSave.disabled = !dirty;
}

async function loadBlog() {
  if (blogState.loading) return;
  blogState.loading = true;
  try {
    const [stateRes, agentRes] = await Promise.all([
      fetch("/blog/data", { cache: "no-store" }),
      fetch("/blog-agent", { cache: "no-store" }),
    ]);
    const state = await stateRes.json();
    blogState.ideas       = state.ideas    || [];
    blogState.projects    = state.projects || [];
    blogState.drafts      = state.drafts   || [];
    blogState.medium_seen = state.medium_seen || [];
    // pick previously-finalized idea if any, else first finalized, else none
    if (!blogState.finalizedId || !blogIdeaById(blogState.finalizedId)) {
      const fin = blogState.ideas.find(i => i.finalized);
      blogState.finalizedId = fin ? fin.id : null;
    }

    const agentData = await agentRes.json();
    blogState.agentSaved = agentData.content || "";
    blogState.agentMtime = agentData.mtime || "";
    blogState.agentPath  = agentData.path  || "";
    blogEls.agentText.value = blogState.agentSaved;
    blogEls.agentPath.textContent = agentData.path || "—";
    blogEls.agentMtime.textContent = agentData.mtime ? relTime(agentData.mtime) : "—";
    blogEls.agentBytes.textContent = `${blogEls.agentText.value.length} bytes`;
    setBlogAgentDirty(false);

    renderIdeas();
    renderProjects();
    renderArchive();
    renderWorkspace();
    renderStudio();
    if (blogEls.navCount) blogEls.navCount.textContent = String(blogState.ideas.length || "—");
  } catch (e) {
    toast(`blog load failed: ${e.message}`, "error");
  } finally {
    blogState.loading = false;
  }
}

function renderIdeas() {
  blogEls.ideaGrid.innerHTML = "";
  if (!blogState.ideas.length) {
    blogEls.emptyState.classList.remove("hidden");
    return;
  }
  blogEls.emptyState.classList.add("hidden");

  blogState.ideas.forEach(idea => {
    const draft = blogDraftForIdea(idea.id);
    const sourceClass = (idea.source || "ai").toLowerCase();
    const card = el("div", {
      class: `idea-card ${idea.finalized ? "finalized" : ""}`,
    }, [
      el("div", { class: "idea-meta" }, [
        el("span", { class: `idea-source ${sourceClass}` }, idea.source || "ai"),
        idea.source_ref && /^https?:/.test(idea.source_ref)
          ? el("a", { class: "source-link", href: idea.source_ref, target: "_blank", rel: "noopener noreferrer" }, "open ↗")
          : (idea.source_ref ? el("span", { class: "source-link" }, idea.source_ref.slice(0, 36)) : null),
      ]),
      el("h3", { class: "idea-title" }, idea.title),
      idea.angle ? el("div", { class: "idea-angle" }, idea.angle) : null,
      el("div", { class: "idea-actions" }, [
        (() => {
          const b = el("button", {
            class: `btn ${idea.finalized ? "primary" : "ghost"}`,
            title: idea.finalized ? "open workspace" : "finalize this topic and open workspace",
          }, [
            el("span", { class: "btn-key" }, idea.finalized ? "▸" : "★"),
            el("span", {}, idea.finalized ? "open" : "finalize"),
          ]);
          b.addEventListener("click", () => finalizeIdea(idea.id));
          return b;
        })(),
        (() => {
          if (!idea.finalized) return null;
          const b = el("button", { class: "btn ghost", title: "unfinalize — keep idea but exit workspace" }, [
            el("span", { class: "btn-key" }, "↺"), el("span", {}, "unfinalize"),
          ]);
          b.addEventListener("click", () => unfinalizeIdea(idea.id));
          return b;
        })(),
        draft ? el("span", { class: "agent-meta-pill", title: "this idea has a draft" }, "▸ draft") : null,
        (() => {
          const b = el("button", { class: "btn ghost", title: "delete idea + any draft" }, [
            el("span", { class: "btn-key" }, "✕"), el("span", {}, "delete"),
          ]);
          b.addEventListener("click", () => deleteIdea(idea.id));
          return b;
        })(),
      ]),
    ]);
    blogEls.ideaGrid.appendChild(card);
  });
}

function renderProjects() {
  blogEls.projects.innerHTML = "";
  if (!blogState.projects.length) {
    blogEls.projects.appendChild(el("div", { class: "ws-empty" }, "no project repos added yet."));
    return;
  }
  blogState.projects.forEach(p => {
    const row = el("div", { class: "project-row" }, [
      el("div", { class: "proj-meta" }, [
        el("span", { class: "proj-name" }, p.name || p.path.split("/").pop()),
        el("span", { class: "proj-path", title: p.path }, p.path),
      ]),
      (() => {
        const b = el("button", { class: "proj-remove", title: "remove" }, "✕");
        b.addEventListener("click", () => removeProject(p.id));
        return b;
      })(),
    ]);
    blogEls.projects.appendChild(row);
  });
}

function renderArchive() {
  if (!blogEls.archiveList) return;
  blogEls.archiveList.innerHTML = "";
  const posts = blogState.medium_seen || [];
  if (blogEls.archiveCount) blogEls.archiveCount.textContent = String(posts.length);
  if (!posts.length) {
    blogEls.archiveList.appendChild(
      el("div", { class: "ws-empty", style: "padding:14px; font-size:11px;" },
         "no archive yet — click refresh from medium.")
    );
    return;
  }
  posts.forEach(p => {
    const date = (p.date || "").slice(0, 16);  // already short for RSS dates
    const row = el("div", { class: "archive-row" }, [
      el("a", { href: p.url, target: "_blank", rel: "noopener noreferrer", title: p.title }, [
        date ? el("span", { class: "arc-date" }, date) : null,
        el("span", { class: "arc-title" }, p.title || "(untitled)"),
      ]),
      el("span", { class: "open-arrow" }, "↗"),
    ]);
    blogEls.archiveList.appendChild(row);
  });
}

function renderWorkspace() {
  blogEls.workspaceWrap.innerHTML = "";
  if (!blogState.finalizedId) return;
  const idea = blogIdeaById(blogState.finalizedId);
  if (!idea) { blogState.finalizedId = null; return; }
  const draft = blogDraftForIdea(idea.id);
  const variations = idea.variations || [];

  const tabs = el("div", { class: "workspace-tabs" }, [
    tabButton("variations", `10 variations${variations.length ? ` · ${variations.length}` : ""}`),
    tabButton("draft", draft ? "draft" : "draft (none yet)"),
  ]);

  const body = el("div", { class: "workspace-body" });
  if (blogState.activeTab === "variations") {
    body.appendChild(renderVariations(idea, variations));
  } else {
    body.appendChild(renderDraftEditor(idea, draft));
  }

  const ws = el("div", { class: "workspace" }, [
    el("div", { class: "workspace-head" }, [
      el("div", {}, [
        el("div", { class: "crumb-mini" }, "finalized topic"),
        el("h2", {}, idea.title),
        idea.angle ? el("div", { class: "muted", style: "font-size:13px; margin-top:6px;" }, idea.angle) : null,
      ]),
      el("div", { class: "workspace-head-actions", style: "display:flex; gap:6px; align-items:center;" }, [
        (() => {
          const b = el("button", { class: "btn ghost" }, [
            el("span", { class: "btn-key" }, "↺"), el("span", {}, "unfinalize"),
          ]);
          b.addEventListener("click", () => unfinalizeIdea(idea.id));
          return b;
        })(),
      ]),
    ]),
    tabs,
    body,
  ]);
  blogEls.workspaceWrap.appendChild(ws);
}

function tabButton(name, label) {
  const b = el("button", {
    class: `ws-tab ${blogState.activeTab === name ? "active" : ""}`,
    "data-tab": name,
  }, label);
  b.addEventListener("click", () => {
    blogState.activeTab = name;
    renderWorkspace();
  });
  return b;
}

function renderVariations(idea, variations) {
  const wrap = el("div");
  const head = el("div", {
    style: "display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap;",
  }, [
    (() => {
      const b = el("button", { class: "btn primary" }, [
        el("span", { class: "btn-key" }, "✦"),
        el("span", {}, variations.length ? "regenerate 10 variations" : "generate 10 variations"),
      ]);
      b.addEventListener("click", () => generateVariations(idea.id, b));
      return b;
    })(),
    el("span", { class: "muted-mono", style: "align-self:center;" },
      variations.length ? "click a variation to use it as the draft title." : "no variations yet."),
  ]);
  wrap.appendChild(head);

  if (variations.length) {
    const list = el("div", { class: "variations-list" });
    variations.forEach((v, i) => {
      const row = el("div", { class: "variation-row" }, [
        el("span", { class: "var-num" }, String(i + 1).padStart(2, "0")),
        el("span", { class: "var-title" }, v),
        (() => {
          const b = el("button", { class: "btn ghost", title: "generate full draft from this title" }, [
            el("span", { class: "btn-key" }, "✎"), el("span", {}, "use → draft"),
          ]);
          b.addEventListener("click", () => generateDraft(idea.id, v, b));
          return b;
        })(),
      ]);
      list.appendChild(row);
    });
    wrap.appendChild(list);
  } else {
    wrap.appendChild(el("div", { class: "ws-empty" }, "hit generate to brainstorm 10 title variations off this idea."));
  }
  return wrap;
}

function renderDraftEditor(idea, draft) {
  const wrap = el("div");

  if (!draft || !draft.content) {
    const head = el("div", { style: "display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap;" }, [
      (() => {
        const b = el("button", { class: "btn primary" }, [
          el("span", { class: "btn-key" }, "✦"),
          el("span", {}, "generate full blog draft"),
        ]);
        b.addEventListener("click", () => generateDraft(idea.id, null, b));
        return b;
      })(),
      el("span", { class: "muted-mono", style: "align-self:center;" },
        "calls the blog-writer agent with your projects + medium archive as context."),
    ]);
    wrap.appendChild(head);
    wrap.appendChild(el("div", { class: "ws-empty" }, "no draft yet — generate one to start."));
    return wrap;
  }

  const split = el("div", { class: "draft-split" });

  // editor side
  const titleInput = el("input", {
    class: "draft-title",
    type:  "text",
    value: draft.title || "",
  });
  const editor = el("textarea", { spellcheck: "false" });
  editor.value = draft.content;

  let saveTimer = null;
  const flagDirty = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveDraftContent(draft.id, editor.value, titleInput.value), 500);
  };
  editor.addEventListener("input", flagDirty);
  titleInput.addEventListener("input", flagDirty);

  const meta = el("div", { class: "draft-meta-row" }, [
    el("span", { class: "muted-mono" }, `draft · ${relTime(draft.updated_at || draft.created_at)} · ${draft.versions ? draft.versions.length : 0} prior versions`),
    (() => {
      const b = el("button", { class: "btn ghost", title: "regenerate draft from scratch (archives current)" }, [
        el("span", { class: "btn-key" }, "↻"), el("span", {}, "regenerate"),
      ]);
      b.addEventListener("click", () => generateDraft(idea.id, titleInput.value || null, b));
      return b;
    })(),
    (() => {
      const b = el("button", { class: "btn primary" }, [
        el("span", { class: "btn-key" }, "⎘"),
        el("span", {}, "copy markdown"),
      ]);
      b.addEventListener("click", () => {
        navigator.clipboard.writeText(editor.value).then(
          () => toast("copied markdown ✓", "ok"),
          () => toast("copy failed", "error"),
        );
      });
      return b;
    })(),
  ]);

  const editorWrap = el("div", { class: "draft-editor" }, [
    titleInput, editor, meta,
  ]);
  split.appendChild(editorWrap);

  // comments side
  const cmtList = el("div", { class: "comments-list" });
  (draft.comments || []).slice().reverse().forEach(c => {
    cmtList.appendChild(el("div", { class: "comment-row" }, [
      el("span", { class: "comment-ts" }, relTime(c.ts)),
      el("div", {}, c.text),
    ]));
  });
  if (!(draft.comments || []).length) {
    cmtList.appendChild(el("div", { class: "ws-empty" }, "no comments yet — add one and the draft revises itself."));
  }

  const cmtInput = el("textarea", { placeholder: "e.g. 'tighten H2 sections', 'less hedging in the intro', 'the gratitude app rejection was 3 times not 2'" });
  const cmtBtn = el("button", { class: "btn primary" }, [
    el("span", { class: "btn-key" }, "⏎"),
    el("span", {}, "save + revise"),
  ]);
  cmtBtn.addEventListener("click", () => {
    const text = (cmtInput.value || "").trim();
    if (!text) { toast("write a comment first", "error"); return; }
    submitComment(draft.id, text, cmtBtn).then(() => { cmtInput.value = ""; });
  });
  cmtInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); cmtBtn.click(); }
  });

  const applied = draft.last_applied_agent_updates;
  const appliedPill = (applied && applied.length)
    ? el("span", { class: "agent-meta-pill", title: applied.map(a => `• ${a.rule}`).join("\n") },
        `+${applied.length} agent rule${applied.length === 1 ? "" : "s"}`)
    : null;

  const commentsPanel = el("aside", { class: "comments-panel" }, [
    el("h4", {}, "comments → revisions"),
    el("div", { class: "projects-sub" }, "each comment re-prompts the blog-writer agent. style notes also flow back into the agent .md."),
    cmtList,
    el("div", { class: "comments-input" }, [
      cmtInput,
      el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
        appliedPill,
        el("div", { class: "spacer" }),
        cmtBtn,
      ]),
    ]),
  ]);
  split.appendChild(commentsPanel);

  wrap.appendChild(split);
  return wrap;
}

async function saveDraftContent(draftId, content, title) {
  try {
    const res = await fetch("/blog/draft/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: draftId, content, title }),
    });
    const data = await blogJson(res);
    if (data.ok) {
      const local = blogState.drafts.find(d => d.id === draftId);
      if (local) {
        local.content = content;
        local.title   = title || local.title;
        local.updated_at = data.draft && data.draft.updated_at;
      }
    } else {
      toast(`save failed: ${data.error || "?"}`, "error");
    }
  } catch (e) {
    toast(`save error: ${e.message}`, "error");
  }
}

async function submitComment(draftId, text, btn) {
  btn.disabled = true;
  const lbl = btn.querySelector("span:last-child");
  const prev = lbl.textContent;
  lbl.textContent = "revising… (30-90s)";
  setOverlay(true);
  try {
    const res = await fetch("/blog/comment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft_id: draftId, text }),
    });
    const data = await blogJson(res);
    if (!data.ok) {
      toast(`revise failed: ${data.error || res.status}`, "error");
      return;
    }
    const newDraft = data.draft;
    const idx = blogState.drafts.findIndex(d => d.id === draftId);
    if (idx >= 0) blogState.drafts[idx] = newDraft;
    else blogState.drafts.push(newDraft);
    if (newDraft.last_applied_agent_updates && newDraft.last_applied_agent_updates.length) {
      toast(`revised · ${newDraft.last_applied_agent_updates.length} agent rule(s) added ✓`, "ok");
      // also reload agent textarea so user sees the new style notes
      try {
        const ar = await fetch("/blog-agent", { cache: "no-store" });
        const ad = await ar.json();
        if (ad.content) {
          blogState.agentSaved = ad.content;
          blogState.agentMtime = ad.mtime || blogState.agentMtime;
          blogEls.agentText.value = ad.content;
          blogEls.agentBytes.textContent = `${ad.content.length} bytes`;
          blogEls.agentMtime.textContent = ad.mtime ? relTime(ad.mtime) : blogEls.agentMtime.textContent;
          setBlogAgentDirty(false);
        }
      } catch {}
    } else {
      toast("revised ✓", "ok");
    }
    renderWorkspace();
  } catch (e) {
    toast(`network error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    btn.disabled = false;
    lbl.textContent = prev;
  }
}

async function scrapeMedium() {
  blogEls.scrape.disabled = true;
  const lbl = blogEls.scrape.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "scraping…";
  try {
    const res = await fetch("/blog/scrape-medium", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`scrape failed: ${data.error || "?"}`, "error"); return; }
    const n = (data.posts || []).length;
    toast(`medium archive cached: ${n} posts · the generator will avoid these ✓`, "ok");
    await loadBlog();
  } catch (e) {
    toast(`scrape error: ${e.message}`, "error");
  } finally {
    blogEls.scrape.disabled = false;
    lbl.textContent = prev;
  }
}

async function generateIdeasNow() {
  blogEls.generate.disabled = true;
  const lbl = blogEls.generate.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "thinking… (60-90s)";
  setOverlay(true);
  try {
    const res = await fetch("/blog/generate-ideas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`idea gen failed: ${data.error || "?"}`, "error"); return; }
    toast(`+${(data.ideas || []).length} fresh ideas (old non-finalized wiped) ✓`, "ok");
    await loadBlog();
  } catch (e) {
    toast(`idea gen error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    blogEls.generate.disabled = false;
    lbl.textContent = prev;
  }
}

async function researchTrendingNow() {
  blogEls.research.disabled = true;
  const lbl = blogEls.research.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "scouting reddit + x… (2-4min)";
  setOverlay(true);
  try {
    const res = await fetch("/blog/research-trending", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`trending research failed: ${data.error || "?"}`, "error"); return; }
    toast(`+${(data.ideas || []).length} trending ideas from reddit/x ✓`, "ok");
    await loadBlog();
  } catch (e) {
    toast(`trending research error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    blogEls.research.disabled = false;
    lbl.textContent = prev;
  }
}

async function clearIdeasList() {
  const nonFinalized = blogState.ideas.filter(i => !i.finalized).length;
  if (!nonFinalized) {
    toast("nothing to clear — all ideas are either finalized or list is empty", "");
    return;
  }
  if (!confirm(`wipe ${nonFinalized} non-finalized idea(s)? finalized ones stay.`)) return;
  blogEls.clear.disabled = true;
  try {
    const res = await fetch("/blog/clear-ideas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`clear failed: ${data.error || "?"}`, "error"); return; }
    toast(`cleared ${data.removed || 0} ideas`, "ok");
    await loadBlog();
  } catch (e) {
    toast(`clear error: ${e.message}`, "error");
  } finally {
    blogEls.clear.disabled = false;
  }
}

async function finalizeIdea(id) {
  try {
    const res = await fetch("/blog/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`finalize failed: ${data.error || "?"}`, "error"); return; }
    blogState.finalizedId = id;
    blogState.activeTab = "draft";
    await loadBlog();

    // jump to studio tab
    showSection("studio");
    history.replaceState(null, "", "#studio");

    // auto-prep: kick draft + variations in parallel (only if not already done)
    const idea = blogIdeaById(id);
    const existingDraft = blogDraftForIdea(id);
    const hasVariations = idea && (idea.variations || []).length;

    const jobs = [];
    if (!hasVariations) jobs.push(generateVariations(id, null));
    if (!existingDraft) jobs.push(generateDraft(id, null, null));

    if (jobs.length) {
      toast(`finalized ✓ generating ${jobs.length === 2 ? "draft + 10 titles" : (existingDraft ? "10 titles" : "draft")} in parallel…`, "ok");
      setOverlay(true);
      Promise.all(jobs).finally(() => {
        setOverlay(false);
        toast("studio ready ✓", "ok");
        renderStudio();
      });
    } else {
      toast("finalized — draft + titles already exist ✓", "ok");
    }
  } catch (e) {
    toast(`finalize error: ${e.message}`, "error");
  }
}

async function unfinalizeIdea(id) {
  if (!confirm("unfinalize this blog? draft + titles stay but it leaves the studio list.")) return;
  try {
    await fetch("/blog/unfinalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    blogState.finalizedId = null;
    await loadBlog();
  } catch (e) {
    toast(`unfinalize error: ${e.message}`, "error");
  }
}

async function deleteIdea(id) {
  if (!confirm("delete this idea + any draft attached? this is irreversible.")) return;
  try {
    const res = await fetch(`/blog/ideas/${encodeURIComponent(id)}`, { method: "DELETE" });
    const data = await blogJson(res);
    if (!data.ok) { toast(`delete failed: ${data.error || "?"}`, "error"); return; }
    if (blogState.finalizedId === id) blogState.finalizedId = null;
    toast("deleted", "");
    await loadBlog();
  } catch (e) {
    toast(`delete error: ${e.message}`, "error");
  }
}

async function generateVariations(ideaId, btn) {
  let lbl = null, prev = "";
  if (btn) {
    btn.disabled = true;
    lbl = btn.querySelector("span:last-child");
    prev = lbl.textContent; lbl.textContent = "generating… (~30s)";
  }
  if (!btn) setOverlay(true);
  try {
    const res = await fetch("/blog/variations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: ideaId }),
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`variations failed: ${data.error || "?"}`, "error"); return null; }
    const idea = blogIdeaById(ideaId);
    if (idea) idea.variations = data.variations || [];
    if (btn) toast(`+${(data.variations || []).length} variations ✓`, "ok");
    renderWorkspace();
    renderStudio();
    return data.variations || [];
  } catch (e) {
    toast(`variations error: ${e.message}`, "error");
    return null;
  } finally {
    if (btn) setOverlay(false); else setOverlay(false);
    if (btn) { btn.disabled = false; lbl.textContent = prev; }
  }
}

async function generateDraft(ideaId, overrideTitle, btn) {
  let lbl = null, prev = "";
  if (btn) {
    btn.disabled = true;
    lbl = btn.querySelector("span:last-child");
    prev = lbl.textContent; lbl.textContent = "drafting… (60-120s)";
  }
  if (!btn) setOverlay(true);
  try {
    const res = await fetch("/blog/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: ideaId, title: overrideTitle || "" }),
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`draft failed: ${data.error || "?"}`, "error"); return null; }
    const idx = blogState.drafts.findIndex(d => d.idea_id === ideaId);
    if (idx >= 0) blogState.drafts[idx] = data.draft; else blogState.drafts.push(data.draft);
    blogState.activeTab = "draft";
    if (btn) toast("draft ready ✓", "ok");
    renderWorkspace();
    renderStudio();
    return data.draft;
  } catch (e) {
    toast(`draft error: ${e.message}`, "error");
    return null;
  } finally {
    setOverlay(false);
    if (btn) { btn.disabled = false; lbl.textContent = prev; }
  }
}

async function addProject() {
  const path = (blogEls.projectPath.value || "").trim();
  const name = (blogEls.projectName.value || "").trim();
  if (!path) { toast("path required", "error"); return; }
  blogEls.projectAdd.disabled = true;
  try {
    const res = await fetch("/blog/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, name }),
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`add failed: ${data.error || "?"}`, "error"); return; }
    if (data.signal && data.signal.error) {
      toast(`added (warning: ${data.signal.error})`, "");
    } else {
      toast("project added ✓", "ok");
    }
    blogEls.projectPath.value = "";
    blogEls.projectName.value = "";
    await loadBlog();
  } catch (e) {
    toast(`add error: ${e.message}`, "error");
  } finally {
    blogEls.projectAdd.disabled = false;
  }
}

async function removeProject(id) {
  if (!confirm("remove this project from tracking?")) return;
  try {
    const res = await fetch(`/blog/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
    const data = await blogJson(res);
    if (!data.ok) { toast(`remove failed: ${data.error || "?"}`, "error"); return; }
    toast("removed", "");
    await loadBlog();
  } catch (e) {
    toast(`remove error: ${e.message}`, "error");
  }
}


// ─────────────────── blog studio (09) ───────────────────

const studioEls = {
  list:     null,  // resolved lazily — studio section may not exist on old builds
  main:     null,
  empty:    null,
  navCount: null,
};

function studioResolveEls() {
  if (studioEls.list) return;
  studioEls.list     = document.querySelector("#studio-list");
  studioEls.main     = document.querySelector("#studio-main");
  studioEls.empty    = document.querySelector("#studio-empty");
  studioEls.navCount = document.querySelector('[data-count="studio"]');
}

async function loadStudio() {
  studioResolveEls();
  // studio shares blog state — load if not loaded
  if (!blogState.ideas.length && !blogState.drafts.length) {
    await loadBlog();
  }
  renderStudio();
}

function studioFinalizedIdeas() {
  return blogState.ideas.filter(i => i.finalized);
}

function renderStudio() {
  studioResolveEls();
  if (!studioEls.list) return;

  const finalized = studioFinalizedIdeas();
  if (studioEls.navCount) studioEls.navCount.textContent = String(finalized.length || "—");

  // sidebar list
  studioEls.list.innerHTML = "";
  const countEl = document.querySelector("#studio-count");
  if (countEl) countEl.textContent = String(finalized.length);

  if (!finalized.length) {
    studioEls.list.appendChild(el("div", { class: "ws-empty", style: "padding:14px; font-size:11px;" },
      "no finalized blogs yet."));
    studioEls.main.innerHTML = "";
    studioEls.main.appendChild(el("div", { class: "ws-empty", style: "padding:40px; text-align:center;" }, [
      "no finalized blogs yet — go to ",
      el("strong", {}, "08 blog ideas"),
      ", hit ",
      el("strong", {}, "finalize"),
      " on an idea, and it'll land here with a draft + 10 title variations ready.",
    ]));
    return;
  }

  // ensure something is selected
  if (!blogState.finalizedId || !finalized.find(i => i.id === blogState.finalizedId)) {
    blogState.finalizedId = finalized[0].id;
  }

  finalized.forEach(idea => {
    const draft = blogDraftForIdea(idea.id);
    const variations = idea.variations || [];
    const active = idea.id === blogState.finalizedId;
    const row = el("div", { class: `studio-row ${active ? "active" : ""}` }, [
      el("div", { class: "studio-row-title" }, draft && draft.title ? draft.title : idea.title),
      el("div", { class: "studio-row-meta" }, [
        el("span", { class: "studio-pill" }, draft && draft.content ? "draft ✓" : "draft …"),
        el("span", { class: "studio-pill" }, variations.length ? `${variations.length} titles` : "titles …"),
        draft && draft.comments && draft.comments.length
          ? el("span", { class: "studio-pill" }, `${draft.comments.length} cmt`)
          : null,
        draft && draft.versions && draft.versions.length
          ? el("span", { class: "studio-pill" }, `v${draft.versions.length + 1}`)
          : null,
        draft && draft.published_at
          ? el("span", { class: "studio-pill", style: "color:#00ba7c; border-color:rgba(0,186,124,0.35);" }, "published")
          : null,
      ]),
      el("div", { class: "studio-row-time muted-mono" },
        relTime((draft && draft.updated_at) || idea.created_at)),
    ]);
    row.addEventListener("click", () => {
      blogState.finalizedId = idea.id;
      blogState.activeTab = "draft";
      renderStudio();
    });
    studioEls.list.appendChild(row);
  });

  // main pane — render selected
  studioEls.main.innerHTML = "";
  const idea = blogIdeaById(blogState.finalizedId);
  if (!idea) return;
  studioEls.main.appendChild(renderStudioBlog(idea));
}

function renderStudioBlog(idea) {
  const draft = blogDraftForIdea(idea.id);
  const variations = idea.variations || [];

  const wrap = el("div", { class: "studio-blog" });

  // header
  wrap.appendChild(el("div", { class: "studio-blog-head" }, [
    el("div", {}, [
      el("div", { class: "crumb-mini" }, "finalized blog"),
      el("h2", {}, draft && draft.title ? draft.title : idea.title),
      idea.angle ? el("div", { class: "muted", style: "font-size:13px; margin-top:6px;" }, idea.angle) : null,
    ]),
    el("div", { style: "display:flex; gap:6px; align-items:center;" }, [
      (() => {
        const b = el("button", { class: "btn ghost", title: "open full-screen reading preview" }, [
          el("span", { class: "btn-key" }, "⛶"), el("span", {}, "full preview"),
        ]);
        b.addEventListener("click", () => openPreviewModal(idea.id));
        return b;
      })(),
      (() => {
        const b = el("button", { class: "btn ghost", title: "regenerate both draft + titles" }, [
          el("span", { class: "btn-key" }, "↻"), el("span", {}, "regen all"),
        ]);
        b.addEventListener("click", () => regenStudioAll(idea.id, b));
        return b;
      })(),
      (() => {
        const b = el("button", { class: "btn ghost" }, [
          el("span", { class: "btn-key" }, "↺"), el("span", {}, "unfinalize"),
        ]);
        b.addEventListener("click", () => unfinalizeIdea(idea.id));
        return b;
      })(),
    ]),
  ]));

  // titles panel
  wrap.appendChild(renderStudioTitles(idea, variations, draft));

  // thumbnail panel
  wrap.appendChild(renderStudioThumbnail(idea, draft));

  // draft + comments split
  wrap.appendChild(renderStudioDraft(idea, draft));

  // revision history
  if (draft && draft.versions && draft.versions.length) {
    wrap.appendChild(renderStudioVersions(draft));
  }

  return wrap;
}

function renderStudioTitles(idea, variations, draft) {
  const wrap = el("div", { class: "studio-panel" });
  wrap.appendChild(el("div", { class: "studio-panel-head" }, [
    el("h4", {}, [
      "title options",
      el("span", { class: "studio-pill", style: "margin-left:8px;" }, `${variations.length}/10`),
    ]),
    (() => {
      const b = el("button", { class: "btn ghost" }, [
        el("span", { class: "btn-key" }, "✦"),
        el("span", {}, variations.length ? "regen 10 titles" : "generate 10 titles"),
      ]);
      b.addEventListener("click", () => generateVariations(idea.id, b));
      return b;
    })(),
  ]));

  // Always show the current draft title at the top
  const currentTitle = (draft && draft.title) || idea.title;
  const titlesList = el("div", { class: "studio-titles" });

  // First row: current (highlighted)
  const currentRow = el("div", { class: "studio-title-row current" }, [
    el("span", { class: "var-num" }, "→"),
    el("span", { class: "var-title" }, currentTitle),
    el("span", { class: "studio-pill" }, "current"),
  ]);
  titlesList.appendChild(currentRow);

  variations.forEach((v, i) => {
    if (v === currentTitle) return;  // skip duplicate
    const row = el("div", { class: "studio-title-row" }, [
      el("span", { class: "var-num" }, String(i + 1).padStart(2, "0")),
      el("span", { class: "var-title" }, v),
      (() => {
        const b = el("button", { class: "btn ghost", title: "use this title (regenerates draft with new title)" }, [
          el("span", { class: "btn-key" }, "✓"), el("span", {}, "use this"),
        ]);
        b.addEventListener("click", () => useTitle(idea.id, v, b));
        return b;
      })(),
    ]);
    titlesList.appendChild(row);
  });

  if (!variations.length) {
    titlesList.appendChild(el("div", { class: "ws-empty", style: "padding:14px;" },
      "titles are still generating — or hit the button to kick them off."));
  }

  wrap.appendChild(titlesList);
  return wrap;
}

async function useTitle(ideaId, title, btn) {
  if (!confirm(`switch title to:\n\n"${title}"\n\nthis regenerates the draft with the new title (current draft archived to versions).`)) return;
  await generateDraft(ideaId, title, btn);
}


function renderStudioThumbnail(idea, draft) {
  const wrap = el("div", { class: "studio-panel" });
  const hasThumb = draft && draft.thumbnail_path;
  const thumbUrl = hasThumb ? `/thumbnails/${draft.thumbnail_path.split("/").pop()}?t=${encodeURIComponent(draft.thumbnail_at || "")}` : null;

  wrap.appendChild(el("div", { class: "studio-panel-head" }, [
    el("h4", {}, [
      "thumbnail",
      hasThumb
        ? el("span", { class: "studio-pill", style: "margin-left:8px; color:#00ba7c; border-color:rgba(0,186,124,0.35);" }, `${relTime(draft.thumbnail_at)}`)
        : el("span", { class: "studio-pill", style: "margin-left:8px;" }, "none yet"),
    ]),
    (() => {
      const b = el("button", { class: "btn primary" }, [
        el("span", { class: "btn-key" }, "✦"),
        el("span", {}, hasThumb ? "regenerate" : "generate thumbnail"),
      ]);
      b.addEventListener("click", () => openThumbModal(draft));
      return b;
    })(),
  ]));

  if (!hasThumb || !draft) {
    wrap.appendChild(el("div", { class: "ws-empty", style: "padding:14px;" },
      "no thumbnail yet — hit generate to spawn the chatgpt browser-automation agent (2–4 min). minimal centered text on 16:9 by default."));
    return wrap;
  }

  const body = el("div", { class: "thumb-body" }, [
    (() => {
      const a = el("a", { href: thumbUrl, target: "_blank", title: "open full size" });
      a.appendChild(el("img", { class: "thumb-img", src: thumbUrl, alt: draft.title }));
      return a;
    })(),
    el("div", { class: "thumb-meta" }, [
      el("div", { class: "muted-mono" }, draft.thumbnail_path),
      el("div", { style: "display:flex; gap:6px; flex-wrap:wrap;" }, [
        (() => {
          const b = el("button", { class: "btn ghost", title: "download to your Downloads folder" }, [
            el("span", { class: "btn-key" }, "↓"), el("span", {}, "download"),
          ]);
          b.addEventListener("click", () => {
            const a = document.createElement("a");
            a.href = thumbUrl; a.download = draft.thumbnail_path.split("/").pop();
            document.body.appendChild(a); a.click(); a.remove();
          });
          return b;
        })(),
        (() => {
          const b = el("button", { class: "btn ghost", title: "copy image to clipboard" }, [
            el("span", { class: "btn-key" }, "⎘"), el("span", {}, "copy image"),
          ]);
          b.addEventListener("click", () => copyImageToClipboard(thumbUrl));
          return b;
        })(),
      ]),
      draft.thumbnail_prompt
        ? el("details", { class: "thumb-prompt-details" }, [
            el("summary", {}, "show prompt used"),
            el("pre", {}, draft.thumbnail_prompt),
          ])
        : null,
    ]),
  ]);
  wrap.appendChild(body);
  return wrap;
}

async function copyImageToClipboard(url) {
  try {
    const r = await fetch(url);
    const blob = await r.blob();
    if (window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      toast("image copied to clipboard ✓", "ok");
    } else {
      toast("rich clipboard not supported in this browser", "error");
    }
  } catch (e) { toast(`copy failed: ${e.message}`, "error"); }
}


// ─────────────────── thumbnail modal ───────────────────

const thumbModal = {
  root:        null,
  promptText:  null,
  additional:  null,
  refInput:    null,
  refClear:    null,
  refPreview:  null,
  status:      null,
  go:          null,
  cancel:      null,
  close:       null,
  draftId:     null,
  refImageB64: null,
  refImageName:null,
};

function thumbResolveEls() {
  if (thumbModal.root) return;
  thumbModal.root        = document.querySelector("#thumb-modal");
  thumbModal.promptText  = document.querySelector("#thumb-prompt");
  thumbModal.additional  = document.querySelector("#thumb-additional");
  thumbModal.refInput    = document.querySelector("#thumb-ref-input");
  thumbModal.refClear    = document.querySelector("#thumb-ref-clear");
  thumbModal.refPreview  = document.querySelector("#thumb-ref-preview");
  thumbModal.status      = document.querySelector("#thumb-status");
  thumbModal.go          = document.querySelector("#thumb-go");
  thumbModal.cancel      = document.querySelector("#thumb-cancel");
  thumbModal.close       = document.querySelector("#thumb-close");
}

function openThumbModal(draft) {
  thumbResolveEls();
  thumbModal.draftId = draft.id;
  // pre-fill prompt with the default template + current title
  const base = `For this blog create a medium thumbnail which is minimal, with centered text.\nAspect ratio: 16:9\n\nBlog title: ${draft.title || "untitled"}`;
  thumbModal.promptText.value = base;
  thumbModal.additional.value = "";
  thumbClearRef();
  thumbModal.status.classList.remove("error");
  thumbModal.status.innerHTML = "will spawn <code>claude --agent claude</code> with the <code>browser-harness</code> skill — drives your chrome to chatgpt, pastes the prompt, downloads the image. takes 2–4 min.";
  thumbModal.root.classList.remove("hidden");
}

function closeThumbModal() {
  thumbResolveEls();
  thumbModal.root.classList.add("hidden");
  thumbModal.draftId = null;
}

function thumbClearRef() {
  thumbResolveEls();
  thumbModal.refImageB64 = null;
  thumbModal.refImageName = null;
  thumbModal.refInput.value = "";
  thumbModal.refPreview.src = "";
  thumbModal.refPreview.classList.add("hidden");
  thumbModal.refClear.disabled = true;
}

function thumbAcceptImageBlob(blob, name) {
  if (!blob) return false;
  if (!/^image\//.test(blob.type || "")) return false;
  if (blob.size > 6 * 1024 * 1024) {
    toast("ref image too large — keep under 6 MB", "error");
    return false;
  }
  thumbResolveEls();
  const reader = new FileReader();
  reader.onload = () => {
    thumbModal.refImageB64  = reader.result;    // data URL
    thumbModal.refImageName = name || `pasted-${Date.now()}.${(blob.type.split("/")[1] || "png").split("+")[0]}`;
    thumbModal.refPreview.src = reader.result;
    thumbModal.refPreview.classList.remove("hidden");
    thumbModal.refClear.disabled = false;
  };
  reader.readAsDataURL(blob);
  return true;
}

async function thumbOnFile(e) {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  if (!thumbAcceptImageBlob(f, f.name)) thumbClearRef();
}

function thumbOnPaste(e) {
  if (!thumbModal.root || thumbModal.root.classList.contains("hidden")) return;
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const it of items) {
    if (it.kind === "file" && /^image\//.test(it.type)) {
      const blob = it.getAsFile();
      if (thumbAcceptImageBlob(blob, blob && blob.name)) {
        e.preventDefault();  // don't let the image data fall into a textarea
        toast("ref image pasted ✓", "ok");
        return;
      }
    }
  }
}

async function thumbGenerate() {
  thumbResolveEls();
  const promptVal = (thumbModal.promptText.value || "").trim();
  const additional = (thumbModal.additional.value || "").trim();
  if (!promptVal) { toast("prompt cannot be empty", "error"); return; }
  if (!thumbModal.draftId) { toast("no draft selected", "error"); return; }

  thumbModal.go.disabled = true;
  thumbModal.cancel.disabled = true;
  const lbl = thumbModal.go.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "driving chatgpt… (2-4 min)";
  thumbModal.status.classList.remove("error");
  thumbModal.status.innerHTML = "agent is opening chatgpt in your chrome and pasting the prompt. don't close the chrome window. you can keep using this dashboard.";
  setOverlay(true);

  try {
    const payload = {
      draft_id: thumbModal.draftId,
      additional_text: additional,
      // override the default prompt with whatever the user has in the textarea
      prompt_override: promptVal,
    };
    if (thumbModal.refImageB64) {
      payload.ref_image_b64 = thumbModal.refImageB64;
      payload.ref_image_name = thumbModal.refImageName || "ref";
    }
    const res = await fetch("/blog/thumbnail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await blogJson(res);
    if (!data.ok) {
      thumbModal.status.classList.add("error");
      thumbModal.status.textContent = `failed: ${data.error || "?"}`;
      toast(`thumbnail failed: ${data.error || "?"}`, "error");
      return;
    }
    // reflect on local draft state
    const d = blogState.drafts.find(x => x.id === thumbModal.draftId);
    if (d) {
      d.thumbnail_path   = data.path;
      d.thumbnail_at     = data.generated_at;
      d.thumbnail_prompt = data.prompt;
      d.thumbnail_ref    = data.ref_path || null;
    }
    toast("thumbnail generated ✓", "ok");
    closeThumbModal();
    renderStudio();
  } catch (e) {
    thumbModal.status.classList.add("error");
    thumbModal.status.textContent = `error: ${e.message}`;
    toast(`thumbnail error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    thumbModal.go.disabled = false;
    thumbModal.cancel.disabled = false;
    lbl.textContent = prev;
  }
}

function setupThumbModal() {
  thumbResolveEls();
  if (!thumbModal.root) return;
  thumbModal.refInput.addEventListener("change", thumbOnFile);
  thumbModal.refClear.addEventListener("click", thumbClearRef);
  thumbModal.go.addEventListener("click", thumbGenerate);
  thumbModal.cancel.addEventListener("click", closeThumbModal);
  thumbModal.close.addEventListener("click", closeThumbModal);
  thumbModal.root.addEventListener("click", (e) => {
    if (e.target === thumbModal.root) closeThumbModal();
  });
  // listen on document — paste events fire wherever focus is (textarea, body, etc.)
  document.addEventListener("paste", thumbOnPaste);
}

function renderStudioDraft(idea, draft) {
  const wrap = el("div", { class: "studio-panel" });
  wrap.appendChild(el("div", { class: "studio-panel-head" }, [
    el("h4", {}, "draft + comments"),
    draft && draft.content
      ? el("span", { class: "muted-mono" }, `${draft.content.length} chars · ${relTime(draft.updated_at || draft.created_at)}`)
      : el("span", { class: "muted-mono" }, "no draft yet"),
  ]));

  if (!draft || !draft.content) {
    const body = el("div", { style: "padding:14px;" });
    const b = el("button", { class: "btn primary" }, [
      el("span", { class: "btn-key" }, "✦"),
      el("span", {}, "generate full draft"),
    ]);
    b.addEventListener("click", () => generateDraft(idea.id, null, b));
    body.appendChild(b);
    body.appendChild(el("div", { class: "ws-empty", style: "padding:14px 0 0;" },
      "calls the blog-writer agent with your projects + medium archive as context."));
    wrap.appendChild(body);
    return wrap;
  }

  const split = el("div", { class: "draft-split" });

  // editor side
  const titleInput = el("input", {
    class: "draft-title",
    type:  "text",
    value: draft.title || "",
  });
  const editor = el("textarea", { spellcheck: "false" });
  editor.value = draft.content;

  let saveTimer = null;
  const flagDirty = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveDraftContent(draft.id, editor.value, titleInput.value), 500);
  };
  editor.addEventListener("input", flagDirty);
  titleInput.addEventListener("input", flagDirty);

  const publishedPill = draft.published_at
    ? el("span", { class: "studio-pill", title: draft.published_path || "" },
        `published ${relTime(draft.published_at)}`)
    : null;

  const meta = el("div", { class: "draft-meta-row" }, [
    el("span", { class: "muted-mono" }, `draft · ${relTime(draft.updated_at || draft.created_at)} · ${draft.versions ? draft.versions.length : 0} prior versions`),
    publishedPill,
    (() => {
      const b = el("button", { class: "btn ghost", title: "regenerate draft from scratch (archives current)" }, [
        el("span", { class: "btn-key" }, "↻"), el("span", {}, "regen draft"),
      ]);
      b.addEventListener("click", () => generateDraft(idea.id, titleInput.value || null, b));
      return b;
    })(),
    (() => {
      const b = el("button", { class: "btn ghost" }, [
        el("span", { class: "btn-key" }, "⎘"),
        el("span", {}, "copy markdown"),
      ]);
      b.addEventListener("click", () => {
        navigator.clipboard.writeText(editor.value).then(
          () => toast("copied markdown ✓", "ok"),
          () => toast("copy failed", "error"),
        );
      });
      return b;
    })(),
    (() => {
      const b = el("button", { class: "btn primary", title: "archive markdown + copy to clipboard + open medium.com/new-story" }, [
        el("span", { class: "btn-key" }, "↗"),
        el("span", {}, draft.published_at ? "re-publish" : "publish to medium"),
      ]);
      b.addEventListener("click", () => publishDraft(draft.id, editor.value, b));
      return b;
    })(),
  ]);

  const editorWrap = el("div", { class: "draft-editor" }, [titleInput, editor, meta]);
  split.appendChild(editorWrap);

  // comments side
  const cmtList = el("div", { class: "comments-list" });
  (draft.comments || []).slice().reverse().forEach(c => {
    cmtList.appendChild(el("div", { class: "comment-row" }, [
      el("span", { class: "comment-ts" }, relTime(c.ts)),
      el("div", {}, c.text),
    ]));
  });
  if (!(draft.comments || []).length) {
    cmtList.appendChild(el("div", { class: "ws-empty" }, "no comments yet — add one and the draft revises itself."));
  }

  const cmtInput = el("textarea", {
    placeholder: "e.g. 'tighten the H2', 'more native-bridge specifics', 'cut the intro question'",
  });
  const cmtBtn = el("button", { class: "btn primary" }, [
    el("span", { class: "btn-key" }, "⏎"),
    el("span", {}, "save + revise"),
  ]);
  cmtBtn.addEventListener("click", () => {
    const text = (cmtInput.value || "").trim();
    if (!text) { toast("write a comment first", "error"); return; }
    submitComment(draft.id, text, cmtBtn).then(() => { cmtInput.value = ""; renderStudio(); });
  });
  cmtInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); cmtBtn.click(); }
  });

  const applied = draft.last_applied_agent_updates;
  const appliedPill = (applied && applied.length)
    ? el("span", { class: "agent-meta-pill", title: applied.map(a => `• ${a.rule}`).join("\n") },
        `+${applied.length} agent rule${applied.length === 1 ? "" : "s"}`)
    : null;

  const commentsPanel = el("aside", { class: "comments-panel" }, [
    el("h4", {}, "comments → revisions"),
    el("div", { class: "projects-sub" }, "each comment re-prompts blog-writer. style notes flow back into the agent .md."),
    cmtList,
    el("div", { class: "comments-input" }, [
      cmtInput,
      el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
        appliedPill,
        el("div", { class: "spacer" }),
        cmtBtn,
      ]),
    ]),
  ]);
  split.appendChild(commentsPanel);

  wrap.appendChild(split);
  return wrap;
}

function renderStudioVersions(draft) {
  const wrap = el("div", { class: "studio-panel" });
  const versions = (draft.versions || []).slice().reverse();
  wrap.appendChild(el("div", { class: "studio-panel-head" }, [
    el("h4", {}, [
      "revision history",
      el("span", { class: "studio-pill", style: "margin-left:8px;" }, `${versions.length}`),
    ]),
    el("span", { class: "muted-mono" }, "newest first — click to peek"),
  ]));

  const list = el("div", { class: "studio-versions" });
  versions.forEach((v, i) => {
    const idxLabel = `v${versions.length - i}`;
    const preview = (v.content || "").split("\n").slice(0, 2).join(" / ").slice(0, 160);
    const row = el("details", { class: "studio-version" }, [
      el("summary", {}, [
        el("span", { class: "var-num" }, idxLabel),
        el("span", { class: "var-title" }, preview || "(empty)"),
        el("span", { class: "muted-mono" }, `${(v.source || "snapshot")} · ${relTime(v.saved_at)}`),
      ]),
      el("pre", { class: "studio-version-body" }, v.content || ""),
    ]);
    list.appendChild(row);
  });
  wrap.appendChild(list);
  return wrap;
}

// ─────────── full-screen blog preview modal ───────────

const previewModal = {
  root:    null,
  article: null,
  meta:    null,
  close:   null,
  keyHandler: null,
};

function previewResolveEls() {
  if (previewModal.root) return;
  previewModal.root    = document.querySelector("#preview-modal");
  previewModal.article = document.querySelector("#preview-article");
  previewModal.meta    = document.querySelector("#preview-meta");
  previewModal.close   = document.querySelector("#preview-close");
  if (previewModal.close) {
    previewModal.close.addEventListener("click", closePreviewModal);
  }
  if (previewModal.root) {
    // click on the dim backdrop (outside the shell) closes
    previewModal.root.addEventListener("click", (e) => {
      if (e.target === previewModal.root) closePreviewModal();
    });
  }
}

function openPreviewModal(ideaId) {
  previewResolveEls();
  if (!previewModal.root) return;
  const idea = blogIdeaById(ideaId);
  if (!idea) { toast("idea not found", "error"); return; }
  const draft = blogDraftForIdea(idea.id);
  const title = (draft && draft.title) || idea.title || "untitled";
  const body  = (draft && draft.content) || "";

  const metaParts = [];
  if (body) metaParts.push(`${body.length} chars`);
  if (draft && draft.updated_at) metaParts.push(relTime(draft.updated_at));
  if (draft && draft.versions && draft.versions.length) metaParts.push(`v${draft.versions.length + 1}`);
  if (draft && draft.published_at) metaParts.push("published");
  previewModal.meta.textContent = metaParts.join(" · ");

  const titleHtml = `<h1 class="preview-title">${title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</h1>`;
  const bodyHtml  = body
    ? mdToHtml(body)
    : `<div class="preview-empty">no draft yet — generate one from the studio.</div>`;
  previewModal.article.innerHTML = titleHtml + bodyHtml;
  // reset scroll so each open starts at the top
  const scroller = previewModal.root.querySelector(".preview-scroll");
  if (scroller) scroller.scrollTop = 0;

  previewModal.root.classList.remove("hidden");
  document.body.style.overflow = "hidden";

  previewModal.keyHandler = (e) => {
    if (e.key === "Escape") { e.preventDefault(); closePreviewModal(); }
  };
  document.addEventListener("keydown", previewModal.keyHandler);
}

function closePreviewModal() {
  if (!previewModal.root) return;
  previewModal.root.classList.add("hidden");
  document.body.style.overflow = "";
  if (previewModal.keyHandler) {
    document.removeEventListener("keydown", previewModal.keyHandler);
    previewModal.keyHandler = null;
  }
}

// ─────────── markdown → HTML (focused on blog-writer's output subset) ───────────
//
// Why we need this: Medium does NOT parse pasted markdown text. Its editor
// reads `text/html` from the clipboard (the path that makes Google-Docs /
// Notion paste preserve formatting). So we convert md → html and write BOTH
// formats to the clipboard via ClipboardItem; Medium picks up the html.
//
// Covers: H1-H6, paragraphs, **bold**, *italic*, `inline code`, fenced code
// blocks, [links](url), unordered + ordered lists, blockquotes, hr.
function mdToHtml(md) {
  if (!md) return "";

  // 1. pull out fenced code blocks first so their guts aren't processed
  const codeBlocks = [];
  md = md.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (_, lang, body) => {
    codeBlocks.push({ lang, body });
    return `\x00CODE${codeBlocks.length - 1}\x00`;
  });

  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const inline = (text) => {
    text = esc(text);
    // inline code (do first — protects its content from other inline rules)
    const codes = [];
    text = text.replace(/`([^`]+)`/g, (_, c) => {
      codes.push(c);
      return `\x01C${codes.length - 1}\x01`;
    });
    // bold then italic (bold first so ** doesn't get eaten by single-*)
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    text = text.replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
    // links
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
    // restore inline code
    text = text.replace(/\x01C(\d+)\x01/g, (_, i) => `<code>${codes[+i]}</code>`);
    return text;
  };

  const lines = md.split("\n");
  const out = [];
  let i = 0;
  const isBlockStart = (ln) =>
    /^#{1,6}\s/.test(ln) || /^>\s/.test(ln) || /^\s*[-*+]\s/.test(ln) ||
    /^\s*\d+\.\s/.test(ln) || /^---+\s*$/.test(ln) || /^\x00CODE\d+\x00$/.test(ln);

  while (i < lines.length) {
    const line = lines[i];

    if (/^\x00CODE\d+\x00$/.test(line)) { out.push(line); i++; continue; }

    if (/^#{1,6}\s/.test(line)) {
      const m = line.match(/^(#{1,6})\s+(.*)$/);
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
      i++; continue;
    }
    if (/^---+\s*$/.test(line)) { out.push("<hr/>"); i++; continue; }

    if (/^>\s?/.test(line)) {
      const block = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        block.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      out.push(`<blockquote><p>${inline(block.join(" "))}</p></blockquote>`);
      continue;
    }
    if (/^\s*[-*+]\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      out.push(`<ul>${items.map(it => `<li>${inline(it)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      out.push(`<ol>${items.map(it => `<li>${inline(it)}</li>`).join("")}</ol>`);
      continue;
    }

    if (!line.trim()) { i++; continue; }

    // paragraph — collect until blank/block boundary
    const para = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }

  let html = out.join("\n");

  // restore fenced code blocks as <pre><code>
  html = html.replace(/\x00CODE(\d+)\x00/g, (_, idx) => {
    const cb = codeBlocks[+idx];
    const body = cb.body.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const cls = cb.lang ? ` class="language-${cb.lang}"` : "";
    return `<pre><code${cls}>${body}</code></pre>`;
  });

  // wrap so Medium treats it as a doc fragment (not just inline)
  return `<div>${html}</div>`;
}

async function writeRichClipboard(md, html) {
  // try the rich path first (HTML + plain markdown side-by-side)
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    try {
      const item = new ClipboardItem({
        "text/html":  new Blob([html], { type: "text/html"  }),
        "text/plain": new Blob([md],   { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
      return "rich";
    } catch (e) { /* fall through to plain */ }
  }
  // fallback: plain markdown only
  try { await navigator.clipboard.writeText(md); return "plain"; }
  catch { return "none"; }
}


async function publishDraft(draftId, currentEditorContent, btn) {
  // make sure any unsaved edits land first
  const draftRef = blogState.drafts.find(d => d.id === draftId);
  if (draftRef && currentEditorContent && currentEditorContent !== draftRef.content) {
    await saveDraftContent(draftId, currentEditorContent, draftRef.title);
  }
  btn.disabled = true;
  const lbl = btn.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "archiving…";
  try {
    const res = await fetch("/blog/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft_id: draftId }),
    });
    const data = await blogJson(res);
    if (!data.ok) { toast(`publish failed: ${data.error || "?"}`, "error"); return; }

    // 1. convert md → html and put BOTH formats on clipboard.
    //    medium reads text/html on paste (same path google-docs / notion use).
    //    text/plain stays as a fallback for non-medium targets.
    const md   = data.content || "";
    const html = mdToHtml(md);
    const mode = await writeRichClipboard(md, html);

    // 2. open medium new-story in a new tab (user is already logged in)
    window.open(data.medium_url || "https://medium.com/new-story", "_blank", "noopener");

    // 3. reflect on local state
    if (draftRef) {
      draftRef.published_at   = data.published_at;
      draftRef.published_path = data.path;
    }
    const idea = data.idea_id ? blogIdeaById(data.idea_id) : null;
    if (idea) {
      idea.published = true;
      idea.published_at = data.published_at;
      idea.published_path = data.path;
    }

    const clipMsg =
      mode === "rich"  ? "html copied (paste into medium for proper formatting)"
    : mode === "plain" ? "markdown copied (rich-clipboard unavailable)"
    :                    "clipboard blocked — open the .md file";
    toast(`archived → ${data.path.split("/").pop()} · ${clipMsg} · medium opened ✓`, "ok");
    renderStudio();
  } catch (e) {
    toast(`publish error: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    lbl.textContent = prev;
  }
}

async function regenStudioAll(ideaId, btn) {
  if (!confirm("regenerate BOTH the draft and 10 titles? current draft will be archived to versions.")) return;
  btn.disabled = true;
  const lbl = btn.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "regenerating both…";
  setOverlay(true);
  try {
    await Promise.all([
      generateVariations(ideaId, null),
      generateDraft(ideaId, null, null),
    ]);
    toast("regenerated draft + titles ✓", "ok");
  } finally {
    setOverlay(false);
    btn.disabled = false;
    lbl.textContent = prev;
    renderStudio();
  }
}


function setupBlog() {
  blogEls.scrape.addEventListener("click", scrapeMedium);
  blogEls.generate.addEventListener("click", generateIdeasNow);
  blogEls.research.addEventListener("click", researchTrendingNow);
  blogEls.clear.addEventListener("click", clearIdeasList);
  blogEls.projectAdd.addEventListener("click", addProject);
  blogEls.projectPath.addEventListener("keydown", (e) => { if (e.key === "Enter") addProject(); });
  blogEls.projectName.addEventListener("keydown", (e) => { if (e.key === "Enter") addProject(); });

  blogEls.editAgent.addEventListener("click", () => {
    blogEls.agentBlock.scrollIntoView({ behavior: "smooth", block: "start" });
    blogEls.agentText.focus();
  });

  blogEls.agentText.addEventListener("input", () => {
    const v = blogEls.agentText.value;
    blogEls.agentBytes.textContent = `${v.length} bytes`;
    setBlogAgentDirty(v !== blogState.agentSaved);
  });

  blogEls.agentDiscard.addEventListener("click", () => {
    if (blogEls.agentText.value === blogState.agentSaved) return;
    blogEls.agentText.value = blogState.agentSaved;
    blogEls.agentBytes.textContent = `${blogState.agentSaved.length} bytes`;
    setBlogAgentDirty(false);
    toast("changes discarded", "");
  });

  blogEls.agentSave.addEventListener("click", async () => {
    const content = blogEls.agentText.value;
    if (content === blogState.agentSaved) return;
    blogEls.agentSave.disabled = true;
    const lbl = blogEls.agentSave.querySelector("span:last-child");
    const prev = lbl.textContent; lbl.textContent = "saving…";
    try {
      const res = await fetch("/blog-agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await blogJson(res);
      if (data.ok) {
        blogState.agentSaved = content;
        blogState.agentMtime = data.mtime || blogState.agentMtime;
        blogEls.agentMtime.textContent = data.mtime ? relTime(data.mtime) : blogEls.agentMtime.textContent;
        setBlogAgentDirty(false);
        toast("saved ✓", "ok");
      } else {
        toast(`save failed: ${data.error || "?"}`, "error");
        blogEls.agentSave.disabled = false;
      }
    } catch (e) {
      toast(`save error: ${e.message}`, "error");
      blogEls.agentSave.disabled = false;
    } finally {
      lbl.textContent = prev;
    }
  });
}

// ─────────────────── linkedin (10 ideas · 11 drafts · agent tab) ───────────────────

const liEls = {
  refresh:      $("#li-refresh"),
  themes:       $("#li-themes"),
  themesMeta:   $("#li-themes-meta"),
  ideaGrid:     $("#li-idea-grid"),
  ideasEmpty:   $("#li-ideas-empty"),
  draftGrid:    $("#li-draft-grid"),
  draftsEmpty:  $("#li-drafts-empty"),
  navIdeas:     document.querySelector('[data-count="linkedin-ideas"]'),
  navDrafts:    document.querySelector('[data-count="linkedin-drafts"]'),
  // agent tab
  agentText:    $("#li-agent-textarea"),
  agentSave:    $("#li-agent-save"),
  agentDiscard: $("#li-agent-discard"),
  agentRemine:  $("#li-agent-remine"),
  agentBytes:   $("#li-agent-bytes"),
  agentMtime:   $("#li-agent-mtime"),
  agentPath:    $("#li-agent-path"),
  agentDirty:   $("#li-agent-dirty"),
};

const liState = {
  themes:     [],
  ideas:      [],
  drafts:     [],
  loading:    false,
  agentSaved: "",
  agentLoaded:false,
};

async function loadLinkedin() {
  if (liState.loading) return;
  liState.loading = true;
  try {
    const res  = await fetch("/linkedin/data", { cache: "no-store" });
    const data = await blogJson(res);
    liState.themes = data.themes || [];
    liState.ideas  = data.ideas  || [];
    liState.drafts = data.drafts || [];
    renderLinkedinIdeas();
    renderLinkedinDrafts();
  } catch (e) {
    toast(`linkedin load failed: ${e.message}`, "error");
  } finally {
    liState.loading = false;
  }
}

function renderLinkedinIdeas() {
  // theme chips
  liEls.themes.innerHTML = "";
  (liState.themes || []).forEach(t => liEls.themes.appendChild(chip(t)));
  liEls.themesMeta.textContent = liState.themes.length
    ? `${liState.themes.length} mined themes` : "no themes yet";

  // idea cards
  liEls.ideaGrid.innerHTML = "";
  const drafted = new Set(liState.drafts.map(d => d.idea_id));
  if (liEls.navIdeas) liEls.navIdeas.textContent = String(liState.ideas.length || "—");

  if (!liState.ideas.length) {
    liEls.ideasEmpty.classList.remove("hidden");
    return;
  }
  liEls.ideasEmpty.classList.add("hidden");

  liState.ideas.forEach(idea => {
    const hasDraft = drafted.has(idea.id) || idea.status === "drafted";
    const sourceClass = (idea.source || "linkedin").toLowerCase().replace(/[^a-z0-9-]/g, "-");
    const card = el("div", { class: "idea-card", "data-id": idea.id }, [
      el("div", { class: "idea-meta" }, [
        el("span", { class: `idea-source ${sourceClass}` }, idea.source || "linkedin"),
      ]),
      el("h3", { class: "idea-title" }, idea.angle || "(no angle)"),
      idea.why_valuable
        ? el("div", { class: "idea-angle" }, `▸ ${idea.why_valuable}`)
        : null,
      el("div", { class: "idea-actions" }, [
        (() => {
          const b = el("button", {
            class: `btn ${hasDraft ? "ghost" : "primary"}`,
            title: hasDraft ? "regenerate the full post for this idea" : "draft a full linkedin post from this idea",
          }, [
            el("span", { class: "btn-key" }, "✎"),
            el("span", {}, hasDraft ? "rewrite post" : "write full post"),
          ]);
          b.addEventListener("click", () => writeLinkedinPost(idea.id, b));
          return b;
        })(),
        hasDraft ? el("span", { class: "agent-meta-pill", title: "this idea has a draft" }, "▸ draft") : null,
        (() => {
          const b = el("button", { class: "btn ghost", title: "discard this idea" }, [
            el("span", { class: "btn-key" }, "✕"), el("span", {}, "discard"),
          ]);
          b.addEventListener("click", () => discardLinkedinIdea(idea.id));
          return b;
        })(),
      ]),
    ]);
    liEls.ideaGrid.appendChild(card);
  });
}

function renderLinkedinDrafts() {
  liEls.draftGrid.innerHTML = "";
  if (liEls.navDrafts) liEls.navDrafts.textContent = String(liState.drafts.length || "—");

  if (!liState.drafts.length) {
    liEls.draftsEmpty.classList.remove("hidden");
    return;
  }
  liEls.draftsEmpty.classList.add("hidden");

  liState.drafts.forEach(draft => liEls.draftGrid.appendChild(makeLinkedinDraftCard(draft)));
}

function makeLinkedinDraftCard(draft) {
  const wrap = el("div", { class: "draft li-draft", "data-id": draft.id });
  if (draft.status === "posted") wrap.classList.add("posted");

  if (draft.why_valuable) {
    wrap.appendChild(el("div", { class: "target" }, [
      el("span", { class: "target-author" }, "why valuable: "),
      el("span", {}, draft.why_valuable),
    ]));
  }

  const textarea = el("textarea", { rows: "10" });
  textarea.value = draft.text || "";
  wrap.appendChild(textarea);

  const counter = el("span", { class: "char-count" });
  const updateCount = () => { counter.textContent = `${textarea.value.length} chars`; };
  textarea.addEventListener("input", updateCount);
  updateCount();

  // inline pane-hidden hint (revealed when compose returns pane_hidden)
  const hint = el("div", { class: "empty hidden", style: "margin-top:10px; text-align:left;" });

  const saveBtn = el("button", { class: "btn ghost", title: "save edits (marks approved)" }, [
    el("span", { class: "btn-key" }, "⏎"), el("span", {}, "save"),
  ]);
  const composeBtn = el("button", { class: "btn primary", title: "pre-fill the linkedin composer (you click Post)" }, [
    el("span", { class: "btn-key" }, "↗"), el("span", {}, "open in composer"),
  ]);
  const postedBtn = el("button", { class: "btn ghost", title: "mark this draft as posted" }, [
    el("span", { class: "btn-key" }, "✓"), el("span", {}, "mark as posted"),
  ]);
  const discardBtn = el("button", { class: "btn ghost danger", title: "discard this draft" }, "discard");
  const thumbBtn = el("button", { class: "btn ghost", title: "generate a 16:9 thumbnail for this post via ChatGPT" }, [
    el("span", { class: "btn-key" }, "🖼"),
    el("span", {}, draft.thumbnail_path ? "regenerate thumbnail" : "generate thumbnail"),
  ]);

  // thumbnail preview (shown once generated; auto-attaches on "open in composer")
  const thumbName = (p) => (p || "").split("/").pop();
  const thumbPreview = el("div", { class: "li-thumb hidden" });
  const thumbImg = el("img", { alt: "thumbnail", loading: "lazy" });
  thumbPreview.appendChild(thumbImg);
  if (draft.thumbnail_path) {
    thumbImg.src = `/linkedin-thumbnails/${thumbName(draft.thumbnail_path)}`;
    thumbPreview.classList.remove("hidden");
  }

  const allBtns = [saveBtn, composeBtn, postedBtn, discardBtn, thumbBtn];
  const setDisabled = (v) => allBtns.forEach(b => b.disabled = v);

  saveBtn.addEventListener("click", async () => {
    const text = textarea.value.trim();
    if (!text) { toast("empty draft", "error"); return; }
    setDisabled(true);
    const lbl = saveBtn.querySelector("span:last-child");
    const prev = lbl.textContent; lbl.textContent = "saving…";
    try {
      const res = await fetch("/linkedin/draft/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: draft.id, text }),
      });
      const data = await blogJson(res);
      if (data.error) { toast(`save failed: ${data.error}`, "error"); }
      else {
        draft.text = text; draft.status = "approved";
        toast("saved ✓ (approved)", "ok");
      }
    } catch (e) {
      toast(`save error: ${e.message}`, "error");
    } finally {
      setDisabled(false); lbl.textContent = prev;
    }
  });

  composeBtn.addEventListener("click", async () => {
    // persist current edits first so the composer fills the latest text
    const text = textarea.value.trim();
    if (!text) { toast("empty draft", "error"); return; }
    hint.classList.add("hidden");
    setDisabled(true);
    const lbl = composeBtn.querySelector("span:last-child");
    const prev = lbl.textContent; lbl.textContent = "opening composer…";
    try {
      if (text !== draft.text) {
        await fetch("/linkedin/draft/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: draft.id, text }),
        });
        draft.text = text;
      }
      const res = await fetch("/linkedin/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: draft.id }),
      });
      const data = await blogJson(res);
      if (data.ok) {
        if (data.thumbnail_attached) {
          toast("composer pre-filled + thumbnail attached ✓ — review and click Post", "ok");
        } else if (data.thumbnail_hint) {
          hint.textContent = data.thumbnail_hint;
          hint.classList.remove("hidden");
          toast("composer pre-filled ✓ — finish the thumbnail (⌘V) then Post", "ok");
        } else {
          toast("composer pre-filled ✓ — review and click Post in linkedin", "ok");
        }
      } else if (data.reason === "pane_hidden") {
        hint.textContent = data.hint || "Bring your LinkedIn pane to the front in cmux, then retry.";
        hint.classList.remove("hidden");
        toast("linkedin pane is hidden — bring it to the front, then retry", "error");
      } else {
        toast(`compose failed: ${data.hint || data.reason || "?"}`, "error");
      }
    } catch (e) {
      toast(`compose error: ${e.message}`, "error");
    } finally {
      setDisabled(false); lbl.textContent = prev;
    }
  });

  postedBtn.addEventListener("click", async () => {
    setDisabled(true);
    try {
      const res = await fetch("/linkedin/mark-posted", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: draft.id }),
      });
      const data = await blogJson(res);
      if (data.error) { toast(`failed: ${data.error}`, "error"); setDisabled(false); }
      else {
        draft.status = "posted";
        wrap.classList.add("posted");
        textarea.disabled = true;
        toast("marked as posted ✓", "ok");
      }
    } catch (e) {
      toast(`error: ${e.message}`, "error"); setDisabled(false);
    }
  });

  discardBtn.addEventListener("click", async () => {
    if (!confirm("discard this draft?")) return;
    setDisabled(true);
    try {
      const res = await fetch(`/linkedin/drafts/${encodeURIComponent(draft.id)}`, { method: "DELETE" });
      const data = await blogJson(res);
      if (data.ok || data.error == null) {
        liState.drafts = liState.drafts.filter(d => d.id !== draft.id);
        renderLinkedinDrafts();
        renderLinkedinIdeas();
        toast("discarded", "");
      } else {
        toast(`discard failed: ${data.error}`, "error"); setDisabled(false);
      }
    } catch (e) {
      toast(`discard error: ${e.message}`, "error"); setDisabled(false);
    }
  });

  thumbBtn.addEventListener("click", async () => {
    setDisabled(true);
    const lbl = thumbBtn.querySelector("span:last-child");
    const prev = lbl.textContent; lbl.textContent = "generating… (30-90s)";
    try {
      const res = await fetch("/linkedin/thumbnail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: draft.id }),
      });
      const data = await blogJson(res);
      if (data.ok) {
        draft.thumbnail_path = data.path;
        thumbImg.src = `${data.url}?t=${Date.now()}`;
        thumbPreview.classList.remove("hidden");
        lbl.textContent = "regenerate thumbnail";
        toast("thumbnail generated ✓ — it attaches when you open in composer", "ok");
      } else {
        toast(`thumbnail failed: ${data.error || "?"}`, "error");
        lbl.textContent = prev;
      }
    } catch (e) {
      toast(`thumbnail error: ${e.message}`, "error");
      lbl.textContent = prev;
    } finally {
      setDisabled(false);
    }
  });

  wrap.appendChild(thumbPreview);
  wrap.appendChild(el("div", { class: "actions" }, [counter, discardBtn, thumbBtn, postedBtn, saveBtn, composeBtn]));
  wrap.appendChild(hint);
  return wrap;
}

async function refreshLinkedin() {
  liEls.refresh.disabled = true;
  const lbl = liEls.refresh.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "mining + drafting… (30-90s)";
  setOverlay(true);
  try {
    const res = await fetch("/linkedin/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await blogJson(res);
    if (data.error) { toast(`refresh failed: ${data.error}`, "error"); return; }
    liState.themes = data.themes || [];
    liState.ideas  = data.ideas  || [];
    liState.drafts = data.drafts || [];
    renderLinkedinIdeas();
    renderLinkedinDrafts();
    toast(`mined ${liState.themes.length} themes · ${liState.ideas.length} ideas ✓`, "ok");
  } catch (e) {
    toast(`refresh error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    liEls.refresh.disabled = false;
    lbl.textContent = prev;
  }
}

async function writeLinkedinPost(ideaId, btn) {
  btn.disabled = true;
  const lbl = btn.querySelector("span:last-child");
  const prev = lbl.textContent; lbl.textContent = "drafting… (30-60s)";
  setOverlay(true);
  try {
    const res = await fetch("/linkedin/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idea_id: ideaId }),
    });
    const data = await blogJson(res);
    if (data.error) { toast(`draft failed: ${data.error}`, "error"); return; }
    const idx = liState.drafts.findIndex(d => d.id === data.id);
    if (idx >= 0) liState.drafts[idx] = data; else liState.drafts.push(data);
    const idea = liState.ideas.find(i => i.id === ideaId);
    if (idea) idea.status = "drafted";
    renderLinkedinIdeas();
    renderLinkedinDrafts();
    toast("full post drafted ✓ — see 11 linkedin drafts", "ok");
  } catch (e) {
    toast(`draft error: ${e.message}`, "error");
  } finally {
    setOverlay(false);
    btn.disabled = false; lbl.textContent = prev;
  }
}

async function discardLinkedinIdea(ideaId) {
  if (!confirm("discard this idea?")) return;
  try {
    const res = await fetch(`/linkedin/ideas/${encodeURIComponent(ideaId)}`, { method: "DELETE" });
    const data = await blogJson(res);
    if (data.ok || data.error == null) {
      liState.ideas = liState.ideas.filter(i => i.id !== ideaId);
      renderLinkedinIdeas();
      toast("discarded", "");
    } else {
      toast(`discard failed: ${data.error}`, "error");
    }
  } catch (e) {
    toast(`discard error: ${e.message}`, "error");
  }
}

// ── linkedin-voice agent (tab inside section 07) ──

function setLiAgentDirty(dirty) {
  liEls.agentDirty.classList.toggle("hidden", !dirty);
  liEls.agentSave.disabled = !dirty;
}

async function loadLinkedinAgent() {
  try {
    const res  = await fetch("/linkedin-agent", { cache: "no-store" });
    const data = await blogJson(res);
    liState.agentSaved = data.content || "";
    liState.agentLoaded = true;
    liEls.agentText.value = liState.agentSaved;
    liEls.agentPath.textContent  = data.path || (data.error ? "(not found)" : "—");
    liEls.agentMtime.textContent = data.mtime ? relTime(data.mtime) : "—";
    liEls.agentBytes.textContent = `${liEls.agentText.value.length} bytes`;
    setLiAgentDirty(false);
    if (data.error) toast(`linkedin agent: ${data.error}`, "");
  } catch (e) {
    toast(`linkedin agent load error: ${e.message}`, "error");
  }
}

function setupLinkedin() {
  if (liEls.refresh) liEls.refresh.addEventListener("click", refreshLinkedin);

  if (liEls.agentText) {
    liEls.agentText.addEventListener("input", () => {
      liEls.agentBytes.textContent = `${liEls.agentText.value.length} bytes`;
      setLiAgentDirty(liEls.agentText.value !== liState.agentSaved);
    });
  }

  if (liEls.agentDiscard) {
    liEls.agentDiscard.addEventListener("click", () => {
      if (liEls.agentText.value === liState.agentSaved) return;
      liEls.agentText.value = liState.agentSaved;
      liEls.agentBytes.textContent = `${liState.agentSaved.length} bytes`;
      setLiAgentDirty(false);
      toast("changes discarded", "");
    });
  }

  if (liEls.agentSave) {
    liEls.agentSave.addEventListener("click", async () => {
      const content = liEls.agentText.value;
      if (content === liState.agentSaved) return;
      liEls.agentSave.disabled = true;
      const lbl = liEls.agentSave.querySelector("span:last-child");
      const prev = lbl.textContent; lbl.textContent = "saving…";
      try {
        const res = await fetch("/linkedin-agent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        const data = await blogJson(res);
        if (data.ok) {
          liState.agentSaved = content;
          setLiAgentDirty(false);
          toast("saved ✓", "ok");
        } else {
          toast(`save failed: ${data.error || "?"}`, "error");
          liEls.agentSave.disabled = false;
        }
      } catch (e) {
        toast(`save error: ${e.message}`, "error");
        liEls.agentSave.disabled = false;
      } finally {
        lbl.textContent = prev;
      }
    });
  }

  if (liEls.agentRemine) {
    liEls.agentRemine.addEventListener("click", async () => {
      liEls.agentRemine.disabled = true;
      const lbl = liEls.agentRemine.querySelector("span:last-child");
      const prev = lbl.textContent; lbl.textContent = "re-mining posts… (20-40s)";
      setOverlay(true);
      try {
        const res = await fetch("/linkedin-agent/remine", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const data = await blogJson(res);
        if (data.ok) {
          toast(`re-mined ${data.count || 0} posts into the agent ✓`, "ok");
          await loadLinkedinAgent();
        } else {
          toast(`re-mine failed: ${data.reason || data.error || "?"}`, "error");
        }
      } catch (e) {
        toast(`re-mine error: ${e.message}`, "error");
      } finally {
        setOverlay(false);
        liEls.agentRemine.disabled = false;
        lbl.textContent = prev;
      }
    });
  }
}

document.getElementById("analytics-run")?.addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true; btn.textContent = "analyzing…";
  try { await fetch("/analytics/run", { method: "POST" }); await loadAnalytics(); }
  finally { btn.disabled = false; btn.textContent = "run analysis now"; }
});

const evalRunBtn = $("#eval-run-btn");
if (evalRunBtn) {
  evalRunBtn.addEventListener("click", async () => {
    evalRunBtn.disabled = true;
    evalRunBtn.textContent = "running…";
    try {
      const res = await (await fetch("/eval/run", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: "{}" })).json();
      if (res.skipped) { toast(`eval skipped (${res.skipped})`, ""); loadEvals(); return; }
      loadEvals();
      if (!res.voice_changed) { toast("eval done ✓ (voice unchanged)", "ok"); return; }
      // Voice changed → the current drafts were written by the old voice. Clear
      // them and regenerate a fresh set to review/post/discard against the new
      // voice (that feedback feeds the next eval). Reuse refresh() so the user
      // gets the same full-screen overlay + render the main refresh button uses
      // — the regen takes a few minutes and must look like it's working.
      toast("voice updated — regenerating all drafts…", "ok");
      showSection("drafts");                  // land on the new set once it renders
      await refresh();                        // overlay + POST /refresh + render + error handling
    } catch (e) {
      toast(`eval failed: ${e.message}`, "error");
    } finally {
      evalRunBtn.disabled = false;
      evalRunBtn.textContent = "run eval now";
    }
  });
}

// initial
setupAgent();
setupCompose();
setupBlog();
setupLinkedin();
setupThumbModal();
const initialHash = (location.hash || "#drafts").slice(1);
if (SECTION_META[initialHash]) showSection(initialHash);
pingServer().then(() => load());
