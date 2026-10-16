/* Grey Mirror offline viewer. Works from file:// — data arrives as <script> chunks, no fetch. */
(function () {
  "use strict";
  const INDEX = window.GM_INDEX;
  const chunks = {}; // threadId -> { n: messages[] }
  const waiters = {};
  window.GM_chunk = function (tid, n, messages) {
    (chunks[tid] = chunks[tid] || {})[n] = messages;
    const key = tid + ":" + n;
    (waiters[key] || []).forEach((fn) => fn(messages));
    delete waiters[key];
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  const shortFmt = new Intl.DateTimeFormat(undefined, { year: "2-digit", month: "numeric", day: "numeric" });
  const fmtDay = (ts) => (ts ? dayFmt.format(new Date(ts)) : "Undated");

  function loadChunk(tid, n) {
    if (chunks[tid] && chunks[tid][n]) return Promise.resolve(chunks[tid][n]);
    return new Promise((resolve, reject) => {
      const key = tid + ":" + n;
      (waiters[key] = waiters[key] || []).push(resolve);
      const s = document.createElement("script");
      s.src = "viewer/t/" + tid + "-" + n + ".js";
      s.onerror = () => reject(new Error("Missing data file " + s.src));
      document.body.appendChild(s);
    });
  }

  // ---- sidebar ---------------------------------------------------------------------------
  const shell = $(".shell");
  const list = $(".threads");
  $(".side h1").textContent = INDEX.label;
  $(".side .source").textContent = INDEX.subtitle;
  document.title = INDEX.label + " — Messages";

  function renderList(filter) {
    list.textContent = "";
    const q = (filter || "").toLowerCase();
    INDEX.threads
      .filter((t) => !q || t.title.toLowerCase().includes(q) || (t.addresses || "").toLowerCase().includes(q))
      .forEach((t) => {
        const li = el("li");
        const b = el("button");
        b.dataset.id = t.id;
        b.setAttribute("aria-current", String(current && current.id === t.id));
        b.append(el("span", "t-title", t.title), el("span", "t-date", t.last ? shortFmt.format(new Date(t.last)) : ""),
          el("span", "t-preview", t.preview || t.count.toLocaleString() + (t.count === 1 ? " message" : " messages")));
        b.addEventListener("click", () => openThread(t));
        li.append(b);
        list.append(li);
      });
  }
  $(".filter").addEventListener("input", (e) => renderList(e.target.value));

  // ---- conversation ----------------------------------------------------------------------
  const stream = $(".stream");
  let current = null;
  let lowest = 0; // lowest chunk index rendered
  let lastDay = null;
  let loading = false;
  let needle = "";

  const URL_RE = /\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]'"]/g;
  function richText(text, into) {
    let last = 0;
    text.replace(URL_RE, (m, i) => {
      appendHighlighted(into, text.slice(last, i));
      const a = el("a", null, m);
      a.href = m;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      into.append(a);
      last = i + m.length;
      return m;
    });
    appendHighlighted(into, text.slice(last));
  }
  function appendHighlighted(into, s) {
    if (!needle || !s) return into.append(s);
    const lower = s.toLowerCase();
    let i = 0, j;
    while ((j = lower.indexOf(needle, i)) !== -1) {
      into.append(s.slice(i, j), el("mark", null, s.slice(j, j + needle.length)));
      i = j + needle.length;
    }
    into.append(s.slice(i));
  }

  function attachmentNode(a) {
    if (!a.f) {
      const span = el("span", "file missing", "⌁ " + (a.n || "Attachment") + " (not on device)");
      return span;
    }
    const src = a.p || a.f;
    const mime = a.m || "";
    if (mime.startsWith("image/") || a.p) {
      const link = el("a");
      link.href = a.f;
      link.target = "_blank";
      const img = el("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = a.n || "Image";
      img.src = src;
      link.append(img);
      return link;
    }
    if (mime.startsWith("video/")) {
      const v = el("video");
      v.controls = true;
      v.preload = "none";
      v.src = a.f;
      return v;
    }
    if (mime.startsWith("audio/") && !/caf|amr/.test(mime)) {
      const au = el("audio");
      au.controls = true;
      au.preload = "none";
      au.src = a.f;
      return au;
    }
    const f = el("a", "file", "↓ " + (a.n || "File"));
    f.href = a.f;
    f.download = a.n || "";
    return f;
  }

  function messageNode(m, prev) {
    const frag = document.createDocumentFragment();
    const day = m.t ? fmtDay(m.t) : "Undated";
    if (day !== lastDay) {
      frag.append(el("div", "day", day));
      lastDay = day;
    }
    if (m.k === "system") {
      frag.append(el("div", "sys", (m.y || "") + (m.t ? " · " + timeFmt.format(new Date(m.t)) : "")));
      return frag;
    }
    const wrap = el("div", "msg" + (m.me ? " me" : ""));
    const showWho = current.group && !m.me && (!prev || prev.s !== m.s || prev.me || prev.k === "system");
    if (showWho) wrap.append(el("div", "who", m.s));
    if (m.r) wrap.append(el("div", "quote", "↩ " + m.r.s + ": " + (m.r.x || "")));
    const atts = m.a || [];
    if (atts.length) {
      const media = el("div", "media");
      atts.forEach((a) => media.append(attachmentNode(a)));
      wrap.append(media);
    }
    if (m.k === "unsent") {
      wrap.append(el("div", "bubble unsent", (m.me ? "You" : m.s) + " unsent a message"));
    } else if (m.x || m.sub) {
      const b = el("div", "bubble");
      if (m.sub) b.append(el("span", "subject", m.sub));
      if (m.x) richText(m.x, b);
      wrap.append(b);
    }
    if (m.re && m.re.length) {
      const r = el("div", "reacts");
      m.re.forEach((x) => {
        const chip = el("span", "react", x.e || "•");
        chip.title = x.s + " reacted " + (x.e || x.k);
        r.append(chip);
      });
      wrap.append(r);
    }
    const meta = el("div", "meta");
    const bits = [];
    if (m.t) bits.push(timeFmt.format(new Date(m.t)));
    if (m.svc && m.svc !== "iMessage") bits.push(m.svc);
    if (m.fx) bits.push("Sent with " + m.fx);
    if (m.app && m.app !== "link") bits.push(m.app);
    meta.append(bits.join(" · "));
    if (m.ed) {
      meta.append(" · ");
      const btn = el("button", null, "Edited");
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", () => {
        const open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!open));
        if (open) return btn._panel && btn._panel.remove();
        const panel = el("div", "edits");
        const ol = el("ol");
        m.ed.forEach((e) => ol.append(el("li", null, e.text + (e.ts ? "  (" + timeFmt.format(new Date(e.ts)) + ")" : ""))));
        panel.append(el("div", null, "Edit history"), ol);
        btn._panel = panel;
        wrap.append(panel);
      });
      meta.append(btn);
    }
    wrap.append(meta);
    frag.append(wrap);
    return frag;
  }

  function renderChunk(messages) {
    const box = el("div", "chunk");
    lastDay = null;
    let prev = null;
    messages.forEach((m) => {
      box.append(messageNode(m, prev));
      prev = m;
    });
    return box;
  }

  async function openThread(t) {
    current = t;
    needle = "";
    $(".find").value = "";
    shell.classList.add("open");
    list.querySelectorAll("button").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.id) === t.id)));
    $(".convo-head h2").textContent = t.title;
    const range = [t.first && shortFmt.format(new Date(t.first)), t.last && shortFmt.format(new Date(t.last))].filter(Boolean).join(" – ");
    $(".convo-head p").textContent = t.count.toLocaleString() + (t.count === 1 ? " message" : " messages") + (range ? " · " + range : "") +
      (t.addresses ? " · " + t.addresses : "");
    stream.textContent = "";
    lowest = t.chunks - 1;
    const msgs = await loadChunk(t.id, lowest);
    if (current !== t) return;
    stream.append(renderChunk(msgs));
    pinToBottom();
    if (stream.scrollHeight <= stream.clientHeight) loadOlder();
  }

  // Keep the newest message in view while photos finish loading, until the reader scrolls.
  let pinned = false;
  const pinObserver = new ResizeObserver(() => {
    if (pinned) stream.scrollTop = stream.scrollHeight;
  });
  function pinToBottom() {
    pinned = true;
    stream.scrollTop = stream.scrollHeight;
    pinObserver.disconnect();
    if (stream.firstElementChild) pinObserver.observe(stream.lastElementChild);
    setTimeout(() => (pinned = false), 4000);
  }
  stream.addEventListener("wheel", () => (pinned = false), { passive: true });
  stream.addEventListener("touchstart", () => (pinned = false), { passive: true });
  stream.addEventListener("keydown", () => (pinned = false));

  async function loadOlder() {
    if (!current || loading || lowest <= 0) return;
    loading = true;
    const t = current;
    const n = lowest - 1;
    const msgs = await loadChunk(t.id, n);
    if (current !== t) return (loading = false);
    const before = stream.scrollHeight;
    stream.prepend(renderChunk(msgs));
    stream.scrollTop += stream.scrollHeight - before;
    lowest = n;
    loading = false;
  }
  stream.addEventListener("scroll", () => {
    if (stream.scrollTop < 600) loadOlder();
  }, { passive: true });

  // Find in conversation: load everything once, re-render with highlights, jump to the latest hit.
  let findTimer;
  $(".find").addEventListener("input", (e) => {
    clearTimeout(findTimer);
    findTimer = setTimeout(async () => {
      if (!current) return;
      needle = e.target.value.trim().toLowerCase();
      const t = current;
      const all = [];
      for (let n = 0; n < t.chunks; n++) all.push(...(await loadChunk(t.id, n)));
      if (current !== t) return;
      stream.textContent = "";
      lowest = 0;
      const shown = needle ? all.filter((m) => ((m.x || "") + " " + (m.sub || "")).toLowerCase().includes(needle)) : all;
      if (needle && !shown.length) {
        const empty = el("div", "empty");
        empty.append(el("strong", null, "No matches"), "Nothing in this conversation contains “" + e.target.value.trim() + "”.");
        stream.append(empty);
        return;
      }
      stream.append(renderChunk(shown));
      pinToBottom();
    }, 220);
  });
  $(".back").addEventListener("click", () => shell.classList.remove("open"));

  renderList("");
  if (INDEX.threads.length && window.matchMedia("(min-width: 761px)").matches) openThread(INDEX.threads[0]);
})();
