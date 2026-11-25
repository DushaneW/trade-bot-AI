// Quorum dashboard. Plain modules, no build step: it ships inside the Python package.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CHAIR = "__chair__";

const local = {
  get(k, d) { try { const v = localStorage.getItem(`quorum.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`quorum.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

// ------------------------------------------------------------------ formatting

const fmt = {
  money: (v) => (v ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  signed: (v) => `${v >= 0 ? "+" : "−"}${fmt.money(Math.abs(v))}`,
  pct: (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(2)}%`,
  price: (v) => (v == null ? "n/a" : v.toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 2 : v >= 1 ? 4 : 6 })),
  qty: (v) => (Math.abs(v) >= 1 ? v.toFixed(3) : v.toPrecision(3)),
  clock: (ts) => new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
  day: (ts) => {
    const d = new Date(ts), now = new Date();
    const y = new Date(now); y.setDate(now.getDate() - 1);
    const label = d.toDateString() === now.toDateString() ? "Today" : d.toDateString() === y.toDateString() ? "Yesterday"
      : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    return `${label} ${fmt.clock(ts)}`;
  },
  ms: (v) => (v == null ? "" : v < 1 ? `${v.toFixed(2)} ms` : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`),
  dir: (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}`,
  tone: (v) => (v > 0 ? "up" : v < 0 ? "down" : ""),
};
const sym = (key) => key.split(":").slice(1).join(":");

function rich(text) {
  const lines = esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split(/\n+/).map((l) => l.trim()).filter(Boolean);
  let out = "", list = [];
  const flush = () => { if (list.length) { out += `<ol>${list.map((l) => `<li>${l}</li>`).join("")}</ol>`; list = []; } };
  for (const l of lines) {
    const m = /^\d+[.)]\s+(.*)$/.exec(l) || /^[-•]\s+(.*)$/.exec(l);
    if (m) list.push(m[1]); else { flush(); out += `<p>${l}</p>`; }
  }
  flush();
  return out;
}

// ------------------------------------------------------------------ api

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

// ------------------------------------------------------------------ state

const state = {
  overview: null,
  catalog: null,
  messages: [],
  chat: [],
  seen: local.get("seen", {}),
  unread: {},
  tabs: local.get("tabs", [CHAIR]),
  viewing: null,
  lean: new Map(), // analyst id -> latest direction, drives where its eyes look
};
const listeners = new Set();
const onMsg = new Set();

const bots = (role) => (state.overview?.bots ?? []).filter((b) => !role || b.role === role);
const bot = (id) => state.overview?.bots.find((b) => b.id === id);
const account = (id) => state.overview?.accounts.find((a) => a.id === id);

async function refresh() {
  try {
    state.overview = await api("/api/state");
    state.tabs = state.tabs.filter((t) => t === CHAIR || bot(t));
    renderSide();
    renderTabs();
    renderRail();
    listeners.forEach((fn) => fn());
  } catch {
    setConn(false);
  }
}

// ------------------------------------------------------------------ agents: look & identity

const LOOK = {
  trend: ["#5b9cf6", "diamond"], momentum: ["#f2b84b", "circle"], volatility: ["#f06a5d", "cloud"],
  breadth: ["#36c2b4", "squircle"], volume: ["#4fb0f5", "pill"], regime: ["#9b7bf0", "hex"],
  structure: ["#d98ad9", "squircle"], divergence: ["#e5c158", "diamond"], relative: ["#4cc38a", "cloud"],
  trend_rider: ["#7c8cf8", "squircle"], mean_reverter: ["#3fb7d9", "circle"], breakout: ["#f08a4b", "hex"],
  accumulator: ["#5cc47a", "pill"], council: ["#c88af0", "cloud"],
};
const ROLE = {
  trend: "Trend", momentum: "Momentum", volatility: "Risk", breadth: "Macro", volume: "Flow", regime: "Regime",
  structure: "Levels", divergence: "Signals", relative: "Ranking",
  trend_rider: "Trader", mean_reverter: "Trader", breakout: "Trader", accumulator: "Trader", council: "Trader",
};
const SHAPE = {
  squircle: '<rect x="4" y="4" width="40" height="40" rx="14"/>',
  circle: '<circle cx="24" cy="24" r="20.5"/>',
  diamond: '<rect x="9.5" y="9.5" width="29" height="29" rx="8" transform="rotate(45 24 24)"/>',
  cloud: '<path d="M14.5 40C8.7 40 4 35.6 4 30c0-4.6 3.2-8.5 7.6-9.6C12.5 13.4 17.7 8 24 8s11.5 5.4 12.4 12.4c4.4 1.1 7.6 5 7.6 9.6 0 5.6-4.7 10-10.5 10z"/>',
  pill: '<rect x="6" y="3.5" width="36" height="41" rx="18"/>',
  hex: '<path d="M19 5.4a10 10 0 0 1 10 0l9.6 5.6a10 10 0 0 1 5 8.6v8.8a10 10 0 0 1-5 8.6L29 42.6a10 10 0 0 1-10 0L9.4 37a10 10 0 0 1-5-8.6v-8.8a10 10 0 0 1 5-8.6z"/>',
};

// The critters. Each is drawn once and then animated with CSS: it breathes and blinks on its own clock,
// sleeps when it is not allowed to work, and reacts to what it does (see react()).

const CHAIR_COLOR = "#f0883e";
const EYE_DROP = { cloud: 3, pill: 1 }; // shapes whose visual centre sits lower

function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c) => Math.round(amount > 0 ? c + (255 - c) * amount : c * (1 + amount));
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(mix);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

function installGradients() {
  const colors = new Set([CHAIR_COLOR, "#8a877f", ...Object.values(LOOK).map(([c]) => c)]);
  const defs = [...colors].map((c) => `
    <linearGradient id="qg-${c.slice(1)}" x1="0" y1="0" x2=".35" y2="1">
      <stop offset="0" stop-color="${shade(c, 0.28)}"/><stop offset=".55" stop-color="${c}"/><stop offset="1" stop-color="${shade(c, -0.18)}"/>
    </linearGradient>`).join("");
  // One clip path per shape keeps the shine and cheeks inside the body on diamonds and clouds.
  const clips = Object.entries(SHAPE).map(([name, path]) => `<clipPath id="qk-${name}">${path}</clipPath>`).join("");
  document.body.insertAdjacentHTML("afterbegin",
    `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>${defs}${clips}</defs></svg>`);
}

function hash(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

function moodOf(who) {
  if (who === CHAIR) return "happy";
  const idle = state.overview?.setup?.step === "done" && !state.overview?.running;
  // Previews (backtest results, the new-bot picker) are not real bots and stay awake.
  if (who.enabled === undefined) return "neutral";
  if (idle || !who.enabled || who.paused_reason) return "asleep";
  if (who.role === "trader" && who.budget) {
    if (who.pnl > who.budget * 0.004) return "happy";
    if (who.pnl < -who.budget * 0.004) return "sad";
  }
  return "neutral";
}

function face(id, color, shape, mood, look = 0, extra = "") {
  const seed = hash(id);
  const y = 24 + (EYE_DROP[shape] ?? 0);
  const px = (Math.max(-1, Math.min(1, look)) * 1.8).toFixed(2);
  const timing = `--breathe:${(3 + (seed % 9) / 6).toFixed(2)}s;--d:-${(seed % 37) / 10}s;--b:-${(seed % 53) / 10}s;--blink:${(4 + (seed % 7) / 2).toFixed(1)}s`;
  return `<svg class="critter mood-${mood}" viewBox="0 0 48 48" style="${timing}" aria-hidden="true">
    <g class="bodyg">
      <g fill="url(#qg-${color.slice(1)})">${SHAPE[shape]}</g>
      <ellipse class="shine" cx="19" cy="${y - 13}" rx="9" ry="3.6" fill="#fff" opacity=".28" clip-path="url(#qk-${shape})"/>
      ${extra}
      <g fill="#ff7f96" opacity=".32" clip-path="url(#qk-${shape})"><ellipse cx="11.5" cy="${y + 6}" rx="3.4" ry="2.1"/><ellipse cx="36.5" cy="${y + 6}" rx="3.4" ry="2.1"/></g>
      <g class="eyes">
        <ellipse cx="18" cy="${y}" rx="4.6" ry="5.3" fill="#fff"/><ellipse cx="30" cy="${y}" rx="4.6" ry="5.3" fill="#fff"/>
        <g class="pupils" style="--px:${px}px">
          <circle cx="18" cy="${y + 0.6}" r="2.5" fill="#1d1b19"/><circle cx="30" cy="${y + 0.6}" r="2.5" fill="#1d1b19"/>
          <circle cx="19" cy="${y - 0.6}" r=".9" fill="#fff"/><circle cx="31" cy="${y - 0.6}" r=".9" fill="#fff"/>
        </g>
      </g>
      <g class="lids" fill="none" stroke="#1d1b19" stroke-width="1.8" stroke-linecap="round">
        <path d="M14 ${y + 0.5}q4 3 8 0"/><path d="M26 ${y + 0.5}q4 3 8 0"/>
      </g>
      <g class="mouths" fill="none" stroke="#1d1b19" stroke-width="1.7" stroke-linecap="round">
        <path class="m-neutral" d="M22 ${y + 8.5}q2 1.3 4 0"/>
        <path class="m-happy" d="M20.5 ${y + 7.5}q3.5 3.6 7 0"/>
        <path class="m-sad" d="M21.5 ${y + 9.6}q2.5-2 5 0"/>
        <ellipse class="m-asleep" cx="24" cy="${y + 8.6}" rx="1.4" ry="1.1" fill="#1d1b19" stroke="none"/>
      </g>
    </g>
    <text class="zz" x="36" y="10">z</text><text class="zz z2" x="40" y="6">z</text>
  </svg>`;
}

function avatar(who, size = "", { dots = false, badge = 0 } = {}) {
  const badgeHtml = badge ? `<span class="badge">${badge > 9 ? "9+" : badge}</span>` : "";
  if (who === CHAIR) {
    const crown = '<path d="M17 10.5a7 7 0 0 1 14 0" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="2.2" stroke-linecap="round"/>';
    return `<span class="av ${size}" data-av="${CHAIR}" data-follow>${face(CHAIR, CHAIR_COLOR, "squircle", "happy", 0, crown)}${badgeHtml}</span>`;
  }
  const [color, shape] = LOOK[who.strategy] ?? ["#8a877f", "squircle"];
  const analyst = who.role === "analyst";
  const look = analyst ? state.lean.get(who.id) ?? 0 : 0;
  const d = !dots ? "" : `<span class="dots">${dotsOf(who)}</span>`;
  return `<span class="av ${size}" data-av="${who.id}" ${analyst ? "" : "data-follow"}>${face(who.id, color, shape, moodOf(who), look)}${d}${badgeHtml}</span>`;
}

function dotsOf(who) {
  return who.role === "trader"
    ? `<i class="${who.paused_reason || !who.enabled ? "stop" : "ok"}"></i><i class="${Object.keys(who.positions ?? {}).length ? "ok" : ""}"></i><i class="${who.pnl > 0 ? "ok" : who.pnl < 0 ? "stop" : ""}"></i>`
    : `<i class="${who.enabled ? "ok" : ""}"></i><i class="${(who.reputation?.accuracy ?? 0) > 0.55 ? "ok" : ""}"></i><i class="${who.reputation?.calls ? "ok" : ""}"></i>`;
}

// Bring existing critters up to date without redrawing them, so their animations keep running.
function refreshCritters() {
  for (const av of $$("[data-av]")) {
    const id = av.dataset.av;
    const who = id === CHAIR ? CHAIR : bot(id);
    const critter = $(".critter", av);
    if (!who || !critter) continue;
    const mood = moodOf(who);
    if (!critter.classList.contains(`mood-${mood}`) && !critter.dataset.reacting) {
      critter.classList.remove("mood-neutral", "mood-happy", "mood-sad", "mood-asleep");
      critter.classList.add(`mood-${mood}`);
    }
    const dots = $(".dots", av);
    if (dots && who !== CHAIR) {
      const html = dotsOf(who);
      if (dots.innerHTML !== html) dots.innerHTML = html;
    }
  }
}

function lookAt(id, direction) {
  state.lean.set(id, direction);
  const px = `${(Math.max(-1, Math.min(1, direction)) * 1.8).toFixed(2)}px`;
  $$(`[data-av="${CSS.escape(id)}"] .pupils`).forEach((p) => p.style.setProperty("--px", px));
}

// One-off reactions: "hop", "wiggle", "think"; a mood ("happy", "sad") can ride along for a moment.
function react(id, motion, mood = null, ms = 1100) {
  for (const critter of $$(`[data-av="${CSS.escape(id)}"] .critter`)) {
    if (critter.classList.contains("mood-asleep")) continue;
    critter.classList.remove("hop", "wiggle");
    void critter.getBBox(); // restart the animation if it is already playing
    critter.classList.add(motion);
    if (mood) {
      critter.dataset.reacting = "1";
      critter.classList.remove("mood-neutral", "mood-happy", "mood-sad");
      critter.classList.add(`mood-${mood}`);
    }
    clearTimeout(critter._t);
    critter._t = setTimeout(() => {
      critter.classList.remove(motion);
      delete critter.dataset.reacting;
      refreshCritters();
    }, ms);
  }
}

const lastWiggle = new Map();
function hearCritters(msg) {
  const d = msg.data;
  if (msg.topic === "fill") {
    react(d.bot_id, "hop", d.pnl > 0 ? "happy" : d.pnl < 0 ? "sad" : null, 1600);
  } else if (msg.topic === "signal" && Math.abs(d.direction) >= 0.6) {
    const now = Date.now();
    if (now - (lastWiggle.get(d.source) ?? 0) > 6000) { lastWiggle.set(d.source, now); react(d.source, "wiggle"); }
  } else if (msg.topic === "chat" && d.role === "lead") {
    if (d.kind === "task") {
      $$(`[data-av="${CHAIR}"] .critter`).forEach((c) => c.classList.toggle("think", d.status === "running"));
    } else {
      react(CHAIR, "hop", "happy");
    }
  } else if (msg.topic === "council") {
    refreshCritters();
  }
}

// Critters that are not busy forecasting glance at your cursor.
let gaze = null;
document.addEventListener("pointermove", (e) => {
  if (gaze) return;
  gaze = requestAnimationFrame(() => {
    gaze = null;
    for (const av of $$("[data-follow]")) {
      const r = av.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) continue;
      const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
      const dist = Math.hypot(dx, dy) || 1, pull = Math.min(1, dist / 160);
      const p = $(".pupils", av);
      p?.style.setProperty("--px", `${((dx / dist) * 1.8 * pull).toFixed(2)}px`);
      p?.style.setProperty("--py", `${((dy / dist) * 1.4 * pull).toFixed(2)}px`);
    }
  });
});
document.addEventListener("pointerleave", () => $$("[data-follow] .pupils").forEach((p) => { p.style.setProperty("--px", "0px"); p.style.setProperty("--py", "0px"); }));

const profileLabel = (key) => state.catalog?.profiles?.[key]?.label ?? "Balanced";
const nameOf = (id) => (id === CHAIR ? "Chair" : bot(id)?.name ?? "Bot");
const roleOf = (id) => (id === CHAIR ? "Team lead" : ROLE[bot(id)?.strategy] ?? "");

// ------------------------------------------------------------------ unread

function noteworthy(msg) {
  const d = msg.data;
  switch (msg.topic) {
    case "fill": return d.bot_id;
    case "rejected": return d.bot;
    case "bot": return d.event === "paused" ? d.bot : null;
    // Analysts talk on every bar; their calls reach you through the Chair, not as unread badges.
    case "chat": return d.role === "lead" && d.kind !== "task" ? CHAIR : null;
    default: return null;
  }
}

function countUnread() {
  state.unread = {};
  const bump = (id, ts) => { if (id && ts > (state.seen[id] ?? 0)) state.unread[id] = (state.unread[id] ?? 0) + 1; };
  state.messages.forEach((m) => bump(noteworthy(m), m.ts));
  state.chat.forEach((c) => bump(noteworthy({ topic: "chat", data: c }), c.ts));
}

function markSeen(id) {
  state.seen[id] = Date.now();
  state.unread[id] = 0;
  local.set("seen", state.seen);
  renderSide();
}

// ------------------------------------------------------------------ live connection

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connect, 2000); };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    let fresh = true;
    if (msg.topic === "chat") fresh = upsertChat(msg.data);
    else if (msg.topic !== "bar" && msg.topic !== "score") {
      state.messages.push(msg);
      if (state.messages.length > 3000) state.messages.splice(0, 1000);
    }
    if (msg.topic === "signal" && msg.data.kind !== "risk") lookAt(msg.data.source, msg.data.direction);
    const who = noteworthy(msg);
    if (who && fresh && who !== state.viewing) {
      state.unread[who] = (state.unread[who] ?? 0) + 1;
      renderSide();
      $(`.row[data-id="${CSS.escape(who)}"]`)?.classList.add("pulse");
    }
    onMsg.forEach((fn) => fn(msg));
    if (msg.topic === "bot" && msg.data.event === "paused") toast(`${msg.data.name} paused: ${msg.data.reason}`, true);
    if (["bot", "account", "halt", "fill"].includes(msg.topic)) refresh();
  };
}

function upsertChat(m) {
  const i = state.chat.findIndex((c) => c.id === m.id);
  if (i >= 0) { state.chat[i] = m; return false; }
  state.chat.push(m);
  return true;
}

function setConn(on) {
  const dot = $("#conn");
  if (dot) { dot.className = on ? "on" : "off"; dot.parentElement.dataset.tip = on ? "Engine running" : "Engine unreachable"; }
}

// ------------------------------------------------------------------ toasts & dialogs

function toast(text, bad = false) {
  const el = document.createElement("div");
  el.className = `toast${bad ? " bad" : ""}`;
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => { el.classList.add("out"); el.addEventListener("animationend", () => el.remove()); }, 4000);
}

function dialog(html, bind) {
  const d = $("#dialog");
  d.innerHTML = html;
  return new Promise((resolve) => {
    const close = (v) => { d.close(); resolve(v); };
    d.onclose = () => resolve(null);
    $$("[data-close]", d).forEach((b) => b.addEventListener("click", () => close(b.dataset.close || null)));
    bind?.(d, close);
    d.showModal();
  });
}

// ------------------------------------------------------------------ icons

const I = {
  team: '<path d="M7 9.5h10M7 13h6"/><path d="M5.5 4.5h13a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H11l-4.5 3.5v-3.5h-1a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z"/>',
  markets: '<path d="M4 19V5"/><path d="M4 19h16"/><path d="m7.5 14 3.5-3.5 3 2.5 5-6"/>',
  activity: '<path d="m4 7 1.5 1.5L8.5 5.5M4 13l1.5 1.5 3-3M4 19l1.5 1.5 3-3"/><path d="M12 7h8M12 13h8M12 19h8"/>',
  accounts: '<rect x="3.5" y="7" width="17" height="12.5" rx="2.5"/><path d="M8.5 7V5.5a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5V7M3.5 12.5h17"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  rewind: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4.5h4.5"/><path d="M12 7.5V12l3 2"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  doc: '<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M14 3.5V8h4"/>',
  chat: '<path d="M5 18.5 3.5 21l.5-3.5A8 8 0 1 1 12 20a8 8 0 0 1-4.5-1.4z"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h10"/>',
};
const icon = (n, cls = "i") => `<svg viewBox="0 0 24 24" class="${cls}" aria-hidden="true">${I[n]}</svg>`;

// ------------------------------------------------------------------ rail

function renderRail() {
  const o = state.overview;
  const pnl = o ? o.totals.equity - o.totals.budget : 0;
  const pct = o && o.totals.budget ? pnl / o.totals.budget : 0;
  const here = location.hash || "#/";
  const link = (href, name, tip) => `<a href="${href}" class="${here === href || (href !== "#/" && here.startsWith(href)) || (href === "#/" && (here === "#/" || here.startsWith("#/agent"))) ? "active" : ""}" data-tip="${tip}" aria-label="${tip}">${icon(name)}</a>`;
  $("#rail").innerHTML = `
    ${link("#/", "team", "Team")}
    ${link("#/markets", "markets", "Markets")}
    ${link("#/activity", "activity", "Activity")}
    ${link("#/backtest", "rewind", "Backtest")}
    ${link("#/accounts", "accounts", "Accounts")}
    <span class="gap"></span>
    ${o?.totals.budget ? `<div class="pnl" title="Return across all traders"><small>P&amp;L</small><b class="${fmt.tone(pnl)}">${fmt.pct(pct)}</b></div>` : ""}
    ${link("#/settings", "settings", "Settings")}
    <span class="me" data-tip="Engine">You<i id="conn" class="on"></i></span>`;
}

// ------------------------------------------------------------------ sidebar

const closedGroups = new Set(local.get("closedGroups", []));

function preview(id) {
  if (id === CHAIR) {
    const last = [...state.chat].reverse().find((m) => m.role === "lead" && m.kind !== "task");
    if (!last) return { ts: null, text: "Briefings and answers land here" };
    const first = last.items?.[0];
    return { ts: last.ts, text: first ? `${first.verb} ${first.text}` : last.text };
  }
  const b = bot(id);
  if (b.paused_reason) return { ts: b.last?.ts, text: `Paused: ${b.paused_reason}` };
  if (b.last) return { ts: b.last.ts, text: b.last.text };
  return { ts: null, text: b.role === "trader" ? `${fmt.pct(b.pnl_pct)} on ${fmt.money(b.budget)} · waiting for a setup` : "Listening to the market" };
}

function row(id) {
  const who = id === CHAIR ? CHAIR : bot(id);
  const p = preview(id);
  return `<div class="row${state.viewing === id ? " active" : ""}" data-id="${id}" role="link" tabindex="0">
    ${avatar(who, "", { dots: id !== CHAIR, badge: state.unread[id] ?? 0 })}
    <div style="min-width:0">
      <div class="l1"><span class="name">${esc(nameOf(id))}</span><span class="chip">${esc(roleOf(id))}</span><span class="time">${p.ts ? fmt.clock(p.ts) : ""}</span></div>
      <div class="l2">${esc(p.text)}</div>
    </div></div>`;
}

let sideSig = "";

function patchRow(r) {
  const id = r.dataset.id;
  if (id !== CHAIR && !bot(id)) return;
  const p = preview(id);
  const time = p.ts ? fmt.clock(p.ts) : "";
  if ($(".time", r).textContent !== time) $(".time", r).textContent = time;
  if ($(".l2", r).textContent !== p.text) $(".l2", r).textContent = p.text;
  const n = state.unread[id] ?? 0;
  let badge = $(".badge", r);
  if (n > 0) {
    if (!badge) { badge = document.createElement("span"); badge.className = "badge"; $(".av", r).append(badge); }
    badge.textContent = n > 9 ? "9+" : n;
  } else badge?.remove();
}

function renderSide() {
  if (!state.overview) return;
  const q = $("#search").value.trim().toLowerCase();
  const match = (id) => !q || `${nameOf(id)} ${roleOf(id)} ${bot(id)?.title ?? "team lead chair"}`.toLowerCase().includes(q);
  const unread = [CHAIR, ...bots().map((b) => b.id)].filter((id) => (state.unread[id] ?? 0) > 0 && id !== state.viewing && match(id));
  const group = (key, label, ids, role) => `
    <div class="group${closedGroups.has(key) ? " closed" : ""}" data-group="${key}">
      <div class="group-head" role="button" tabindex="0" aria-expanded="${!closedGroups.has(key)}">${icon("chev", "chev")}<span>${label}</span><span class="count">${ids.length}</span>
        <span class="end"><a href="#/new?role=${role}" title="New ${role}" aria-label="New ${role}">${icon("plus", "")}</a></span></div>
      <div class="group-body"><div>${ids.map(row).join("") || `<div class="row" style="cursor:default"><span></span><span class="l2">No ${role}s yet</span></div>`}</div></div>
    </div>`;
  // Same people in the same places: update text and badges in place so the critters keep animating.
  const sig = JSON.stringify([q, state.viewing, unread, bots().map((b) => b.id + b.name), [...closedGroups]]);
  if (sig === sideSig) {
    for (const r of $$("#sideList .row[data-id]")) patchRow(r);
    refreshCritters();
    return;
  }
  sideSig = sig;
  const scroll = $("#sideList").scrollTop;
  $("#sideList").innerHTML = `
    ${unread.length ? `<div class="group unread-group"><div class="group-head" style="cursor:default">Unread <span class="count">${unread.length}</span></div>${unread.map(row).join("")}</div>` : ""}
    ${match(CHAIR) ? `<div class="group"><div class="group-head" style="cursor:default">Lead</div>${row(CHAIR)}</div>` : ""}
    ${bots().length ? `
      ${group("traders", "Traders", bots("trader").map((b) => b.id).filter(match), "trader")}
      ${group("analysts", "Analysts", bots("analyst").map((b) => b.id).filter(match), "analyst")}`
    : `<p class="side-note">Your analysts and traders appear here once the Chair has set up your account.</p>`}`;
  $("#sideList").scrollTop = scroll;
}

$("#sideList").addEventListener("click", (e) => {
  const r = e.target.closest(".row[data-id]");
  if (r) { location.hash = r.dataset.id === CHAIR ? "#/" : `#/agent/${r.dataset.id}`; return; }
  const head = e.target.closest(".group[data-group] .group-head");
  if (head && !e.target.closest("a")) {
    const g = head.parentElement;
    g.classList.toggle("closed");
    g.classList.contains("closed") ? closedGroups.add(g.dataset.group) : closedGroups.delete(g.dataset.group);
    local.set("closedGroups", [...closedGroups]);
  }
});
$("#sideList").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const target = e.target.closest(".row[data-id], .group-head[role=button]");
  if (target) { e.preventDefault(); target.click(); }
});
$("#search").addEventListener("input", renderSide);

// ------------------------------------------------------------------ tabs

const PAGE_TITLES = { markets: "Markets", activity: "Activity", backtest: "Backtest", accounts: "Accounts", settings: "Settings", new: "New bot" };

function renderTabs() {
  // (Re)drawn only when the set of tabs changes; the council button on the right updates on its own.
  const o = state.overview;
  const page = (location.hash.slice(2).split(/[/?]/)[0]) || "";
  const pageTab = PAGE_TITLES[page] ? `<div class="tab active"><b>${PAGE_TITLES[page]}</b></div>` : "";
  const live = o?.totals.testnet ? "testnet" : "live";
  const sig = JSON.stringify([state.tabs.map((id) => id + nameOf(id)), state.viewing, page]);
  if (sig === tabsSig && $("#tabs .right")) {
    const right = o ? councilControl(o, live) : "";
    if (right !== lastRight) { $("#tabs .right").innerHTML = right; lastRight = right; }
    return;
  }
  tabsSig = sig;
  lastRight = o ? councilControl(o, live) : "";
  $("#tabs").innerHTML = `
    <button class="icon-btn menu-btn" id="menuBtn" aria-label="Open the council list">${icon("menu", "")}</button>
    ${state.tabs.map((id) => `
      <div class="tab${state.viewing === id ? " active" : ""}" data-tab="${id}" role="link" tabindex="0">
        ${avatar(id === CHAIR ? CHAIR : bot(id), "s")}<b>${esc(nameOf(id))}</b><span>· ${esc(roleOf(id))}</span>
        ${id === CHAIR ? "" : `<button class="x" data-close-tab="${id}" aria-label="Close tab">${icon("x", "")}</button>`}
      </div>`).join("")}
    ${pageTab}
    <a class="icon-btn add" href="#/new" title="New bot" aria-label="New bot">${icon("plus", "")}</a>
    <div class="right">${lastRight}</div>`;
}
let tabsSig = "", lastRight = "";

$("#tabs").addEventListener("click", async (e) => {
  const x = e.target.closest("[data-close-tab]");
  if (x) {
    e.stopPropagation();
    const id = x.dataset.closeTab;
    state.tabs = state.tabs.filter((t) => t !== id);
    local.set("tabs", state.tabs);
    if (state.viewing === id) location.hash = "#/"; else renderTabs();
    return;
  }
  const tab = e.target.closest("[data-tab]");
  if (tab) { location.hash = tab.dataset.tab === CHAIR ? "#/" : `#/agent/${tab.dataset.tab}`; return; }
  if (e.target.closest("#menuBtn")) { $("#side").classList.add("open"); $("#scrim").classList.add("on"); return; }
  const ctl = e.target.closest("#council");
  if (ctl) {
    ctl.disabled = true;
    try {
      await api("/api/council", { method: "POST", body: { running: ctl.dataset.to === "start" } });
      if (location.hash !== "#/" && location.hash !== "") location.hash = "#/"; // the Chair confirms there
      await refresh();
    } catch (err) { toast(err.message, true); ctl.disabled = false; }
  }
});

function councilControl(o, live) {
  if (o.setup.step !== "done") return `<span class="mode-pill idle"><i></i>Setup in progress</span>`;
  if (!o.running) {
    return `<span class="mode-pill idle"><i></i>Council stopped</span>
      <button class="ctl start" id="council" data-to="start"><i></i><span>Start</span></button>`;
  }
  return `<span class="mode-pill${live === "live" ? " live" : ""}"><i></i>${live === "testnet" ? "Trading on testnet" : "Trading real money"} · ${o.totals.running} of ${o.bots.length} active</span>
    <button class="ctl stop" id="council" data-to="stop"><i></i><span>Stop</span></button>`;
}
$("#scrim").addEventListener("click", () => { $("#side").classList.remove("open"); $("#scrim").classList.remove("on"); });

// ------------------------------------------------------------------ Chair conversation

const openSteps = new Set();

function taskBlock(m) {
  const done = m.steps.filter((s) => s.status !== "running").length;
  if (m.status === "running") {
    return `<div class="card open waiting" data-msg="${m.id}">
      <div class="card-head"><span class="st run"></span>Waiting for answers<span class="meta">${done}/${m.steps.length}${icon("chev", "chev")}</span></div>
      <div class="card-body"><div>${m.steps.map((s) => `
        <div class="item">${s.bot_id ? avatar(bot(s.bot_id) ?? CHAIR, "s") : avatar(CHAIR, "s")}<b>${esc(s.name)}</b>
          <span class="state">${s.status === "running" ? `Working <span class="st run"></span>` : `${fmt.ms(s.ms)} <span class="st ok"></span>`}</span></div>`).join("")}</div></div>
    </div>`;
  }
  const total = m.steps.reduce((t, s) => t + (s.ms ?? 0), 0);
  const open = openSteps.has(m.id);
  const ids = m.steps.map((s) => s.bot_id).filter(Boolean);
  return `<div data-msg="${m.id}" style="display:grid;gap:10px">
    <button class="steps-line${open ? " open" : ""}" data-steps="${m.id}">${esc(m.text.replace(/^Consulting/, "Consulted"))} <span>· ${fmt.ms(total)}</span>${icon("chev", "")}</button>
    <div class="card${open ? " open" : ""}" style="${open ? "" : "display:none"}">
      <div class="card-body"><div>${m.steps.map((s) => `
        <div class="item"><span class="st ${s.status === "done" ? "ok" : s.status === "failed" ? "fail" : "idle"}"></span>
          <div style="min-width:0"><b>${esc(s.name)}</b>${s.bot_id ? ` <span class="muted">· ${esc(roleOf(s.bot_id))}</span>` : ""}
            <div class="sub">${icon("chat", "")}<span>${esc(s.note ?? "")}</span></div></div>
          <span class="t">${fmt.ms(s.ms)}</span></div>`).join("")}</div></div>
    </div>
    <div class="participants">${ids.length} answers, from <span class="stack">${ids.slice(0, 4).map((id) => avatar(bot(id) ?? CHAIR, "s")).join("")}</span><b>${ids.length} analysts</b> ${fmt.clock(m.ts)}</div>
  </div>`;
}

function field(f) {
  const id = `f_${f.name}`;
  const hint = f.hint ? `<small>${esc(f.hint)}</small>` : "";
  const showIf = f.show_if ? ` data-show-if='${esc(JSON.stringify(f.show_if))}'` : "";
  if (f.kind === "check") {
    return `<label class="check"${showIf}><input type="checkbox" name="${f.name}" ${f.value ? "checked" : ""}> <span>${esc(f.label)}${f.hint ? `<small class="muted" style="display:block">${esc(f.hint)}</small>` : ""}</span></label>`;
  }
  if (f.kind === "choice") {
    return `<div class="field"${showIf}><span>${esc(f.label)}</span><div class="options row-options">${f.options.map((o) => `
      <label class="option"><input type="radio" name="${f.name}" value="${esc(o.value)}" ${o.value === f.value ? "checked" : ""}>
        <b>${esc(o.label)}</b>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</label>`).join("")}</div></div>`;
  }
  const type = f.kind === "secret" ? "password" : f.kind === "number" ? "number" : "text";
  return `<label class="field"${showIf} for="${id}"><span>${esc(f.label)}</span>
    <input class="input" id="${id}" name="${f.name}" type="${type}" ${type === "number" ? 'step="any" min="0"' : ""} value="${esc(f.value ?? "")}" placeholder="${esc(f.placeholder ?? "")}" autocomplete="off" spellcheck="false">${hint}</label>`;
}

function promptBlock(m) {
  const p = m.prompt, done = Boolean(m.answered);
  let body = "";
  if (p.type === "choice") {
    body = `<div class="options">${p.options.map((o) => `
      <button type="button" class="option${o.primary ? " primary" : ""}" data-choice="${esc(o.value)}" ${done ? "disabled" : ""}>
        <b>${esc(o.label)}</b>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</button>`).join("")}</div>`;
  } else if (p.type === "multi") {
    body = `<div class="toggles">${p.options.map((o) => `<button type="button" data-multi="${esc(o.value)}" class="${(p.selected ?? []).includes(o.value) ? "on" : ""}" ${done ? "disabled" : ""}>${esc(o.label)}</button>`).join("")}</div>
      <div><button class="btn primary" type="submit" ${done ? "disabled" : ""}>${esc(p.submit)}</button></div>`;
  } else {
    body = `${p.help ? `<div class="hint">${esc(p.help)}</div>` : ""}${p.fields.map(field).join("")}
      <div><button class="btn primary" type="submit" ${done ? "disabled" : ""}>${esc(p.submit)}</button></div>`;
  }
  return `<form class="lead prompt${done ? " answered" : ""}" data-msg="${m.id}" data-type="${p.type}" autocomplete="off">
    <div class="bubble"><p>${esc(m.text)}</p></div>
    <fieldset class="prompt-body" ${done ? "disabled" : ""}>${body}</fieldset>
  </form>`;
}

function chatBlock(m) {
  if (m.kind === "task") return taskBlock(m);
  if (m.kind === "prompt") return promptBlock(m);
  if (m.role === "owner") return `<div class="owner" data-msg="${m.id}">${esc(m.text)}</div>`;
  const items = m.items?.length ? `<ol>${m.items.map((i) => `<li><b>${esc(i.verb)}</b> ${esc(i.text)}</li>`).join("")}</ol>` : "";
  const body = m.source === "claude" || m.kind === "say" ? rich(m.text) : `<p>${esc(m.text)}</p>`;
  const props = m.proposals?.length ? `<div class="proposals">${m.proposals.map((p) => p.applied
    ? `<button class="btn done">✓ ${esc(p.label)}</button>` : `<button class="btn" data-prop="${p.id}">${esc(p.label)}</button>`).join("")}</div>` : "";
  const kind = { briefing: "Briefing · ", answer: "Answer · ", notice: "" }[m.kind] ?? "";
  return `<div class="lead${m.kind === "notice" ? " notice" : ""}" data-msg="${m.id}">
    <div class="bubble">${body}${items}</div>${props}
    <div class="under">${kind}${fmt.clock(m.ts)}${m.source === "claude" ? " · written by Claude" : ""}</div>
  </div>`;
}

function composer(placeholder, onSend, suggestions = []) {
  return {
    html: `<div class="composer">
      ${suggestions.length ? `<div class="suggest">${suggestions.map((s) => `<button type="button">${esc(s)}</button>`).join("")}</div>` : ""}
      <form class="box" autocomplete="off">
        <a class="round" href="#/new" title="New bot" aria-label="New bot">${icon("plus", "")}</a>
        <input name="q" placeholder="${esc(placeholder)}" maxlength="2000">
        <button class="round send" aria-label="Send" disabled>${icon("up", "")}</button>
      </form></div>`,
    bind(el) {
      const form = $(".box", el), input = $("input", form), send = $(".send", form);
      input.addEventListener("input", () => { send.disabled = !input.value.trim(); });
      form.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value.trim(); if (!v) return; input.value = ""; send.disabled = true; onSend(v); });
      $$(".suggest button", el).forEach((b) => b.addEventListener("click", () => onSend(b.textContent)));
      return input;
    },
  };
}

