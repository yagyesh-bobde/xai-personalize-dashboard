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
  const meta = SECTION_META[name] || { title: name, sub: "" };
  els.sectionTitle.textContent = meta.title;
  els.sectionSub.textContent = meta.sub;
  if (name === "scheduled") loadScheduled();
  if (name === "history")   loadHistory();
  if (name === "agent")     loadAgent();
  if (name === "drafts" || name === "compose") fetchQueuePreview();
}

$$(".nav-item").forEach(n => {
  n.addEventListener("click", (e) => {
    e.preventDefault();
    showSection(n.dataset.section);
    history.replaceState(null, "", `#${n.dataset.section}`);
  });
});

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

  const allBtns = [postBtn, schedBtn, queueBtn, discardBtn];
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
  discardBtn.addEventListener("click", () => {
    wrap.classList.add("discarded");
    textarea.disabled = true;
    setDisabled(true);
    queueButtons.delete(queueBtn);
  });

  wrap.appendChild(el("div", { class: "actions" }, [uploadLabel, counter, discardBtn, postBtn, schedBtn, queueBtn]));
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

// initial
setupAgent();
setupCompose();
const initialHash = (location.hash || "#foryou").slice(1);
if (SECTION_META[initialHash]) showSection(initialHash);
pingServer().then(() => load());
