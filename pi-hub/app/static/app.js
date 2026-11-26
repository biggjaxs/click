/* Click chat app (plain JavaScript, no build step). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(c));
  return node;
};

if (new URLSearchParams(location.search).has("tile")) {
  document.body.classList.add("tile");
  document.documentElement.dataset.theme = "dark";   // the floating window is always dark glass, like the orb
}
hydrateIcons();
const ib = (name, title, onclick, cls = "icon-btn") => { const b = el("button", { class: cls, type: "button", title, "aria-label": title, onclick }); b.append(icon(name)); return b; };
const withIcon = (name, text) => [icon(name), text];

const state = {
  ws: null, status: null, settings: {}, conversationId: null, conversations: [],
  busy: false, current: null, uploads: [], incognito: false, reconnectDelay: 1000,
};

// ---------- API helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  if (res.status === 401) { location.href = "/login"; throw new Error("Please log in"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

function toast(text, ms = 3500, iconName = null) {
  const t = el("div", { class: "toast" }, iconName ? icon(iconName) : null, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

// ---------- Markdown ----------
marked.setOptions({ gfm: true, breaks: true });
function renderMarkdown(target, text) {
  target.innerHTML = DOMPurify.sanitize(marked.parse(text || ""), { ADD_ATTR: ["target"] });
  target.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
  target.querySelectorAll("pre code").forEach(code => {
    try { hljs.highlightElement(code); } catch {}
    const pre = code.parentElement;
    const btn = el("button", { class: "copy-btn", type: "button", text: "Copy" });
    btn.onclick = async () => {
      try { await navigator.clipboard.writeText(code.innerText); btn.textContent = "Copied"; }
      catch { btn.textContent = "Select & copy"; }
      setTimeout(() => (btn.textContent = "Copy"), 1500);
    };
    pre.append(btn);
  });
}

// ---------- Theme / look ----------
function applySettings(s) {
  state.settings = s;
  const root = document.documentElement;
  root.style.setProperty("--accent", s.accent_color || "#7c3aed");
  if (document.body.classList.contains("tile")) root.dataset.theme = "dark";
  else if (s.theme === "light" || s.theme === "dark") root.dataset.theme = s.theme; else delete root.dataset.theme;
  root.dataset.font = s.font_size || "medium";
  root.dataset.chatStyle = s.chat_style || "bubbles";
  const dark = s.theme === "dark" || (s.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  $("#hljs-dark").disabled = !dark;
  $("#hljs-light").disabled = dark;
  const name = s.assistant_name || "Click";
  $("#brand-name").textContent = name;
  document.querySelectorAll(".assistant-name").forEach(n => (n.textContent = name));
  $("#input").placeholder = `Message ${name}…`;
  document.title = name;
}

// ---------- Status ----------
function applyStatus(st) {
  state.status = st;
  if (st.settings) applySettings(st.settings);
  const pc = st.pc || {};
  const pill = $("#pill-pc");
  const local = pc.pc_name && st.settings && st.answering_source === "pc";
  const [cls, label] = pc.connected ? ["ok", local ? "Ready" : `${pc.pc_name} connected`]
    : pc.paired ? ["warn", "Computer offline"] : ["", "No computer paired"];
  pill.replaceChildren(el("span", { class: "dot " + cls }), el("span", { text: label }));
  $("#pill-watch").classList.toggle("hidden", !st.watch_mode);
  $("#pill-paused").classList.toggle("hidden", !st.paused);
  const pause = $("#pause-btn");
  pause.replaceChildren(icon(st.paused ? "play" : "pause"), el("span", { text: st.paused ? "Resume everything" : "Pause everything" }));
  pause.classList.toggle("on", !!st.paused);
  $("#sb-model").textContent = st.answering_source === "pc"
    ? `${st.answering_model} on your computer`
    : `Backup model on the Pi (${st.answering_model})`;
  $("#sb-memory").textContent = st.memory_paused ? "Memory paused" : (st.encrypted ? "Memory encrypted" : "Memory on");
}

// ---------- WebSocket ----------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => { state.reconnectDelay = 1000; };
  ws.onmessage = ev => handleEvent(JSON.parse(ev.data));
  ws.onclose = ev => {
    if (ev.code === 4401) { location.href = "/login"; return; }
    $("#sb-model").textContent = "Reconnecting";
    if (state.busy) finishTurn();
    setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 10000);
  };
}
function send(obj) {
  if (state.ws && state.ws.readyState === 1) { state.ws.send(JSON.stringify(obj)); return true; }
  toast("Not connected to Click yet. Trying again…");
  return false;
}

// ---------- Messages ----------
function hideEmpty() { $("#empty-state")?.classList.add("hidden"); }
function scrollDown(force = false) {
  const m = $("#messages");
  if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 160) m.scrollTop = m.scrollHeight;
}

function addUserMessage(text, uploads = []) {
  hideEmpty();
  const bubble = el("div", { class: "bubble" });
  if (uploads.length) {
    const wrap = el("div", { class: "user-uploads" });
    for (const u of uploads) {
      if ((u.mime || "").startsWith("image/")) wrap.append(el("img", { src: `/api/uploads/${u.id}/file`, alt: u.filename }));
      else wrap.append(el("span", { class: "file-pill" }, icon("file"), u.filename));
    }
    bubble.append(wrap);
  }
  bubble.append(text);
  const node = el("div", { class: "msg user" }, bubble);
  $("#messages").append(node);
  scrollDown(true);
}

function newAssistantMessage() {
  hideEmpty();
  const extras = el("div", { class: "extras" });
  const steps = el("div", { class: "steps" });
  extras.append(steps);
  const content = el("div", { class: "content typing" });
  const meta = el("div", { class: "meta-line" });
  const bubble = el("div", { class: "bubble" }, extras, content, meta);
  const node = el("div", { class: "msg assistant" }, bubble);
  $("#messages").append(node);
  const msg = { node, extras, steps, content, meta, text: "", tools: {}, rendered: 0 };
  state.current = msg;
  scrollDown(true);
  return msg;
}

let renderTimer = null;
function scheduleRender(msg) {
  if (renderTimer) return;
  renderTimer = setTimeout(() => { renderTimer = null; renderMarkdown(msg.content, msg.text); msg.content.classList.add("typing"); scrollDown(); }, 50);
}

const TOOL_LABELS = {
  web_search: ["Searching the web", "Searched the web"], fetch_web_page: ["Reading a web page", "Read a web page"],
  generate_image: ["Making an image", "Made an image"], get_screen: ["Looking at your screen", "Looked at your screen"],
  read_screen_text: ["Reading the text on screen", "Read the text on screen"], list_windows: ["Checking open windows", "Checked open windows"],
  get_active_window: ["Checking the active window", "Checked the active window"], get_ui_elements: ["Reading the buttons", "Read the buttons"],
  find_ui_element: ["Finding it on screen", "Found it on screen"], list_folder: ["Looking in a folder", "Looked in a folder"],
  read_file: ["Reading a file", "Read a file"], search_files: ["Searching your files", "Searched your files"],
  rename_file: ["Renaming a file", "Renamed a file"], move_file: ["Moving a file", "Moved a file"],
  ghost_move: ["Pointing with my cursor", "Pointed with my cursor"], ghost_highlight: ["Highlighting", "Highlighted"],
  ghost_inspect: ["Checking under my cursor", "Checked under my cursor"], ghost_hide: ["Hiding my cursor", "Hid my cursor"],
  teach_step: ["Showing you the next step", "Showed you a step"], click: ["Clicking", "Clicked"], type_text: ["Typing", "Typed"],
  press_keys: ["Pressing keys", "Pressed keys"], scroll: ["Scrolling", "Scrolled"], remember: ["Saving to memory", "Saved to memory"],
  forget: ["Forgetting", "Forgot"], view_image: ["Looking at your image", "Looked at your image"],
};
const stepText = (name, done, caption) => !done && caption ? caption : (TOOL_LABELS[name] || [name, name])[done ? 1 : 0];
const fmtMs = ms => ms == null ? "" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

function imageCard(img) {
  const wrap = el("div", { class: "gen-image" });
  const image = el("img", { src: img.url, alt: img.prompt, loading: "lazy" });
  image.onclick = () => openImageViewer(img);
  const dl = el("a", { class: "icon-btn", href: img.url + "?download=true", download: `click-image-${img.id}.png`, title: "Download" });
  dl.append(icon("download"));
  wrap.append(image, el("div", { class: "actions" }, dl,
    ib("refresh", "Regenerate", () => { if (send({ type: "regenerate_image", image_id: img.id })) toast("Regenerating with a new seed"); }),
    el("span", { class: "seed", text: `seed ${img.params?.seed ?? "?"}` })));
  return wrap;
}

const progressCards = {};
function handleEvent(ev) {
  const msg = state.current;
  switch (ev.type) {
    case "status": applyStatus(ev); break;
    case "preview": showPreview(ev); break;
    case "conversation":
      state.conversationId = ev.id; loadConversations(); break;
    case "token":
      if (!msg) return;
      msg.text += ev.text; scheduleRender(msg); break;
    case "notice":
      (msg ? msg.extras : $("#messages")).append(el("div", { class: "notice" }, icon("info"), ev.text)); scrollDown(); break;
    case "tool": {
      if (!msg) return;
      let step = msg.tools[ev.name];
      if (!step || (ev.status === "running" && step.dataset.status !== "running")) {
        step = el("div", { class: "step" });
        step._t0 = performance.now();
        msg.tools[ev.name] = step;
        msg.steps.append(step);
      }
      step.dataset.status = ev.status;
      step.className = "step" + (ev.status === "failed" ? " failed" : "");
      if (ev.status === "running") {
        step.replaceChildren(el("span", { class: "spin" }), el("span", { text: stepText(ev.name, false, ev.caption) }));
      } else if (ev.status === "failed") {
        step.replaceChildren(icon("alert"), el("span", { text: `${stepText(ev.name, false)} failed: ${ev.error || ""}` }));
      } else {
        const took = fmtMs(Math.round(performance.now() - step._t0));
        const okI = icon("check"); okI.classList.add("ok-i");
        step.replaceChildren(okI, el("span", { text: `${stepText(ev.name, true)}  ·  ${took}` }));
      }
      scrollDown();
      break;
    }
    case "confirm_request": {
      const target = msg ? msg.extras : $("#messages");
      const labels = { once: "Allow once", task: "Allow for this chat", deny: "Deny" };
      const card = el("div", { class: "card confirm", id: `confirm-${ev.id}` },
        el("div", { class: "card-title" }, icon("lock"), ev.title),
        ev.detail ? el("div", { class: "card-detail", text: ev.detail }) : null,
        el("div", { class: "actions" }, ev.options.map(o => el("button", {
          class: "btn small" + (o === "once" ? " primary" : ""), type: "button", text: labels[o],
          onclick: () => send({ type: "confirm", id: ev.id, decision: o }),
        }))));
      target.append(card); scrollDown(true);
      break;
    }
    case "confirm_resolved": {
      const card = $(`#confirm-${ev.id}`);
      if (card) card.replaceWith(el("div", { class: "notice",
        text: ev.decision === "deny" ? "You said no." : (ev.decision === "task" ? "Allowed for this chat." : "Allowed once.") }));
      break;
    }
    case "image_progress": {
      let card = progressCards[ev.job_id];
      if (!card) {
        if (["done", "failed", "cancelled"].includes(ev.status)) break;
        const bar = el("div", { class: "progress indeterminate" }, el("div"));
        const label = el("div", { class: "item-sub grow", text: "Starting" });
        const cancel = el("button", { class: "btn small", type: "button", text: "Cancel",
          onclick: () => send({ type: "cancel_image", job_id: ev.job_id }) });
        card = el("div", { class: "card" }, el("div", { class: "card-title" }, icon("image"), "Making an image"),
          el("div", { class: "card-detail", text: ev.prompt || "" }), bar, el("div", { class: "row" }, label, cancel));
        card._bar = bar; card._label = label;
        progressCards[ev.job_id] = card;
        (msg ? msg.extras : $("#messages")).append(card);
      }
      if (ev.status === "done" || ev.status === "cancelled" || ev.status === "failed") {
        if (ev.status !== "done") card.replaceWith(el("div", { class: "notice",
          text: ev.status === "cancelled" ? "Image cancelled." : `Image failed: ${ev.error || ""}` }));
        else card.remove();
        delete progressCards[ev.job_id];
      } else if (ev.total) {
        card._bar.classList.remove("indeterminate");
        card._bar.firstChild.style.width = `${Math.round((ev.step / ev.total) * 100)}%`;
        card._label.textContent = `Step ${ev.step} of ${ev.total}`;
      } else if (ev.status) {
        card._label.textContent = ev.status === "loading_model" ? "Loading the image model (the first time takes a few minutes)" : "Working";
      }
      scrollDown();
      break;
    }
    case "image":
      (msg ? msg.extras : $("#messages")).append(imageCard(ev.image)); scrollDown(true); break;
    case "web_results": {
      if (!msg) return;
      const det = el("details", { class: "sources" }, el("summary", {}, icon("globe"), `Searched the web for “${ev.query}” · ${ev.results.length} sources`),
        ev.results.map(r => el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.title || r.url })));
      msg.extras.append(det);
      break;
    }
    case "memory_saved":
      for (const f of ev.facts || []) toast(`Remembered: ${f.text}`, 3500, "memory");
      break;
    case "memory_forgotten":
      toast(ev.facts?.length ? `Forgot ${ev.facts.length} thing(s)` : "Nothing matching was saved", 3500, "memory");
      break;
    case "activity":
      if (panelOpen === "activity") renderActivityRow(ev.entry, true);
      break;
    case "paired":
      toast(`Paired with ${ev.pc_name}`, 3500, "check"); if (panelOpen === "settings") openPanel("settings"); break;
    case "error":
      (msg ? msg.extras : $("#messages")).append(el("div", { class: "notice" }, icon("alert"), ev.message));
      break;
    case "done": finishTurn(ev); break;
  }
}

function finishTurn(ev = {}) {
  const msg = state.current;
  if (msg) {
    clearTimeout(renderTimer); renderTimer = null;
    renderMarkdown(msg.content, msg.text || (Object.keys(msg.tools).length || msg.extras.children.length ? "" : "_(no answer)_"));
    msg.content.classList.remove("typing");
    if (ev.source) {
      const where = ev.source === "pi" ? "backup model on the Pi" : "";
      msg.meta.append(el("span", { class: ev.source === "pi" ? "warnish" : "", text: [ev.model, where, fmtMs(ev.ms)].filter(Boolean).join("  ·  ") }));
    }
  }
  state.current = null;
  state.busy = false;
  $("#send-btn").classList.remove("hidden");
  $("#stop-btn").classList.add("hidden");
  $("#input").focus();
}

// ---------- Sending ----------
async function submit(text) {
  text = text.trim();
  if ((!text && !state.uploads.length) || state.busy) return;
  const uploads = state.uploads.slice();
  if (!send({ type: "send", text: text || "(see attachment)", conversation_id: state.incognito ? null : state.conversationId,
              uploads: uploads.map(u => u.id), incognito: state.incognito })) return;
  document.getElementById("typing-preview")?.classList.add("hidden");
  addUserMessage(text, uploads);
  state.uploads = []; renderAttachments();
  $("#input").value = ""; autosize();
  state.busy = true;
  $("#send-btn").classList.add("hidden");
  $("#stop-btn").classList.remove("hidden");
  newAssistantMessage();
}

async function uploadFiles(files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/uploads", { method: "POST", body: fd });
      state.uploads.push({ ...res, preview: res.is_image ? URL.createObjectURL(file) : null });
      renderAttachments();
    } catch (e) { toast(`Couldn't upload ${file.name}: ${e.message}`); }
  }
}
function renderAttachments() {
  const box = $("#attachments");
  box.replaceChildren(...state.uploads.map((u, i) => el("span", { class: "attachment" },
    u.preview ? el("img", { src: u.preview, alt: "" }) : icon("file"), u.filename,
    ib("x", "Remove", () => { state.uploads.splice(i, 1); renderAttachments(); }))));
}

function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, innerHeight * 0.4) + "px";
}

// ---------- Conversations ----------
async function loadConversations(q = $("#conv-search").value) {
  try {
    state.conversations = await api("/api/conversations" + (q ? `?q=${encodeURIComponent(q)}` : ""));
  } catch { return; }
  const list = $("#conv-list");
  list.replaceChildren(...state.conversations.map(c => {
    const node = el("div", { class: "conv" + (c.id === state.conversationId ? " active" : ""), role: "button", tabindex: "0" },
      el("span", { class: "conv-title", text: c.title }),
      el("span", { class: "conv-actions" },
        ib("pencil", "Rename", e => { e.stopPropagation(); renameConversation(c); }),
        ib("trash", "Delete", e => { e.stopPropagation(); deleteConversation(c); })));
    node.onclick = () => openConversation(c.id);
    node.onkeydown = e => { if (e.key === "Enter") openConversation(c.id); };
    return node;
  }));
  const cur = state.conversations.find(c => c.id === state.conversationId);
  $("#chat-title").textContent = state.incognito ? "Incognito chat (nothing is saved)" : (cur ? cur.title : "New chat");
}

async function openConversation(id) {
  if (state.busy) { toast("Wait for the answer to finish, or press Stop."); return; }
  if (state.incognito) { $("#incognito").checked = false; setIncognito(false); }
  state.conversationId = id;
  closeSidebar();
  const msgs = await api(`/api/conversations/${id}/messages`);
  const box = $("#messages");
  box.querySelectorAll(".msg, .notice, .card").forEach(n => n.remove());
  hideEmpty();
  for (const m of msgs) {
    if (m.role === "user") addUserMessage(m.content, m.uploads || []);
    else {
      const a = newAssistantMessage();
      a.text = m.content;
      for (const t of m.meta.tools || []) { const okI = icon("check"); okI.classList.add("ok-i"); a.steps.append(el("div", { class: "step" }, okI, stepText(t.name, true))); }
      for (const img of m.images || []) a.extras.append(imageCard(img));
      finishTurn({ source: m.meta.source, model: m.meta.model, ms: m.meta.ms });
    }
  }
  scrollDown(true);
  loadConversations();
}

function newChat() {
  if (state.busy) { toast("Wait for the answer to finish, or press Stop."); return; }
  if (state.conversationId) send({ type: "new_chat", previous_id: state.conversationId });
  if (state.incognito) send({ type: "end_incognito" });
  state.conversationId = null;
  $("#messages").querySelectorAll(".msg, .notice, .card").forEach(n => n.remove());
  $("#empty-state").classList.remove("hidden");
  closeSidebar();
  loadConversations();
  $("#input").focus();
}

async function renameConversation(c) {
  const title = prompt("Rename chat", c.title);
  if (title && title.trim()) { await api(`/api/conversations/${c.id}`, { method: "PATCH", body: { title } }); loadConversations(); }
}
async function deleteConversation(c) {
  if (!confirm(`Delete “${c.title}”? This removes the chat from history.`)) return;
  await api(`/api/conversations/${c.id}`, { method: "DELETE" });
  if (c.id === state.conversationId) newChat(); else loadConversations();
}

function setIncognito(on) {
  state.incognito = on;
  if (on) newChat();
  $("#chat-title").textContent = on ? "Incognito chat (nothing is saved)" : "New chat";
}

// ---------- Panels ----------
let panelOpen = null;
function openPanel(name) {
  panelOpen = name;
  closeSidebar();
  $("#panel").classList.remove("hidden");
  const titles = { try: "Things to try", gallery: "Gallery", memory: "Memory", permissions: "Permissions", activity: "Activity log", settings: "Settings" };
  $("#panel-title").textContent = titles[name] || name;
  const content = $("#panel-content");
  content.replaceChildren(el("div", { class: "notice", text: "Loading" }));
  ({ try: renderTry, gallery: renderGallery, memory: renderMemory, permissions: renderPermissions, activity: renderActivity, settings: renderSettings }[name])(content)
    .catch(e => content.replaceChildren(el("div", { class: "notice" }, icon("alert"), e.message)));
}
function closePanel() { $("#panel").classList.add("hidden"); panelOpen = null; }
const section = (title, help, ...children) => el("div", { class: "section" }, el("h3", { text: title }), help ? el("p", { class: "help", text: help }) : null, ...children);
const switchRow = (label, checked, onchange) => {
  const input = el("input", { type: "checkbox" });
  input.checked = !!checked;
  input.onchange = () => onchange(input.checked, input);
  return el("label", { class: "switch-row" }, input, el("span", { class: "switch" }), el("span", { text: label }));
};

// ---------- Things to try ----------
const TRY = [
  ["New", "sparkle", [
    ["Talk to Clicky", "Press Control + Space (or the mic in the Ask box) and just talk. Clicky talks back.", null],
    ["Summaries", "Watching a long video or reading an article? Ask for the gist.", "Summarize what I'm watching"],
    ["Guided lessons you can talk to", "Clicky points at each step and reads it out. Say next, repeat, or ask a question.", "Show me how to send an email"],
    ["What Clicky has learned", "Clicky learns which apps and projects you use (privately) to help you better.", "What have you learned about me?"],
  ]],
  ["Every day", "clock", [
    ["Morning briefing", "Your reminders, calendar, unread mail and the weather.", "Good morning"],
    ["Focus time", "A timer that nudges you if you drift to YouTube or social media.", "Help me focus for 25 minutes"],
    ["Reminders", "Set them in plain English.", "Remind me to drink water in 30 minutes"],
    ["Music", "Play, pause, skip.", "Play some music"],
  ]],
  ["Do it for me", "pointer", [
    ["Open anything", "Apps, files, folders, websites, even with typos.", "Open my downloads"],
    ["Notes", "Make notes with titles, or add to one you have.", "Make a note titled ideas"],
    ["Write for you", "Clicky writes it and puts it in Notes.", "Open Notes and write a short poem about the ocean"],
    ["Messages and email", "Texts always ask first; emails open as drafts for you to send.", "Text mom saying I'm on my way"],
    ["Settings", "Volume, dark mode, Wi-Fi, any Settings page.", "Turn on dark mode"],
    ["Fill in forms", "From your Contacts card. Never passwords or cards, never presses Submit.", "Fill out this form"],
  ]],
  ["Teach and learn", "memory", [
    ["Show me where", "Clicky glides to any button, icon or menu.", "Where is the Wi-Fi?"],
    ["Big apps", "Lessons for Blender, Excel, Photoshop... built from real tutorials.", "How do I add a cube in Blender?"],
    ["Teach Clicky once", "Do something while Clicky watches; it saves it as a skill.", "Watch me do it, call it my routine"],
    ["Your skills", "Everything Clicky knows how to do.", "What skills do you know?"],
  ]],
  ["Watching out for you", "shield", [
    ["Error pop-ups", "Clicky offers to explain them and points at the right button.", null],
    ["Breaks and battery", "Gentle reminders after long sessions or when the battery is low.", null],
    ["Find what you saw", "Turn on Remember what I see in the menu bar, then ask.", "What was that website I looked at yesterday?"],
  ]],
];
async function renderTry(content) {
  const cards = TRY.map(([title, ic, items]) => section(title, null, el("div", { class: "try-grid" }, ...items.map(([name, desc, ask]) =>
    el("div", { class: "try-card" },
      el("div", { class: "try-head" }, icon(ic), el("b", { text: name })),
      el("p", { class: "help", text: desc }),
      ask ? el("button", { class: "btn small", text: "Try it", onclick: () => { closePanel(); submit(ask); } }) : null)))));
  content.replaceChildren(...cards);
}

async function renderGallery(content) {
  const images = await api("/api/images");
  if (!images.length) { content.replaceChildren(el("p", { class: "help", text: "No images yet. Ask Click to draw something!" })); return; }
  content.replaceChildren(el("div", { class: "gallery" }, images.map(img =>
    el("img", { src: img.url, alt: img.prompt, loading: "lazy", onclick: () => openImageViewer(img) }))));
}

function openImageViewer(img) {
  openPanel("gallery");
  const content = $("#panel-content");
  $("#panel-title").textContent = "Image";
  content.replaceChildren(el("div", { class: "viewer" },
    el("img", { src: img.url, alt: img.prompt }),
    section("Prompt", null, el("p", { text: img.prompt })),
    img.negative_prompt ? section("Avoid", null, el("p", { text: img.negative_prompt })) : null,
    el("p", { class: "help", text: `Seed ${img.params?.seed} · ${img.params?.width}×${img.params?.height} · ${img.params?.model || ""} · ${img.params?.quality || ""}` }),
    el("div", { class: "row" },
      el("a", { class: "btn", href: img.url + "?download=true", download: "" }, icon("download"), "Download"),
      el("button", { class: "btn", text: "Regenerate", onclick: () => { closePanel(); send({ type: "regenerate_image", image_id: img.id }); toast("Regenerating with a new seed"); } }),
      img.conversation_id ? el("button", { class: "btn", text: "Open chat", onclick: () => { closePanel(); openConversation(img.conversation_id); } }) : null,
      el("button", { class: "btn danger", text: "Delete", onclick: async () => {
        if (confirm("Delete this image?")) { await api(`/api/images/${img.id}`, { method: "DELETE" }); openPanel("gallery"); } } }))));
}

async function renderMemory(content) {
  const [facts, episodes, msettings, perms] = await Promise.all([
    api("/api/memory/facts"), api("/api/memory/episodes"), api("/api/memory/settings"), api("/api/permissions")]);
  const list = el("div", { class: "list" });
  const search = el("input", { class: "input", type: "search", placeholder: "Search memories" });
  const drawFacts = items => list.replaceChildren(...(items.length ? items.map(factItem) : [el("p", { class: "help", text: "Nothing saved yet." })]));
  search.oninput = async () => drawFacts(await api("/api/memory/facts?q=" + encodeURIComponent(search.value)));
  drawFacts(facts);

  const newText = el("input", { class: "input grow", placeholder: "Add something Click should know" });
  const newCat = el("select", { class: "input", style: "width:auto" }, msettings.categories.map(c => el("option", { value: c, text: c })));
  const addRow = el("div", { class: "row" }, newText, newCat, el("button", { class: "btn primary", text: "Add", onclick: async () => {
    if (!newText.value.trim()) return;
    try { await api("/api/memory/facts", { method: "POST", body: { text: newText.value, category: newCat.value } }); openPanel("memory"); }
    catch (e) { toast(e.message); }
  } }));

  const cats = el("div", { class: "row" }, msettings.categories.map(c => switchRow(c, !msettings.blocked.includes(c), async (on) => {
    const blocked = new Set((await api("/api/memory/settings")).blocked);
    on ? blocked.delete(c) : blocked.add(c);
    await api("/api/memory/settings", { method: "PUT", body: { blocked: [...blocked] } });
  })));

  const epList = el("div", { class: "list" }, episodes.length ? episodes.map(e => el("div", { class: "item" },
    el("div", { class: "item-main" }, e.summary, el("div", { class: "item-sub", text: new Date(e.created_at * 1000).toLocaleString() })),
    ib("trash", "Delete", async () => { await api(`/api/memory/episodes/${e.id}`, { method: "DELETE" }); openPanel("memory"); })))
    : [el("p", { class: "help", text: "No session notes yet." })]);

  const wipeInput = el("input", { class: "input grow", placeholder: "Type WIPE to confirm" });
  content.replaceChildren(
    section("Save memories", "When off, Click won't save anything new. Incognito chats never save.",
      switchRow("Saving memories is on", perms.permissions.memory_saving.enabled, async on => {
        await api("/api/permissions", { method: "PUT", body: { memory_saving: on } }); })),
    section("What Click knows about you", "Edit or delete anything. Click never saves passwords, card, bank or ID numbers.", search, addRow, list),
    section("Don't save these topics", "Turn a topic off and Click won't save facts about it (you can still tell it to remember).", cats),
    section("Session notes", "Short summaries of past chats.", epList),
    section("Export or wipe", null,
      el("div", { class: "row" },
        el("a", { class: "btn", href: "/api/memory/export" }, icon("download"), "Export everything (JSON)"),
        el("button", { class: "btn", text: "Tidy up now", onclick: async () => {
          toast("Tidying memory"); const r = await api("/api/memory/consolidate", { method: "POST" });
          toast(`Merged ${r.merged}, removed ${r.dropped}, summarized ${r.summarized}`); openPanel("memory"); } })),
      el("div", { class: "danger-zone" }, el("p", { class: "help", text: "Wipe all memories (facts and session notes). Chats stay unless you delete them." }),
        el("div", { class: "row" }, wipeInput, el("button", { class: "btn danger", text: "Wipe all memory", onclick: async () => {
          try { await api("/api/memory/wipe", { method: "POST", body: { confirm: wipeInput.value } }); toast("All memories wiped"); openPanel("memory"); }
          catch (e) { toast(e.message); } } })))));
}

function factItem(f) {
  const textNode = el("div", { class: "item-main" }, f.text,
    el("div", { class: "item-sub", text: `${f.category} · from ${f.source_type} · ${new Date(f.updated_at * 1000).toLocaleDateString()}` }));
  const item = el("div", { class: "item" }, textNode,
    ib("clock", "Earlier versions", async () => {
      const h = await api(`/api/memory/facts/${f.id}/history`);
      toast(h.length ? "Previously: " + h.map(x => x.text).join(" → ") : "No earlier versions", 6000); }),
    ib("pencil", "Edit", async () => {
      const t = prompt("Edit memory", f.text);
      if (t && t.trim()) { try { await api(`/api/memory/facts/${f.id}`, { method: "PATCH", body: { text: t } }); openPanel("memory"); } catch (e) { toast(e.message); } } }),
    ib("trash", "Delete", async () => { await api(`/api/memory/facts/${f.id}`, { method: "DELETE" }); item.remove(); }));
  return item;
}

async function renderPermissions(content) {
  const data = await api("/api/permissions");
  const rows = Object.entries(data.permissions).map(([name, p]) => el("div", { class: "perm" },
    el("div", { class: "perm-text" }, el("strong", { text: p.label }), el("small", { text: p.description })),
    switchRow("", p.enabled, async (on, input) => {
      try { await api("/api/permissions", { method: "PUT", body: { [name]: on } }); }
      catch (e) { input.checked = !on; toast(e.message); }
    })));
  content.replaceChildren(
    data.paused ? el("div", { class: "card", text: "Everything is paused. Press “Resume everything” in the sidebar to turn capabilities back on." }) : null,
    section("What Click may do", "Looking at your screen and files always asks first unless you allow it for a chat. Real clicks and typing show a confirmation on your computer every time. On your computer, Ctrl+Alt+Esc stops everything instantly.", ...rows));
}

const ACTIVITY_ICONS = { screen: "monitor", file: "file", memory: "memory", input: "mouse", web: "globe", image: "image", pc: "cpu", permission: "lock" };
function renderActivityRow(e, prepend = false) {
  const list = $("#activity-list");
  if (!list) return;
  const row = el("div", { class: "item" }, icon(ACTIVITY_ICONS[e.category] || "info"),
    el("div", { class: "item-main" }, el("strong", { text: e.action }), e.detail ? " — " + e.detail : "",
      el("div", { class: "item-sub", text: new Date(e.at * 1000).toLocaleString() + " · " + e.category })));
  prepend ? list.prepend(row) : list.append(row);
}
async function renderActivity(content, category = "") {
  const entries = await api("/api/activity" + (category ? `?category=${category}` : ""));
  const filter = el("select", { class: "input", style: "width:auto" },
    ["", "screen", "file", "memory", "input", "web", "image", "pc", "permission"].map(c => el("option", { value: c, text: c || "Everything", selected: c === category })));
  filter.onchange = () => renderActivity(content, filter.value);
  content.replaceChildren(
    el("p", { class: "help", text: "Every screenshot, file read, memory change, mouse/keyboard action, web search and request to your computer." }),
    el("div", { class: "row" }, filter, el("a", { class: "btn", href: "/api/activity/export" }, icon("download"), "Export")),
    el("div", { class: "list", id: "activity-list" }));
  entries.forEach(e => renderActivityRow(e));
}

const ACCENTS = ["#7c3aed", "#2563eb", "#0891b2", "#059669", "#ca8a04", "#ea580c", "#e11d48", "#db2777", "#475569"];
async function renderSettings(content) {
  const [s, sp, st] = await Promise.all([api("/api/settings"), api("/api/system-prompt"), api("/api/status")]);
  const save = async patch => { try { applySettings(await api("/api/settings", { method: "PUT", body: patch })); } catch (e) { toast(e.message); } };
  const swatches = (key) => {
    const row = el("div", { class: "swatches" });
    const draw = () => row.replaceChildren(...ACCENTS.map(c => el("button", {
      class: "swatch" + (state.settings[key] === c ? " active" : ""), style: `background:${c}`, "aria-label": c, type: "button",
      onclick: async () => { await save({ [key]: c }); draw(); updatePreview(); } })),
      (() => { const i = el("input", { type: "color", value: state.settings[key] || "#7c3aed", "aria-label": "Custom colour" });
        i.onchange = async () => { await save({ [key]: i.value }); draw(); updatePreview(); }; return i; })());
    draw();
    return row;
  };
  const select = (key, options) => {
    const sel = el("select", { class: "input", style: "width:auto" }, Object.entries(options).map(([v, t]) => el("option", { value: v, text: t, selected: s[key] === v })));
    sel.onchange = async () => { await save({ [key]: sel.value }); updatePreview(); };
    return sel;
  };
  const preview = el("div", { class: "cursor-preview" });
  const updatePreview = () => {
    const c = state.settings.cursor_color || "#7c3aed", style = state.settings.cursor_style || "arrow";
    const shape = style === "dot" ? `<circle cx="12" cy="12" r="7" fill="${c}" stroke="#fff" stroke-width="2"/>`
      : style === "ring" ? `<circle cx="12" cy="12" r="8" fill="none" stroke="${c}" stroke-width="4"/>`
      : `<path d="M4 2 L4 20 L9 15 L12.5 22 L15.5 20.5 L12 13.8 L19 13.5 Z" fill="${c}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>`;
    preview.innerHTML = `<svg width="34" height="34" viewBox="0 0 24 24" style="filter: drop-shadow(0 0 6px ${c})">${shape}</svg>`;
    preview.append(el("span", { class: "tag", style: `background:${c}`, text: state.settings.cursor_label || state.settings.assistant_name || "Click" }));
  };
  updatePreview();

  const name = el("input", { class: "input grow", value: s.assistant_name, maxlength: "40" });
  const label = el("input", { class: "input grow", value: s.cursor_label, maxlength: "24" });
  const prompt = el("textarea", { class: "input", rows: "12" }, sp.text);
  const pairBox = el("div");
  const drawPair = (status) => {
    const pc = status.pc;
    if (pc.paired) {
      pairBox.replaceChildren(el("p", { text: `${pc.connected ? "Connected to" : "Paired with"} ${pc.pc_name}${pc.connected ? "" : " (offline)"}` }),
        pc.connected ? el("p", { class: "help", text: `Chat model: ${pc.capabilities.models?.chat || "?"} · Vision: ${pc.capabilities.models?.vision || "?"} · Images: ${pc.capabilities.image?.enabled ? pc.capabilities.image.model : "off"} · ${pc.capabilities.os || ""}, ${pc.capabilities.ram_gb || "?"} GB RAM${pc.capabilities.gpu ? ", " + pc.capabilities.gpu : ""}` }) : null,
        !pc.connected && pc.last_error ? el("p", { class: "help", text: "Last error: " + pc.last_error }) : null,
        el("button", { class: "btn danger", text: "Unpair this computer", onclick: async () => {
          if (confirm("Unpair? You'll need to pair again to use your computer's power.")) { await api("/api/pair/unpair", { method: "POST" }); openPanel("settings"); } } }));
    } else {
      pairBox.replaceChildren(el("p", { class: "help", text: "Install the Click Companion on your Windows or Mac computer, then press the button and type the code into the Companion." }),
        el("button", { class: "btn primary", text: "Pair a computer", onclick: async () => {
          const r = await api("/api/pair/start", { method: "POST" });
          pairBox.replaceChildren(el("p", { text: "Type this code into the Click Companion on your computer (tray icon → Pair with Click):" }),
            el("div", { class: "code-box", text: r.code }), el("p", { class: "help", text: "The code works for 10 minutes." }),
            r.addresses?.length ? el("p", { class: "help", text: `If the Companion can't find click.local, type this address instead: ${r.addresses.join(" or ")}` }) : null);
        } }));
    }
  };
  drawPair(st);

  content.replaceChildren(
    section("Look", "Make Click yours.",
      el("div", { class: "row" }, el("span", { text: "Theme" }), select("theme", { system: "Match my device", light: "Light", dark: "Dark" }),
        el("span", { text: "Text size" }), select("font_size", { small: "Small", medium: "Medium", large: "Large" }),
        el("span", { text: "Chat style" }), select("chat_style", { bubbles: "Bubbles", minimal: "Minimal" })),
      el("p", { class: "help", text: "Accent colour" }), swatches("accent_color")),
    section("Name", null, el("div", { class: "row" }, name, el("button", { class: "btn", text: "Save", onclick: () => save({ assistant_name: name.value.trim() || "Click" }) }))),
    section("Click's cursor", "The ghost cursor Click uses on your computer. It never replaces your own mouse.",
      preview, el("p", { class: "help", text: "Colour" }), swatches("cursor_color"),
      el("div", { class: "row" }, el("span", { text: "Shape" }), select("cursor_style", { arrow: "Arrow", dot: "Dot", ring: "Ring" })),
      el("div", { class: "row" }, label, el("button", { class: "btn", text: "Save label", onclick: async () => { await save({ cursor_label: label.value.trim() }); updatePreview(); } }))),
    section("Personality and rules", "This is Click's system prompt. Use {name} for its name.", prompt,
      el("div", { class: "row" }, el("button", { class: "btn primary", text: "Save personality", onclick: async () => {
        try { await api("/api/system-prompt", { method: "PUT", body: { text: prompt.value } }); toast("Saved"); } catch (e) { toast(e.message); } } }))),
    section("Your computer", null, pairBox),
    section("Screen", null,
      switchRow("Keep screenshot history (off = screenshots are deleted after Click reads them)", s.screenshot_history, on => save({ screenshot_history: on })),
      el("div", { class: "row" }, el("span", { text: "Watch mode snapshot every" }),
        select("watch_interval_s", { 15: "15 s", 30: "30 s", 60: "1 min", 120: "2 min", 300: "5 min" }))),
    section("Account", null, el("button", { class: "btn", text: "Log out", onclick: async () => { await api("/api/logout", { method: "POST" }); location.href = "/login"; } })));
}

// ---------- Sidebar (mobile) ----------
function closeSidebar() { $("#sidebar").classList.remove("open"); }

// ---------- Wire up ----------
$("#composer").addEventListener("submit", e => { e.preventDefault(); submit($("#input").value); });
$("#try-btn")?.addEventListener("click", () => openPanel("try"));
$("#see-all")?.addEventListener("click", () => openPanel("try"));
$("#input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit($("#input").value); }
});
$("#input").addEventListener("input", autosize);
// Read ahead while typing: Click starts reading the conversation, so answers start sooner.
let typingTimer;
$("#input").addEventListener("input", () => {
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    const text = $("#input").value;
    if (!state.busy && text.trim().length > 2 && state.ws?.readyState === 1)
      state.ws.send(JSON.stringify({ type: "typing", text, conversation_id: state.incognito ? null : state.conversationId }));
  }, 250);
  const pv = document.getElementById("typing-preview");
  if (pv && normText(pv.dataset.for) !== normText($("#input").value)) pv.classList.add("hidden");
});
function normText(t) { return (t || "").toLowerCase().split(/\s+/).join(" ").replace(/[\s?.!]+$/, "").trim(); }
function showPreview(ev) {
  // the answer, worked out while you type: press Enter and it appears instantly
  if (state.busy || normText(ev.for) !== normText($("#input").value) || !(ev.text || "").trim()) return;
  let pv = document.getElementById("typing-preview");
  if (!pv) {
    pv = el("div", { id: "typing-preview", class: "typing-preview" });
    $("#composer").before(pv);
  }
  pv.dataset.for = ev.for;
  pv.replaceChildren(el("span", { class: "tp-label", text: ev.done ? "Answer ready - press Enter" : "Thinking ahead…" }),
                     el("div", { class: "tp-text", text: ev.text }));
  pv.classList.remove("hidden");
}
$("#input").addEventListener("paste", e => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); uploadFiles(files); }
});
$("#file-input").addEventListener("change", e => { uploadFiles([...e.target.files]); e.target.value = ""; });
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer?.files?.length) uploadFiles([...e.dataTransfer.files]); });
$("#stop-btn").addEventListener("click", () => send({ type: "stop" }));
$("#new-chat").addEventListener("click", newChat);
$("#incognito").addEventListener("change", e => setIncognito(e.target.checked));
let searchTimer;
$("#conv-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadConversations(), 250); });
document.querySelectorAll(".nav-btn").forEach(b => b.addEventListener("click", () => openPanel(b.dataset.panel)));
document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closePanel));
document.addEventListener("keydown", e => { if (e.key === "Escape" && panelOpen) closePanel(); });
$("#pause-btn").addEventListener("click", async () => {
  const paused = !(state.status && state.status.paused);
  try { await api("/api/pause", { method: "POST", body: { paused } }); toast(paused ? "Everything is paused" : "Resumed"); }
  catch (e) { toast(e.message); }
});
$("#open-sidebar").addEventListener("click", () => $("#sidebar").classList.add("open"));
$("#close-sidebar").addEventListener("click", closeSidebar);
document.querySelectorAll(".suggestion").forEach(b => b.addEventListener("click", () => submit(b.dataset.text || b.textContent)));
$("#tile-new").addEventListener("click", newChat);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applySettings(state.settings));

api("/api/settings").then(applySettings).catch(() => {});
loadConversations();
connect();