const ask = async (text) => { try { await api("/api/chat", { method: "POST", body: { text } }); } catch (e) { toast(e.message, true); } };

// ------------------------------------------------------------------ views

const views = {
  chair(el) {
    const firstMarket = Object.keys(state.overview?.markets ?? {})[0];
    const ready = state.overview?.setup?.step === "done";
    const c = composer(ready ? "Message the Chair" : "Answer the question above, or type here", ask, ready ? [
      "How are my traders doing?", "Which market has the strongest vote?", firstMarket && `What about ${sym(firstMarket)}?`,
    ].filter(Boolean) : []);
    el.innerHTML = `<div class="convo"><div class="thread" id="thread"></div>${c.html}</div>`;
    const input = c.bind(el);
    const thread = $("#thread", el);
    let lastDay = "";

    const dayMark = (m) => {
      const d = new Date(m.ts).toDateString();
      if (d === lastDay) return "";
      lastDay = d;
      return `<div class="day">${fmt.day(m.ts)}</div>`;
    };
    const atBottom = () => el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    const toBottom = (smooth) => el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });

    let lastIntro = "";
    const intro = () => {
      const setup = state.overview?.setup;
      if (setup && setup.step !== "done") {
        return `${avatar(CHAIR, "m")}<div><b>Chair</b> <span class="muted">· Setup, step ${Math.min(setup.index + 1, setup.total)} of ${setup.total}</span>
          <div class="bar" style="margin-top:8px;width:320px;max-width:100%"><span style="width:${(setup.index / setup.total) * 100}%"></span></div></div>`;
      }
      const running = state.overview?.running;
      return `${avatar(CHAIR, "m")}<div><b>Chair</b> <span class="muted">· Team lead · ${running ? "council running" : "council stopped"}</span>
        <p>Consults ${bots("analyst").length} analysts on demand, watches ${bots("trader").length} traders and briefs you when something changes. Every change to money waits for your click.</p></div>`;
    };
    const renderAll = () => {
      lastDay = "";
      lastIntro = intro();
      thread.innerHTML = `<div class="intro" id="intro">${lastIntro}</div>` + state.chat.map((m) => dayMark(m) + chatBlock(m)).join("");
      $$("form.prompt", thread).forEach(syncVisibility);
      toBottom(false);
    };
    const upsert = (m) => {
      const stick = atBottom() || m.role === "owner" || m.kind === "prompt";
      const old = $(`[data-msg="${m.id}"]`, thread);
      if (old) { old.insertAdjacentHTML("afterend", chatBlock(m)); old.remove(); }
      else { thread.insertAdjacentHTML("beforeend", dayMark(m) + chatBlock(m)); thread.lastElementChild.classList.add("enter"); }
      const form = $(`form.prompt[data-msg="${m.id}"]`, thread);
      if (form) {
        syncVisibility(form);
        if (!m.answered) $("input:not([type=hidden]):not([type=radio]):not([type=checkbox])", form)?.focus({ preventScroll: true });
      }
      if (stick) toBottom(true);
    };

    const answer = async (form, value) => {
      const box = $(".prompt-body", form);
      box.disabled = true;
      try {
        const m = await api(`/api/chat/${form.dataset.msg}/answer`, { method: "POST", body: { value } });
        upsertChat(m); upsert(m);
        refresh();
      } catch (err) { box.disabled = false; toast(err.message, true); }
    };
    function syncVisibility(form) {
      $$("[data-show-if]", form).forEach((el) => {
        const rule = JSON.parse(el.dataset.showIf);
        const ok = Object.entries(rule).every(([k, v]) => form.querySelector(`[name="${k}"]:checked`)?.value === v);
        el.style.display = ok ? "" : "none";
      });
    }
    thread.addEventListener("change", (e) => { const f = e.target.closest("form.prompt"); if (f) syncVisibility(f); });
    thread.addEventListener("submit", (e) => {
      const form = e.target.closest("form.prompt");
      if (!form) return;
      e.preventDefault();
      if (form.dataset.type === "multi") {
        answer(form, { choices: $$("[data-multi].on", form).map((b) => b.dataset.multi) });
        return;
      }
      const value = {};
      $$("input", form).forEach((i) => {
        if (i.type === "checkbox") value[i.name] = i.checked;
        else if (i.type === "radio") { if (i.checked) value[i.name] = i.value; }
        else if (i.type === "number") value[i.name] = i.value === "" ? null : Number(i.value);
        else value[i.name] = i.value;
      });
      answer(form, value);
    });

    thread.addEventListener("click", async (e) => {
      const choice = e.target.closest("[data-choice]");
      if (choice) { answer(choice.closest("form.prompt"), { choice: choice.dataset.choice }); return; }
      const multi = e.target.closest("[data-multi]");
      if (multi) { multi.classList.toggle("on"); return; }
      const line = e.target.closest("[data-steps]");
      if (line) {
        const id = line.dataset.steps;
        openSteps.has(id) ? openSteps.delete(id) : openSteps.add(id);
        const m = state.chat.find((x) => x.id === id);
        if (m) upsert(m);
        return;
      }
      const head = e.target.closest(".waiting .card-head");
      if (head) { head.parentElement.classList.toggle("open"); return; }
      const prop = e.target.closest("[data-prop]");
      if (prop) {
        prop.disabled = true;
        const msgId = prop.closest("[data-msg]").dataset.msg;
        try { const m = await api(`/api/chat/${msgId}/proposals/${prop.dataset.prop}`, { method: "POST" }); upsertChat(m); upsert(m); refresh(); }
        catch (err) { prop.disabled = false; toast(err.message, true); }
      }
    });

    const hear = (msg) => { if (msg.topic === "chat") { upsert(msg.data); markSeen(CHAIR); } };
    onMsg.add(hear);
    renderAll();
    markSeen(CHAIR);
    if (!$("form.prompt:not(.answered)", thread)) input.focus({ preventScroll: true });
    let wasReady = ready;
    const update = () => {
      const html = intro();
      if (html !== lastIntro) { $("#intro", thread).innerHTML = html; lastIntro = html; }
      // Once setup finishes, rebuild so the composer gets its normal placeholder and suggestions.
      if (!wasReady && state.overview?.setup?.step === "done") { wasReady = true; route(); }
    };
    return { update, dispose: () => onMsg.delete(hear) };
  },

  async agent(el, id) {
    let detail;
    try { detail = await api(`/api/bots/${id}`); } catch { el.innerHTML = `<div class="empty">This bot no longer exists.</div>`; return {}; }
    if (!state.tabs.includes(id)) { state.tabs.push(id); local.set("tabs", state.tabs); renderTabs(); }
    markSeen(id);
    const trader = detail.role === "trader";
    const c = composer(`Ask the Chair about ${detail.name}`, async (text) => { await ask(text.includes(detail.name) ? text : `${text} (about ${detail.name})`); location.hash = "#/"; });

    el.innerHTML = `<div class="convo">
      <div class="agent-head" id="head"></div>
      <div class="figures" id="figs"></div>
      <div class="thread" id="thread"></div>
      ${c.html}</div>`;
    c.bind(el);

    const patch = async (body) => {
      try { await api(`/api/bots/${id}`, { method: "PATCH", body }); await refresh(); return true; } catch (e) { toast(e.message, true); return false; }
    };

    let headSig = "";
    const head = () => {
      const b = bot(id) ?? detail;
      const acc = account(b.account_id);
      const running = b.enabled && !b.paused_reason;
      const sig = JSON.stringify([b.name, b.profile, running, Object.keys(b.positions ?? {}).length]);
      if (sig === headSig) return; // unchanged: keep the critter mid-animation
      headSig = sig;
      $("#head", el).innerHTML = `
        ${avatar(b, "l")}
        <div><h1>${esc(b.name)} <span class="chip">${esc(roleOf(id))}</span></h1>
          <p>${esc(b.title)} on ${esc(acc?.name ?? "")}. ${esc(b.summary)}</p></div>
        <div class="actions">
          ${trader ? `<span class="chip" title="Risk profile">${esc(profileLabel(b.profile))}</span>` : ""}
          <button class="btn" data-act="toggle">${running ? "Pause" : "Resume"}</button>
          ${trader ? `<button class="btn" data-act="limits">Budget & limits</button><a class="btn" href="#/backtest?bot=${id}">Backtest</a>` : ""}
          ${trader && Object.keys(b.positions ?? {}).length ? `<button class="btn" data-act="flatten">Close positions</button>` : ""}
          <button class="btn danger" data-act="delete">Delete</button>
        </div>`;
    };

    const figs = () => {
      const b = bot(id) ?? detail;
      const f = trader
        ? [["Equity", fmt.money(b.equity)], ["Return", `<span class="${fmt.tone(b.pnl)}">${fmt.pct(b.pnl_pct)}</span>`],
           ["Win rate", b.win_rate == null ? "n/a" : `${Math.round(b.win_rate * 100)}%`], ["Budget", fmt.money(b.budget)]]
        : [["Right", `${Math.round(b.reputation.accuracy * 100)}%`], ["Vote weight", `${b.reputation.weight.toFixed(2)}×`],
           ["Scored calls", b.reputation.calls], ["Watching", `${b.symbols.length} markets`]];
      $("#figs", el).innerHTML = f.map(([k, v]) => `<div class="fig"><small>${k}</small><b>${v}</b></div>`).join("");
    };

    const line = (m) => {
      const d = m.data;
      if (m.topic === "signal") return `<div class="lead enter"><div class="bubble"><p><b>${esc(sym(d.instrument_key))}</b> ${esc(d.note)} <span class="${fmt.tone(d.direction)}">${fmt.dir(d.direction)}</span></p></div><div class="under">${fmt.clock(m.ts)} · confidence ${Math.round(d.confidence * 100)}%</div></div>`;
      if (m.topic === "fill") return `<div class="lead enter"><div class="bubble"><p><b>${d.side === "buy" ? "Bought" : "Sold"} ${fmt.qty(d.qty)} ${esc(sym(d.instrument_key))}</b> at ${fmt.price(d.price)}${d.pnl ? ` · <span class="${fmt.tone(d.pnl)}">${fmt.signed(d.pnl)}</span>` : ""}</p><p class="muted">${esc(d.reason)}</p></div><div class="under">${fmt.clock(m.ts)}</div></div>`;
      if (m.topic === "rejected") return `<div class="lead notice enter"><div class="bubble"><p>Wanted to ${esc(d.side)} ${esc(sym(d.key))}, the risk manager refused: ${esc(d.reason)}.</p></div><div class="under">${fmt.clock(m.ts)}</div></div>`;
      if (m.topic === "bot" && d.event === "paused") return `<div class="lead notice enter"><div class="bubble"><p>Paused by the risk manager: ${esc(d.reason)}</p></div><div class="under">${fmt.clock(m.ts)}</div></div>`;
      return "";
    };
    const mine = (m) => (m.data.source ?? m.data.bot_id ?? m.data.bot) === id && ["signal", "fill", "rejected", "bot"].includes(m.topic);

    const thread = $("#thread", el);
    const extras = () => {
      if (!trader) {
        const b = bot(id) ?? detail;
        const r = b.reputation, trust = b.trust ?? { markets: [], regimes: [] };
        const bar = (row, label) => `<div class="item trust"><span></span><div><b>${esc(label)}</b>
            <div class="meter"><span style="width:${Math.round(row.accuracy * 100)}%;background:${row.accuracy >= 0.5 ? "var(--up)" : "var(--down)"}"></span></div></div>
            <span class="${row.accuracy >= 0.5 ? "up" : "down"}">${Math.round(row.accuracy * 100)}% · ${row.calls}</span></div>`;
        const regimeName = { trend: "When the market trends", chop: "When the market chops sideways" };
        return `<div class="card open"><div class="card-head">Last ${r.history.length} scored calls<span class="meta">${r.hits} right of ${r.calls}</span></div>
          <div class="card-body"><div><div class="item" style="grid-template-columns:1fr"><div class="calls">${r.history.map((h) => `<i class="${h ? "r" : ""}" title="${h ? "Right" : "Wrong"}"></i>`).join("") || '<span class="muted">Scored 8 bars after each call.</span>'}</div></div></div></div></div>
          <div class="card open"><div class="card-head">Where to trust ${esc(b.name)}<span class="meta">right · calls</span></div>
          <div class="card-body"><div>
            ${trust.markets.length || trust.regimes.length
              ? trust.markets.map((m) => bar(m, sym(m.name))).join("") + trust.regimes.map((g) => bar(g, regimeName[g.name] ?? g.name)).join("")
              : '<div class="item" style="grid-template-columns:1fr"><span class="muted">Each market and market mood gets its own record once calls are scored. Its vote counts for more where it has been right.</span></div>'}
          </div></div></div>`;
      }
      const b = bot(id) ?? detail;
      const pos = Object.entries(b.positions ?? {});
      return `<div class="card open"><div class="card-head">Equity<span class="meta">${fmt.money(b.equity)} of ${fmt.money(b.budget)}</span></div>
          <div class="card-body"><div><div class="item" style="grid-template-columns:1fr">${chart(detail.equity_curve, b.budget)}</div></div></div></div>
        ${pos.length ? `<div class="card open"><div class="card-head">Open positions<span class="meta">${pos.length}</span></div><div class="card-body"><div>
          ${pos.map(([k, p]) => { const u = (p.price - p.avg_price) * p.qty; return `<div class="item"><span class="st ok"></span><div><b>${esc(sym(k))}</b><div class="sub"><span>${fmt.qty(p.qty)} at ${fmt.price(p.avg_price)}, now ${fmt.price(p.price)}</span></div></div><span class="${fmt.tone(u)}">${fmt.signed(u)}</span></div>`; }).join("")}
        </div></div></div>` : ""}`;
    };
    const renderThread = () => {
      const b = bot(id) ?? detail;
      const history = state.messages.filter(mine).slice(-50);
      thread.innerHTML = `
        ${b.paused_reason ? `<div class="hint bad">Paused by the risk manager: ${esc(b.paused_reason)}. Press Resume once you have looked into it.</div>` : ""}
        <div id="extras" style="display:grid;gap:12px">${extras()}</div>
        ${history.length ? `<div class="day">${fmt.day(history[0].ts)}</div>${history.map(line).join("")}` : `<div class="empty">${trader ? "No trades yet. The trader is waiting for its setup." : "Nothing said yet. Analysts speak on every new bar."}</div>`}`;
      $$(".enter", thread).forEach((n) => n.classList.remove("enter"));
      // Open at the top: the header, figures and controls are what you came for.
      el.scrollTop = 0;
    };

    el.addEventListener("click", async (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      const b = bot(id) ?? detail;
      if (act === "toggle") { const on = !(b.enabled && !b.paused_reason); if (await patch({ enabled: on })) toast(on ? `${b.name} resumed` : `${b.name} paused`); }
      if (act === "flatten") { await api(`/api/bots/${id}/close`, { method: "POST" }); toast("Positions closed"); refresh(); }
      if (act === "limits") editLimits(b);
      if (act === "delete") {
        const ok = await dialog(`<h2>Delete ${esc(b.name)}?</h2><p>Its trade history stays in the database. A trader with open positions has to close them first.</p>
          <div class="actions"><button class="btn ghost" data-close="">Cancel</button><button class="btn danger" data-close="yes">Delete</button></div>`);
        if (!ok) return;
        try { await api(`/api/bots/${id}`, { method: "DELETE" }); state.tabs = state.tabs.filter((t) => t !== id); local.set("tabs", state.tabs); toast(`${b.name} deleted`); await refresh(); location.hash = "#/"; }
        catch (err) { toast(err.message, true); }
      }
    });

    const editLimits = async (b) => {
      const L = b.limits;
      const profiles = state.catalog.profiles;
      const ok = await dialog(`
        <h2>${esc(b.name)}: budget & limits</h2>
        <form class="form" id="lim">
          <label class="field"><span>Budget</span><input class="input num" name="budget" type="number" min="1" step="any" value="${b.budget}"><small>The most this trader can ever put to work. Real money on your account.</small></label>
          <label class="field"><span>Risk profile</span><select class="input" name="profile">${Object.entries(profiles).map(([k, p]) => `<option value="${k}" ${k === b.profile ? "selected" : ""}>${esc(p.label)}: ${esc(p.hint)}</option>`).join("")}</select>
            <small>Picking a profile fills in the limits below. You can still adjust them.</small></label>
          <div class="two">
            <label class="field"><span>Stop loss %</span><input class="input num" name="stop_loss_pct" type="number" step="0.5" min="0.5" value="${(L.stop_loss_pct * 100).toFixed(1)}"></label>
            <label class="field"><span>Max drawdown %</span><input class="input num" name="max_drawdown_pct" type="number" step="1" min="1" value="${(L.max_drawdown_pct * 100).toFixed(0)}"></label>
            <label class="field"><span>Daily loss limit %</span><input class="input num" name="daily_loss_pct" type="number" step="1" min="1" value="${(L.daily_loss_pct * 100).toFixed(0)}"></label>
            <label class="field"><span>Max per market %</span><input class="input num" name="max_position_pct" type="number" step="5" min="5" max="100" value="${(L.max_position_pct * 100).toFixed(0)}"></label>
          </div>
        </form>
        <div class="actions"><button class="btn ghost" data-close="">Cancel</button><button class="btn primary" data-close="save">Save</button></div>`,
        (d) => {
          $("select[name=profile]", d).addEventListener("change", (e) => {
            const p = profiles[e.target.value].limits;
            for (const k of ["stop_loss_pct", "max_drawdown_pct", "daily_loss_pct", "max_position_pct"]) {
              $(`input[name=${k}]`, d).value = (p[k] * 100).toFixed(k === "stop_loss_pct" ? 1 : 0);
            }
          });
        });
      if (ok !== "save") return;
      const f = new FormData($("#lim"));
      const limits = Object.fromEntries(["stop_loss_pct", "max_drawdown_pct", "daily_loss_pct", "max_position_pct"].map((k) => [k, Number(f.get(k)) / 100]));
      if (await patch({ profile: f.get("profile"), limits, budget: Number(f.get("budget")) })) toast("Saved");
    };

    const hear = (msg) => {
      if (!mine(msg)) return;
      $(".empty", thread)?.remove();
      const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
      thread.insertAdjacentHTML("beforeend", line(msg));
      while (thread.querySelectorAll(".lead").length > 60) thread.querySelector(".lead").remove();
      if (stick) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      markSeen(id);
    };
    onMsg.add(hear);
    head(); figs(); renderThread();
    let tick = 0;
    const update = () => {
      head(); figs();
      if (trader && ++tick % 5 === 0) api(`/api/bots/${id}`).then((d) => { detail = d; $("#extras", el).innerHTML = extras(); }).catch(() => {});
    };
    return { update, dispose: () => onMsg.delete(hear) };
  },

  markets(el) {
    el.innerHTML = `<div class="page"><h1>Markets</h1><p>Every market a bot watches, with the council's reputation-weighted vote. The bar leans toward the side the analysts favour.</p><div class="list" id="mk"></div></div>`;
    let busy = false;
    const update = async () => {
      if (busy) return;
      busy = true;
      const ms = state.overview?.markets ?? {};
      const keys = Object.keys(ms);
      const closes = await Promise.all(keys.map((k) => api(`/api/series?key=${encodeURIComponent(k)}`).catch(() => [])));
      busy = false;
      $("#mk", el).innerHTML = keys.length ? keys.map((k, i) => {
        const m = ms[k], d = Math.max(-1, Math.min(1, m.direction));
        return `<div class="list-row market-row"><div><b>${esc(sym(k))}</b> <span class="num">${fmt.price(m.price)}</span>
            <small>${esc(account(k.split(":")[0])?.name ?? "")} · ${m.voices} analysts · vote ${fmt.dir(d)}${m.risk_off > 0.3 ? ` · risk warnings ${Math.round(m.risk_off * 100)}%` : ""}</small></div>
          ${spark(closes[i])}
          <div class="lean" title="Council vote ${fmt.dir(d)}"><span style="left:${d >= 0 ? 50 : 50 + d * 50}%;width:${Math.abs(d) * 50}%;background:${d >= 0 ? "var(--up)" : "var(--down)"}"></span></div></div>`;
      }).join("") : `<div class="empty">No market is watched yet.</div>`;
    };
    update();
    return { update };
  },

  activity(el) {
    let filter = local.get("activityFilter", "trades");
    const rules = {
      trades: (m) => m.topic === "fill",
      warnings: (m) => m.topic === "rejected" || m.topic === "error" || m.topic === "halt" || (m.topic === "bot" && m.data.event === "paused"),
      signals: (m) => m.topic === "signal" && Math.abs(m.data.direction) >= 0.5,
    };
    const text = (m) => {
      const d = m.data;
      if (m.topic === "fill") return [d.bot_name, `${d.side === "buy" ? "Bought" : "Sold"} ${fmt.qty(d.qty)} ${sym(d.instrument_key)} at ${fmt.price(d.price)}${d.pnl ? ` · ${fmt.signed(d.pnl)}` : ""}. ${d.reason}`];
      if (m.topic === "signal") return [d.bot_name, `${sym(d.instrument_key)}: ${d.note} (${fmt.dir(d.direction)})`];
      if (m.topic === "rejected") return [d.bot_name, `Refused: ${d.reason}`];
      if (m.topic === "bot") return [d.name, `Paused: ${d.reason}`];
      if (m.topic === "halt") return ["Kill switch", d.on ? "Trading halted" : "Trading resumed"];
      return [d.bot_name ?? "Engine", d.message ?? ""];
    };
    const render = () => {
      const items = state.messages.filter(rules[filter]).slice(-150).reverse();
      el.innerHTML = `<div class="page"><h1>Activity</h1><p>What the bots did and why, newest first.</p>
        <div class="toggles" style="margin-bottom:14px">${[["trades", "Trades"], ["warnings", "Warnings"], ["signals", "Strong signals"]].map(([k, l]) => `<button data-f="${k}" class="${k === filter ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class="list">${items.length ? items.map((m) => { const [who, what] = text(m); return `<div class="list-row"><div><b>${esc(who)}</b><small>${esc(what)}</small></div><span class="muted num">${fmt.clock(m.ts)}</span></div>`; }).join("") : `<div class="empty">Nothing here yet.</div>`}</div></div>`;
      $$("[data-f]", el).forEach((b) => b.addEventListener("click", () => { filter = b.dataset.f; local.set("activityFilter", filter); render(); }));
    };
    render();
    const hear = (m) => { if (rules[filter](m)) render(); };
    onMsg.add(hear);
    return { dispose: () => onMsg.delete(hear) };
  },

  accounts(el) {
    const render = () => {
      const accs = state.overview?.accounts ?? [];
      el.innerHTML = `<div class="page">
        <div style="display:flex;align-items:flex-start;gap:12px"><div><h1>Accounts</h1><p>Where Quorum reads prices and places orders. Keys are encrypted on this computer and never leave it.</p></div>
          <a class="btn primary" style="margin-left:auto" href="#/accounts/new">Connect account</a></div>
        <div class="list">${accs.map((a) => `
          <div class="list-row"><div><b>${esc(a.name)}</b> ${statusTag(a)}
            <small>${esc(a.venue)} · ${esc(a.market)} · ${a.venue === "sim" ? "simulated prices" : a.sandbox ? "testnet or practice account" : "real account"}${a.key_hint ? ` · key ${esc(a.key_hint)}` : ""}</small>
            <small>${esc(a.error || a.status?.message || "Checking…")}</small>
            <small>${a.symbols.map(esc).join(", ")}</small></div>
            <div style="display:flex;gap:8px"><button class="btn" data-check="${a.id}">Check</button>${a.venue === "sim" ? "" : `<button class="btn danger" data-remove="${a.id}">Remove</button>`}</div></div>`).join("")}</div></div>`;
      $$("[data-check]", el).forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true;
        try { const a = await api(`/api/accounts/${b.dataset.check}/check`, { method: "POST" }); toast(a.status?.message ?? "Checked", !a.status?.ok); await refresh(); render(); }
        catch (err) { toast(err.message, true); b.disabled = false; }
      }));
      $$("[data-remove]", el).forEach((b) => b.addEventListener("click", async () => {
        const ok = await dialog(`<h2>Remove this account?</h2><p>Its encrypted keys are deleted from this computer. Bots using it must be deleted first.</p><div class="actions"><button class="btn ghost" data-close="">Cancel</button><button class="btn danger" data-close="yes">Remove</button></div>`);
        if (!ok) return;
        try { await api(`/api/accounts/${b.dataset.remove}`, { method: "DELETE" }); toast("Account removed"); await refresh(); render(); } catch (err) { toast(err.message, true); }
      }));
    };
    render();
    return {};
  },

  connect(el) {
    const venues = state.catalog.venues;
    const H = {
      crypto: { symbols: "BTC/USDT, ETH/USDT, SOL/USDT", key: "API key", secret: "API secret", extra: "Passphrase (OKX, KuCoin and Bitget only)", note: "Create a key that can trade but cannot withdraw. Tick testnet below to practise with the exchange's fake money first." },
      stocks: { symbols: "AAPL, MSFT, SPY", key: "API key ID", secret: "Secret key", note: "Alpaca practice (paper) accounts are free and work like a testnet. Alpaca trading keys cannot withdraw money." },
      forex: { symbols: "EUR_USD, GBP_USD, USD_JPY", key: "API token", account: "Account ID, e.g. 101-004-1234567-001", note: "OANDA practice accounts are free. The API cannot withdraw money." },
    };
    let market = "crypto";
    const render = () => {
      const h = H[market];
      el.innerHTML = `<div class="page"><h1>Connect an account</h1><p>Bots only use the budget you give them, but a key is still a key. Use a sub-account holding only what you can afford to lose.</p>
        <form class="form" id="acc">
          <div class="field"><span>Market</span><div class="seg">${["crypto", "stocks", "forex"].map((m) => `<button type="button" data-m="${m}" class="${m === market ? "on" : ""}">${m[0].toUpperCase() + m.slice(1)}</button>`).join("")}</div></div>
          <div class="two">
            <label class="field"><span>Exchange or broker</span><select class="input" name="venue">${venues[market].map((v) => `<option value="${v}">${v}</option>`).join("")}</select></label>
            <label class="field"><span>Name</span><input class="input" name="name" placeholder="Binance sub-account"></label>
          </div>
          <label class="field"><span>${h.key}</span><input class="input" name="api_key" autocomplete="off" spellcheck="false" required></label>
          ${h.secret ? `<label class="field"><span>${h.secret}</span><input class="input" name="secret" type="password" autocomplete="off" required></label>` : ""}
          ${h.extra ? `<label class="field"><span>${h.extra}</span><input class="input" name="password" type="password" autocomplete="off"></label>` : ""}
          ${h.account ? `<label class="field"><span>${h.account}</span><input class="input" name="account_id" required></label>` : ""}
          <label class="field"><span>Markets to watch</span><input class="input" name="symbols" value="${h.symbols}" required><small>Comma separated, written the way the venue writes them.</small></label>
          <label class="check"><input type="checkbox" name="sandbox" checked> This is a testnet or practice account</label>
          <div class="hint">${h.note}</div>
          <div><button class="btn primary" type="submit">Connect and check</button></div>
        </form></div>`;
      $$("[data-m]", el).forEach((b) => b.addEventListener("click", () => { market = b.dataset.m; render(); }));
      $("#acc", el).addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(e.target), btn = $("button[type=submit]", e.target);
        btn.disabled = true; btn.textContent = "Checking…";
        try {
          const a = await api("/api/accounts", { method: "POST", body: {
            market, venue: f.get("venue"), name: f.get("name"), api_key: f.get("api_key") ?? "", secret: f.get("secret") ?? "",
            password: f.get("password") ?? "", account_id: f.get("account_id") ?? "", sandbox: f.get("sandbox") === "on",
            symbols: String(f.get("symbols")).split(",").map((s) => s.trim()).filter(Boolean) } });
          toast(a.status?.message ?? "Connected", !a.status?.ok);
          await refresh();
          location.hash = "#/accounts";
        } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = "Connect and check"; }
      });
    };
    render();
    return {};
  },

  create(el, role) {
    const accs = state.overview.accounts;
    const pick = { role, strategy: null, account: accs[0]?.id, symbols: new Set() };
    const render = () => {
      const list = state.catalog.strategies.filter((s) => s.role === pick.role);
      pick.strategy ??= list[0]?.key;
      const acc = account(pick.account);
      el.innerHTML = `<div class="page"><h1>New ${pick.role}</h1>
        <p>${pick.role === "trader" ? "A trader gets its own budget and places real orders on the account you pick. Use a testnet account to practise." : "An analyst never trades. Its calls feed the council vote, weighted by how often it has been right."}</p>
        <form class="form" id="nb">
          <div class="seg">${["trader", "analyst"].map((r) => `<button type="button" data-r="${r}" class="${r === pick.role ? "on" : ""}">${r === "trader" ? "Trader" : "Analyst"}</button>`).join("")}</div>
          <div class="picks">${list.map((s) => `<button type="button" class="pick${s.key === pick.strategy ? " on" : ""}" data-s="${s.key}">
            ${avatar({ id: s.key, strategy: s.key, role: s.role })}<span><b>${esc(s.persona)} · ${esc(s.title)}</b><small>${esc(s.summary)}</small></span></button>`).join("")}</div>
          <div class="two">
            <label class="field"><span>Account</span><select class="input" name="account">${accs.map((a) => `<option value="${a.id}" ${a.id === pick.account ? "selected" : ""}>${esc(a.name)} · ${a.market}</option>`).join("")}</select></label>
            <label class="field"><span>Name</span><input class="input" name="name" placeholder="${esc(list.find((s) => s.key === pick.strategy)?.persona ?? "")}"></label>
          </div>
          <div class="field"><span>Markets</span><div class="toggles">${(acc?.symbols ?? []).map((s) => `<button type="button" data-sym="${esc(s)}" class="${pick.symbols.has(s) ? "on" : ""}">${esc(s)}</button>`).join("")}</div></div>
          ${pick.role === "trader" ? `<div class="two">
            <label class="field"><span>Budget</span><input class="input num" name="budget" type="number" min="1" step="any" value="100" required><small>Real money on that account.</small></label>
            <label class="field"><span>Risk profile</span><select class="input" name="profile">${Object.entries(state.catalog.profiles).map(([k, p]) => `<option value="${k}" ${k === "balanced" ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
          </div>` : ""}
          <div><button class="btn primary" type="submit">Create ${pick.role}</button></div>
        </form></div>`;
      $$("[data-r]", el).forEach((b) => b.addEventListener("click", () => { pick.role = b.dataset.r; pick.strategy = null; render(); }));
      $$("[data-s]", el).forEach((b) => b.addEventListener("click", () => {
        pick.strategy = b.dataset.s;
        $$("[data-s]", el).forEach((x) => x.classList.toggle("on", x === b));
        $("input[name=name]", el).placeholder = list.find((s) => s.key === pick.strategy)?.persona ?? "";
      }));
      $$("[data-sym]", el).forEach((b) => b.addEventListener("click", () => { const s = b.dataset.sym; pick.symbols.has(s) ? pick.symbols.delete(s) : pick.symbols.add(s); b.classList.toggle("on"); }));
      $("select[name=account]", el).addEventListener("change", (e) => { pick.account = e.target.value; pick.symbols.clear(); render(); });
      $("#nb", el).addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          const b = await api("/api/bots", { method: "POST", body: { strategy: pick.strategy, account_id: pick.account, symbols: [...pick.symbols], name: f.get("name") || undefined, budget: f.get("budget") ? Number(f.get("budget")) : undefined, profile: f.get("profile") || undefined } });
          await refresh();
          toast(`${b.name} joined the council`);
          location.hash = `#/agent/${b.id}`;
        } catch (err) { toast(err.message, true); }
      });
    };
    render();
    return {};
  },

  async backtest(el, params) {
    const accs = state.overview.accounts;
    const traders = state.catalog.strategies.filter((s) => s.role === "trader");
    const fromBot = bot(params.get("bot"));
    const form = {
      strategy: fromBot?.strategy ?? traders[0].key,
      account: fromBot?.account_id ?? accs[0]?.id,
      symbols: new Set(fromBot?.symbols ?? account(accs[0]?.id)?.symbols.slice(0, 2) ?? []),
      bars: local.get("btBars", 2000),
      budget: fromBot?.budget ?? 100,
      profile: fromBot?.profile ?? "balanced",
      params: fromBot?.params, limits: fromBot?.limits, bot_id: fromBot?.id,
    };
    let runs = await api("/api/backtests");
    let selected = runs.find((r) => r.status === "done")?.id ?? null;
    let detail = selected ? await api(`/api/backtests/${selected}`) : null;

    el.innerHTML = `<div class="page"><h1>Backtest</h1>
      <p>Replays history through the same analysts, council, trader and risk manager that run live. Orders decided on a bar fill at the next bar's open, with fees and slippage.</p>
      <div id="btForm"></div><div id="btRun" style="margin-top:22px"></div><div id="btResult"></div><div id="btPast"></div></div>`;

    const renderForm = () => {
      const acc = account(form.account);
      $("#btForm", el).innerHTML = `<form class="form" id="bt">
        <div class="picks">${traders.map((s) => `<button type="button" class="pick${s.key === form.strategy ? " on" : ""}" data-s="${s.key}">
          ${avatar({ id: s.key, strategy: s.key, role: "trader" })}<span><b>${esc(s.title)}</b><small>${esc(s.summary)}</small></span></button>`).join("")}</div>
        <div class="two">
          <label class="field"><span>History from</span><select class="input" name="account">${accs.map((a) => `<option value="${a.id}" ${a.id === form.account ? "selected" : ""}>${esc(a.name)} · ${a.market}</option>`).join("")}</select></label>
          <label class="field"><span>Budget</span><input class="input num" name="budget" type="number" min="1" step="any" value="${form.budget}"></label>
        </div>
        <div class="field"><span>Risk profile</span><div class="seg">${Object.entries(state.catalog.profiles).map(([k, p]) => `<button type="button" data-profile="${k}" class="${k === form.profile ? "on" : ""}">${esc(p.label)}</button>`).join("")}</div>
          <small>${esc(state.catalog.profiles[form.profile].hint)}</small>
        </div>
        <div class="field"><span>Markets</span><div class="toggles">${(acc?.symbols ?? []).map((s) => `<button type="button" data-sym="${esc(s)}" class="${form.symbols.has(s) ? "on" : ""}">${esc(s)}</button>`).join("")}</div></div>
        <div class="field"><span>History length</span><div class="seg">${[500, 1000, 2000, 5000].map((b) => `<button type="button" data-bars="${b}" class="${b === form.bars ? "on" : ""}">${b.toLocaleString("en-US")} bars</button>`).join("")}</div>
          <small>Bars use the account's timeframe. Longer history is slower but far more meaningful.</small></div>
        ${fromBot ? `<div class="hint">Using ${esc(fromBot.name)}'s settings, budget and risk limits.</div>` : ""}
        <div><button class="btn primary" type="submit">Run backtest</button></div>
      </form>`;
      $$("[data-s]", el).forEach((b) => b.addEventListener("click", () => { form.strategy = b.dataset.s; form.params = undefined; form.bot_id = undefined; renderForm(); }));
      $$("[data-sym]", el).forEach((b) => b.addEventListener("click", () => { const s = b.dataset.sym; form.symbols.has(s) ? form.symbols.delete(s) : form.symbols.add(s); b.classList.toggle("on"); }));
      $$("[data-bars]", el).forEach((b) => b.addEventListener("click", () => { form.bars = Number(b.dataset.bars); local.set("btBars", form.bars); renderForm(); }));
      $$("[data-profile]", el).forEach((b) => b.addEventListener("click", () => { form.profile = b.dataset.profile; form.limits = undefined; renderForm(); }));
      $("select[name=account]", el).addEventListener("change", (e) => { form.account = e.target.value; form.symbols.clear(); renderForm(); });
      $("input[name=budget]", el).addEventListener("change", (e) => { form.budget = Number(e.target.value); });
      $("#bt", el).addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const run = await api("/api/backtests", { method: "POST", body: {
            strategy: form.strategy, account_id: form.account, symbols: [...form.symbols], bars: form.bars,
            budget: form.budget, profile: form.profile, params: form.params, limits: form.limits, bot_id: form.bot_id } });
          runs.unshift(run);
          renderRun(run);
          renderPast();
        } catch (err) { toast(err.message, true); }
      });
    };

    const renderRun = (run) => {
      const box = $("#btRun", el);
      if (!run || run.status === "done") { box.innerHTML = ""; return; }
      if (run.status === "failed") { box.innerHTML = `<div class="hint bad">Backtest failed: ${esc(run.error)}</div>`; return; }
      const label = run.status === "loading" ? "Loading history…" : run.status === "queued" ? "Waiting for the previous run…"
        : `Replaying ${run.bars.toLocaleString("en-US")} bars · ${Math.round(run.progress * 100)}%`;
      box.innerHTML = `<div class="card open waiting"><div class="card-head"><span class="st run"></span>${esc(run.persona)} on ${run.symbols.map(esc).join(", ")}<span class="meta">${label}</span></div>
        <div class="card-body"><div><div class="item" style="grid-template-columns:1fr"><div class="bar"><span style="width:${Math.round(run.progress * 100)}%"></span></div></div></div></div></div>`;
    };

    const renderResult = () => {
      const box = $("#btResult", el);
      if (!detail?.result) { box.innerHTML = ""; return; }
      const r = detail.result;
      const beat = r.return - r.buy_hold_return;
      const span = (r.end - r.start) / 86_400_000;
      const figs = [
        ["Return", `<span class="${fmt.tone(r.return)}">${fmt.pct(r.return)}</span>`, `Buy and hold: ${fmt.pct(r.buy_hold_return)}`],
        ["Max drawdown", `<span class="down">${fmt.pct(r.max_drawdown)}</span>`, "Worst fall from a peak"],
        ["Win rate", r.win_rate == null ? "n/a" : `${Math.round(r.win_rate * 100)}%`, `${r.closed_trades} closed of ${r.trades} fills`],
        ["Profit factor", r.profit_factor == null ? "n/a" : r.profit_factor.toFixed(2), "Gross wins ÷ gross losses"],
        ["Sharpe", r.sharpe == null ? "n/a" : r.sharpe.toFixed(2), r.sharpe == null ? "Needs 30+ days of history" : "Annualised"],
        ["In the market", `${Math.round(r.exposure * 100)}%`, `Fees paid ${fmt.money(r.fees)}`],
      ];
      const analysts = [...r.analysts].sort((a, b) => b.accuracy - a.accuracy);
      box.innerHTML = `
        <div class="section-title">${esc(r.persona)} · ${esc(r.title)} on ${r.symbols.map(esc).join(", ")}
          <span class="muted" style="font-weight:400"> · ${r.bars.toLocaleString("en-US")} bars, ${span >= 1 ? `${span.toFixed(1)} days` : `${(span * 24).toFixed(1)} hours`}, ran in ${r.seconds}s</span></div>
        <div class="hint ${beat < 0 ? "bad" : ""}" style="margin-bottom:12px">${beat >= 0
          ? `Beat buy-and-hold by ${fmt.pct(beat).replace("+", "")} on this stretch of history.`
          : `Did ${fmt.pct(beat).replace("−", "")} worse than simply buying and holding. That is common; most strategies lose to holding in a strong trend.`}
          ${r.paused ? ` The risk manager paused it: ${esc(r.paused)}.` : ""}</div>
        <div class="figures" style="padding:0;margin:0;grid-template-columns:repeat(3,minmax(0,1fr))">${figs.map(([k, v, s]) => `<div class="fig"><small>${k}</small><b>${v}</b><small>${s}</small></div>`).join("")}</div>
        <div class="card open" style="margin-top:12px"><div class="card-head">Equity<span class="meta"><span class="key k1"></span>strategy <span class="key k2"></span>buy and hold</span></div>
          <div class="card-body"><div><div class="item" style="grid-template-columns:1fr">${compareChart(r.equity_curve, r.hold_curve, r.budget)}</div></div></div></div>
        <div class="two" style="margin-top:12px;align-items:start">
          <div class="card open"><div class="card-head">Analysts during the test<span class="meta">accuracy</span></div><div class="card-body"><div>
            ${analysts.map((a) => `<div class="item" style="align-items:center">${avatar({ id: a.key, strategy: a.key, role: "analyst" }, "s")}<div><b>${esc(a.persona)}</b> <span class="muted">· ${esc(a.title)}</span></div><span class="${a.accuracy >= 0.5 ? "up" : "down"}">${Math.round(a.accuracy * 100)}% of ${a.calls}</span></div>`).join("")}
          </div></div></div>
          <div class="card open"><div class="card-head">Last trades<span class="meta">${r.trade_log.length}</span></div><div class="card-body"><div style="max-height:420px;overflow-y:auto">
            ${r.trade_log.slice(-40).reverse().map((t) => `<div class="item"><span class="st ${t.pnl > 0 ? "ok" : t.pnl < 0 ? "fail" : "idle"}"></span><div style="min-width:0"><b>${t.side === "buy" ? "Buy" : "Sell"} ${esc(t.symbol)}</b> <span class="muted">at ${fmt.price(t.price)}</span><div class="sub"><span>${esc(t.reason)}</span></div></div><span class="${fmt.tone(t.pnl)}">${t.pnl ? fmt.signed(t.pnl) : ""}</span></div>`).join("") || `<div class="empty">No trades. The setup never triggered.</div>`}
          </div></div></div>
        </div>
        <div class="hint" style="margin-top:12px">A backtest cannot see order-book depth, outages or funding costs, and a strategy tuned to look good on one stretch of history usually disappoints on the next. Treat this as a filter for bad ideas, not proof of a good one. Next step for anything promising: try it on a testnet account.</div>
        ${detail.bot_id ? "" : `<div style="margin-top:12px"><button class="btn" id="adopt">Hire this trader</button></div>`}`;
      $("#adopt", el)?.addEventListener("click", async () => {
        try {
          const b = await api("/api/bots", { method: "POST", body: { strategy: r.strategy, account_id: accs.find((a) => a.name === detail.account)?.id ?? form.account, symbols: r.symbols, budget: r.budget, profile: detail.profile } });
          await refresh(); toast(`${b.name} joined the team`); location.hash = `#/agent/${b.id}`;
        } catch (err) { toast(err.message, true); }
      });
    };

    const renderPast = () => {
      const done = runs.filter((r) => r.status === "done" || r.status === "failed");
      $("#btPast", el).innerHTML = done.length ? `<div class="section-title">Earlier runs</div><div class="list">${done.map((r) => `
        <div class="list-row" data-run="${r.id}" style="cursor:pointer;${r.id === selected ? "background:var(--raised)" : ""}">
          <div><b>${esc(r.persona)}</b> <span class="muted">· ${esc(r.title)} · ${r.symbols.map(esc).join(", ")} · ${r.bars.toLocaleString("en-US")} bars</span>
            <small>${r.status === "failed" ? `Failed: ${esc(r.error)}` : `Buy and hold ${fmt.pct(r.summary.buy_hold_return)} · drawdown ${fmt.pct(r.summary.max_drawdown)} · ${r.summary.trades} fills`}</small></div>
          <span class="${r.status === "done" ? fmt.tone(r.summary.return) : "down"}">${r.status === "done" ? fmt.pct(r.summary.return) : "failed"}</span></div>`).join("")}</div>` : "";
      $$("[data-run]", el).forEach((row) => row.addEventListener("click", async () => {
        selected = row.dataset.run;
        detail = await api(`/api/backtests/${selected}`);
        renderResult(); renderPast();
        $("#btResult", el).scrollIntoView({ behavior: "smooth", block: "start" });
      }));
    };

    const hear = async (msg) => {
      if (msg.topic !== "backtest") return;
      const run = msg.data;
      const i = runs.findIndex((r) => r.id === run.id);
      if (i >= 0) runs[i] = run; else runs.unshift(run);
      renderRun(run);
      if (run.status === "done") {
        selected = run.id;
        detail = await api(`/api/backtests/${run.id}`);
        renderResult(); renderPast();
        toast(`Backtest finished: ${fmt.pct(run.summary.return)}`);
      } else if (run.status === "failed") renderPast();
    };
    onMsg.add(hear);
    renderForm(); renderRun(runs.find((r) => !["done", "failed"].includes(r.status))); renderResult(); renderPast();
    return { dispose: () => onMsg.delete(hear) };
  },

  async settings(el) {
    let s = await api("/api/settings");
    const render = () => {
      el.innerHTML = `<div class="page"><h1>Settings</h1><p>How the Chair talks to you.</p>
        <form class="form" id="st">
          <div class="hint">Without a key the Chair answers from rules: instant, free and offline. With an Anthropic API key it writes its answers with Claude, from the same facts.</div>
          <label class="field"><span>Anthropic API key</span><input class="input" name="claude_key" type="password" autocomplete="off" placeholder="${s.claude.configured ? `Saved (${esc(s.claude.key_hint)})` : "sk-ant-…"}"><small>Encrypted on this computer. Usage is billed to this key.</small></label>
          <label class="field"><span>Model</span><input class="input" name="model" value="${esc(s.claude.model)}"></label>
          <label class="field" style="max-width:240px"><span>Briefing every (minutes)</span><input class="input num" name="briefing_minutes" type="number" min="1" step="1" value="${s.briefing_minutes}"><small>The Chair stays quiet when nothing changed.</small></label>
          <div class="hint">With a key set, each question sends the market votes, analyst readings and bot results to Anthropic. Exchange keys and account details are never sent.</div>
          <div style="display:flex;gap:8px"><button class="btn primary" type="submit">Save</button>${s.claude.configured ? `<button class="btn danger" type="button" id="clearKey">Remove key</button>` : ""}</div>
        </form></div>`;
      $("#st", el).addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(e.target);
        try {
          s = await api("/api/settings", { method: "POST", body: { claude_key: f.get("claude_key") || undefined, model: f.get("model"), briefing_minutes: Number(f.get("briefing_minutes")) } });
          toast(s.claude.configured ? "Saved. The Chair now writes with Claude." : "Saved");
          render();
        } catch (err) { toast(err.message, true); }
      });
      $("#clearKey", el)?.addEventListener("click", async () => { s = await api("/api/settings", { method: "POST", body: { clear_key: true } }); toast("Key removed"); render(); });
    };
    render();
    return {};
  },
};

function statusTag(a) {
  if (!a.status) return `<span class="tag">checking</span>`;
  if (!a.status.ok) return `<span class="tag bad">not working</span>`;
  if (a.venue === "sim") return `<span class="tag">simulator</span>`;
  if (!a.status.can_trade) return `<span class="tag">prices only</span>`;
  return `<span class="tag ok">ready</span>`;
}

function spark(values) {
  if (values.length < 2) return `<span></span>`;
  const w = 140, h = 34, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const path = values.map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / span) * (h - 4)).toFixed(1)}`).join("");
  const col = values.at(-1) >= values[0] ? "var(--up)" : "var(--down)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><path d="${path}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function compareChart(curve, hold, baseline) {
  if (curve.length < 2) return "";
  const w = 800, h = 220, all = [...curve.map((p) => p[1]), ...hold.map((p) => p[1]), baseline];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const y = (v) => h - 10 - ((v - lo) / span) * (h - 20);
  const line = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * w).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  return `<svg class="chart" style="height:220px" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Strategy equity against buy and hold">
    <line x1="0" x2="${w}" y1="${y(baseline)}" y2="${y(baseline)}" stroke="var(--line)" stroke-dasharray="4 5" vector-effect="non-scaling-stroke"/>
    <path d="${line(hold)}" fill="none" stroke="var(--faint)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
    <path d="${line(curve)}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function chart(points, baseline) {
  if (points.length < 2) return `<span class="muted">The curve appears after the first minute.</span>`;
  const w = 600, h = 160, v = points.map((p) => p[1]);
  const lo = Math.min(...v, baseline), hi = Math.max(...v, baseline), span = hi - lo || 1;
  const y = (x) => h - 8 - ((x - lo) / span) * (h - 16);
  const path = v.map((x, i) => `${i ? "L" : "M"}${((i / (v.length - 1)) * w).toFixed(1)},${y(x).toFixed(1)}`).join("");
  const col = v.at(-1) >= baseline ? "var(--up)" : "var(--down)";
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Equity over time">
    <line x1="0" x2="${w}" y1="${y(baseline)}" y2="${y(baseline)}" stroke="var(--line)" stroke-dasharray="4 5" vector-effect="non-scaling-stroke"/>
    <path d="${path}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

// ------------------------------------------------------------------ router

let current = null;

async function route() {
  current?.dispose?.();
  current = null;
  listeners.clear();
  $("#side").classList.remove("open");
  $("#scrim").classList.remove("on");
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const parts = path.split("/").filter(Boolean);
  const el = $("#view");
  const fresh = el.cloneNode(false); // drop listeners bound by the previous view
  el.replaceWith(fresh);
  state.viewing = !parts.length ? CHAIR : parts[0] === "agent" ? parts[1] : null;
  renderTabs(); renderSide(); renderRail();

  let view;
  if (!parts.length) view = views.chair(fresh);
  else if (parts[0] === "agent" && parts[1]) view = await views.agent(fresh, parts[1]);
  else if (parts[0] === "markets") view = views.markets(fresh);
  else if (parts[0] === "activity") view = views.activity(fresh);
  else if (parts[0] === "backtest") view = await views.backtest(fresh, new URLSearchParams(query));
  else if (parts[0] === "accounts" && parts[1] === "new") view = views.connect(fresh);
  else if (parts[0] === "accounts") view = views.accounts(fresh);
  else if (parts[0] === "settings") view = await views.settings(fresh);
  else if (parts[0] === "new") view = views.create(fresh, new URLSearchParams(query).get("role") === "analyst" ? "analyst" : "trader");
  else fresh.innerHTML = `<div class="empty">That page does not exist. <a href="#/">Back to the Chair</a></div>`;

  current = view ?? null;
  if (view?.update) listeners.add(view.update);
}

// ------------------------------------------------------------------ boot

async function boot() {
  installGradients();
  onMsg.add(hearCritters);
  renderRail();
  [state.catalog, state.messages, state.chat] = await Promise.all([api("/api/catalog"), api("/api/messages?limit=3000"), api("/api/chat?limit=150")]);
  for (const m of state.messages) if (m.topic === "signal" && m.data.kind !== "risk") state.lean.set(m.data.source, m.data.direction);
  countUnread();
  await refresh();
  connect();
  window.addEventListener("hashchange", route);
  await route();
  setInterval(refresh, 3000);
}

boot().catch((err) => {
  $("#view").innerHTML = `<div class="empty">The Quorum engine is not answering (${esc(err.message)}). Start it with <code>python run.py</code> and reload.</div>`;
});
