/* NSCLC-Agent · IMPF-AI — the product.
 * A consult workspace: describe a case, the agent (the unchanged Python
 * package, running in worker.js under Pyodide) stages it, plans, audits and
 * releases — or refuses. This file only renders; every value from the agent
 * is inserted as text, never parsed as HTML.
 */
"use strict";

/* ================================================================ utils */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v; /* static icon markup only */
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of [children].flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
const $ = (sel, root = document) => root.querySelector(sel);
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

const ICONS = {
  logo: '<path d="M12 3v7"/><path d="M12 10c-1.5 2-3 2.5-5 2.5"/><path d="M12 10c1.5 2 3 2.5 5 2.5"/><path d="M7 6.5C4.5 7.5 3 11 3 15c0 3 1.5 5 4 5 2 0 3.5-1.5 3.5-4V11"/><path d="M17 6.5c2.5 1 4 4.5 4 8.5 0 3-1.5 5-4 5-2 0-3.5-1.5-3.5-4V11"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  form: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2A5 5 0 0 1 21 19"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17v.01"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  staging: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  kg: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.2 7l7.4.3M7.2 8.2l3.8 7.6M16.9 9.2 13 15.8"/>',
  lab: '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9 12l2 2 4-4"/>',
  eval: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  about: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  consult: '<path d="M9 3h6l1 3H8z"/><rect x="5" y="5" width="14" height="16" rx="2"/><path d="M9 12h6M9 16h4"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  check: '<path d="M4 12.5l5 5L20 6.5"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17.5v.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  siren: '<path d="M7 18v-6a5 5 0 0 1 10 0v6"/><path d="M4 21h16M12 3v2M4.2 7.2l1.4 1.4M19.8 7.2l-1.4 1.4"/>',
  doc: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  plan: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="M3.5 6l1.2 1.2L7 5M3.5 12l1.2 1.2L7 11M3.5 18l1.2 1.2L7 17"/>',
  memory: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  agent: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M9 12v1.5M15 12v1.5M2 12v3M22 12v3"/>',
};
function icon(name, cls) {
  return h("span", { class: cls || "", style: { display: "inline-flex" },
    html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>` });
}
function logoMark(cls) {
  return h("span", { class: cls || "brand-mark", html: `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS.logo}</svg>` });
}

function toast(text, bad) {
  const node = h("div", { class: "toast" + (bad ? " bad" : "") }, text);
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), bad ? 7000 : 3600);
}
function jsonBlock(value) { return h("pre", { class: "json" }, JSON.stringify(value, null, 2)); }
function downloadJSON(name, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click(); a.remove();
}
function pickFile(accept, multiple) {
  return new Promise((resolve) => {
    const input = h("input", { type: "file", accept, multiple: multiple || null, style: { display: "none" } });
    input.addEventListener("change", () => { resolve(Array.from(input.files || [])); input.remove(); });
    document.body.appendChild(input); input.click();
  });
}
function busy(button, label) {
  const original = Array.from(button.childNodes);
  button.disabled = true;
  clear(button); append(button, [h("span", { class: "spinner" }), label || "运行中…"]);
  return () => { button.disabled = false; clear(button); append(button, original); };
}
function empty(text, iconName) { return h("div", { class: "empty" }, icon(iconName || "info"), h("div", null, text)); }
function fmtList(items) {
  if (!items || !items.length) return h("div", { class: "muted small" }, "无");
  return h("ul", { class: "list" }, items.map((x) => h("li", null, typeof x === "string" ? x : JSON.stringify(x))));
}
function timeAgo(ts) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  const d = new Date(ts);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** Browser storage that never throws (private mode, quota, blocked). */
const LS = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } },
};

/* ================================================================ status */

const STATUS = {
  treatment_recommendation: ["ok", "已放行", "循证治疗建议 · 分期内、证据支撑、通过全部安全规则与终审", "check"],
  draft_for_tumor_board: ["accent", "剂量草案", "待 MDT/医师签核 · 剂量来自确定性方案库", "doc"],
  approved_by_tumor_board: ["ok", "已签核", "MDT/医师已签核", "check"],
  needs_more_information: ["warn", "需补充信息", "方案为临时，补齐以下信息后再放行", "question"],
  needs_staging_workup: ["warn", "需完成分期", "TNM 不完整或有歧义，分期引擎拒绝猜测", "staging"],
  insufficient_evidence: ["neutral", "证据不足", "引用或主张未通过证据护栏，未放行", "info"],
  blocked: ["bad", "安全拦截", "终审发现阻断级违规，方案不得交付", "x"],
  failed_closed: ["bad", "故障关闭", "运行异常，按安全默认关闭", "x"],
  emergency_action_plan: ["emergency", "肿瘤急症", "急症短路：固定安全处置脚本，永不经模型改写", "siren"],
  agent_done: ["accent", "模型结论", "模型主导会诊 · 规则引擎复核仅供参考", "sparkle"],
  agent_emergency: ["emergency", "急症提示", "急症筛查命中 · 模型已优先处理", "siren"],
  agent_error: ["bad", "运行失败", "模型调用失败，可重试", "x"],
};
const statusMeta = (s) => STATUS[s] || ["neutral", s || "—", "", "info"];
function statusPill(status) {
  const meta = statusMeta(status);
  const cls = { ok: "ok", accent: "accent", warn: "warn", neutral: "", bad: "bad", emergency: "bad" }[meta[0]];
  return h("span", { class: `pill ${cls}` }, h("span", { class: "dot" }), meta[1]);
}
function statusBanner(status, right) {
  const meta = statusMeta(status);
  return h("div", { class: `banner ${meta[0]}` },
    h("div", { class: "glyph" }, icon(meta[3])),
    h("div", null, h("div", { class: "t" }, meta[1]), h("div", { class: "d" }, meta[2])),
    right ? h("div", { class: "right" }, right) : null);
}

/* ================================================================ bridge */

const STOPPED = "已停止";
const Bridge = {
  worker: null, seq: 0, pending: new Map(), ready: false, restarting: null,
  start() {
    this.worker = new Worker("worker.js");
    this.worker.onmessage = (e) => this.onMessage(e.data);
    this.worker.onerror = (e) => (this.restarting ? this.onMessage({ type: "fatal", error: e.message || "worker error" }) : Boot.fatal(e.message || "worker error"));
  },
  /* Stop = terminate the worker mid-turn (a synchronous model call cannot be
     interrupted from inside it) and boot a fresh one. The page re-applies the
     model and agent settings and resumes the case from its last saved turn. */
  restart() {
    if (this.worker) this.worker.terminate();
    for (const settle of this.pending.values()) settle({ ok: false, error: STOPPED });
    this.pending.clear();
    this.ready = false;
    return new Promise((resolve, reject) => { this.restarting = { resolve, reject }; this.start(); });
  },
  onMessage(msg) {
    if (this.restarting && ["ready", "fatal", "progress"].includes(msg.type)) {
      if (msg.type === "progress") return undefined;
      const r = this.restarting; this.restarting = null;
      if (msg.type === "fatal") return r.reject(new Error(msg.error));
      this.ready = true; return r.resolve();
    }
    if (msg.type === "progress") return Boot.step(msg.stage);
    if (msg.type === "ready") return Boot.done(msg.info, msg.build);
    if (msg.type === "fatal") return Boot.fatal(msg.error);
    if (msg.type === "event") return Workspace.onEvent(msg.data);
    if (msg.type === "result") {
      const resolve = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (resolve) resolve(msg.data);
    }
  },
  call(name, payload, uploads) {
    if (!this.ready) return Promise.reject(new Error("运行时尚未就绪"));
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (data) => (data.ok ? resolve(data.result) : reject(new Error(data.error))));
      const transfer = [];
      if (uploads) for (const list of Object.values(uploads)) for (const f of list) transfer.push(f.bytes);
      this.worker.postMessage({ id, name, payload: payload || {}, uploads }, transfer);
    });
  },
  async timed(name, payload, uploads) {
    const t0 = performance.now();
    const result = await this.call(name, payload, uploads);
    return { result, ms: Math.round(performance.now() - t0) };
  },
};

const Store = { info: null, build: null, catalog: null, examples: [], llm: null, commands: [] };
const llmOn = () => !!(Store.llm && Store.llm.llm && Store.llm.llm.available);
const visionOn = () => !!(Store.llm && Store.llm.vision && Store.llm.vision.provider !== "none");

const Boot = {
  order: ["runtime", "package", "init"],
  step(stage) {
    const idx = this.order.indexOf(stage);
    document.querySelectorAll("#boot-steps [data-step]").forEach((el) => {
      const i = this.order.indexOf(el.dataset.step);
      el.classList.toggle("done", i < idx || idx === -1);
      el.classList.toggle("on", i === idx);
      el.querySelector(".mark").textContent = i < idx || idx === -1 ? "✓" : i === idx ? "●" : "○";
    });
    $("#boot-bar").style.width = `${idx === -1 ? 100 : [18, 62, 86][idx]}%`;
  },
  async done(info, build) {
    Bridge.ready = true;
    Store.info = info; Store.build = build;
    this.step("ready");
    $("#side-version").textContent = `v${info.version}`;
    try {
      [Store.catalog, Store.examples, Store.commands] = await Promise.all([Bridge.call("catalog"), Bridge.call("examples"), Bridge.call("agent_commands")]);
      await Settings.restore();
      await AgentCfg.apply();
    } catch (err) { toast(`初始化失败：${err.message}`, true); }
    Cases.load();
    render();
    setTimeout(() => $("#boot").classList.add("hide"), 150);
  },
  fatal(error) {
    $("#boot-note").textContent = `运行时加载失败：${error}。请检查网络（需访问 cdn.jsdelivr.net）后刷新。`;
    $("#boot-note").style.color = "var(--bad)";
  },
};

/* ================================================================ cases */

const DEFAULT_ROLE = "oncologist";
const Cases = {
  list: [], currentId: null, draft: null, warned: false,
  load() {
    this.list = LS.get("nsclc.cases.v1", []).filter((c) => c && c.id && Array.isArray(c.messages));
    const id = (location.hash.match(/^#\/case\/([\w-]+)/) || [])[1];
    if (id && this.get(id)) this.currentId = id;
    else this.newDraft();
  },
  save() {
    if (LS.set("nsclc.cases.v1", this.list)) return;
    /* Quota: keep every message's text, but only each case's latest report. */
    const slim = this.list.map((c) => {
      const keep = lastResult(c);
      return Object.assign({}, c, { messages: c.messages.map((m) => (m === keep || !m.payload ? m : Object.assign({}, m, { payload: undefined, text: m.payload.reply }))) });
    });
    if (!LS.set("nsclc.cases.v1", slim) && !this.warned) { this.warned = true; toast("本机存储已满：会诊记录未能保存，可在「模型接入」页清理本机记录。", true); }
  },
  get(id) { return this.list.find((c) => c.id === id) || null; },
  current() { return (this.currentId && this.get(this.currentId)) || this.draft || this.newDraft(); },
  newDraft() {
    this.draft = { id: uid(), title: "", created: Date.now(), updated: Date.now(), role: LS.get("nsclc.pref.role", DEFAULT_ROLE), dose: false, mode: defaultMode(), status: null, stage: null, messages: [], session: null };
    this.currentId = null;
    return this.draft;
  },
  commit(c) { /* a draft becomes a case with its first message */
    if (this.get(c.id)) return;
    this.list.unshift(c); this.draft = null; this.currentId = c.id;
    history.replaceState(null, "", `#/case/${c.id}`);
  },
  select(id) {
    if (!this.get(id)) { this.newDraft(); return; }
    this.currentId = id; this.draft = null;
  },
  remove(id) {
    this.list = this.list.filter((c) => c.id !== id);
    if (Session.boundId === id) Session.invalidate();
    if (this.currentId === id) this.newDraft();
    this.save();
  },
  touch(c) { c.updated = Date.now(); this.list.sort((a, b) => b.updated - a.updated); },
};

/** The worker holds ONE consultation session; bind it to the case on screen.
 *  Authority (role, dose permission) always comes from the case settings
 *  here, never from the stored session (same contract as the CLI). */
const Session = {
  boundId: null,
  async bind(c) {
    if (this.boundId === c.id) return;
    if (c.mode === "agent") {
      if (c.session) await Bridge.call("agent_import", { data: c.session, role: c.role });
      else await Bridge.call("agent_new", { role: c.role });
    } else if (c.session) await Bridge.call("chat_import", { data: c.session, role: c.role, allow_dose_planning: !!c.dose });
    else await Bridge.call("chat_new", { role: c.role, allow_dose_planning: !!c.dose });
    this.boundId = c.id;
  },
  invalidate() { this.boundId = null; },
};

function lastResult(c) {
  for (let i = c.messages.length - 1; i >= 0; i--) {
    const m = c.messages[i];
    if (m.role === "agent" && m.payload && !m.whatif) return m;
  }
  return null;
}
const caseFacts = (c) => { const m = lastResult(c); return (m && (m.payload.session_facts || m.payload.facts)) || {}; };
const isAgent = (p) => !!(p && p.mode === "agent");
function defaultMode() { return llmOn() && LS.get("nsclc.pref.mode", "agent") !== "governed" ? "agent" : "governed"; }

/* ============================================================ fact display */

const HIST = { adenocarcinoma: "腺癌", squamous: "鳞癌", adenosquamous: "腺鳞癌", large_cell: "大细胞癌", nsclc_nos: "NSCLC-NOS" };
const NEG_RE = /negative|阴性|wild|野生|not detected|未检出|无突变/i;
const ROLE_LABEL = { oncologist: "肿瘤科医师", patient: "患者", researcher: "研究者" };
const OUTCOME = { progression: "进展", response: "缓解", stable: "稳定", toxicity: "毒性停药" };

function tnmText(tnm) {
  if (!tnm || !(tnm.t || tnm.n || tnm.m)) return "";
  return `${tnm.prefix || "c"}${tnm.t || "T?"} ${tnm.n || "N?"} ${tnm.m || "M?"}`;
}
function keyDriver(f) {
  for (const [g, v] of Object.entries(f.driver_mutations || {})) if (v && !NEG_RE.test(String(v))) return `${g.toUpperCase()} ${String(v).slice(0, 18)}`;
  return "";
}
function factChips(f) {
  if (!f) return [];
  const out = [];
  if (f.tnm) out.push(tnmText(f.tnm));
  if (f.histologic_category) out.push(HIST[f.histologic_category] || f.histologic_category);
  for (const [g, v] of Object.entries(f.driver_mutations || {})) out.push(`${g.toUpperCase()} ${v}`);
  if (f.pd_l1 && f.pd_l1.tps !== undefined) out.push(`PD-L1 ${f.pd_l1.tps}%`);
  if (f.ecog_ps !== undefined) out.push(`ECOG ${f.ecog_ps}`);
  if (f.cns_metastases && f.cns_metastases.status) out.push(f.cns_metastases.status === "present" ? "脑转移" : "无脑转移");
  const crcl = f.organ_function && f.organ_function.renal && f.organ_function.renal.crcl_ml_min;
  if (crcl !== undefined) out.push(`CrCl ${crcl}`);
  for (const e of f.treatment_history || []) out.push(`${e.line ? `${e.line}线 ` : ""}${(e.agents || []).join("+")}${e.status ? ` ${OUTCOME[e.status] || e.status}` : ""}`);
  return out.filter(Boolean).slice(0, 8);
}
function factRows(f) {
  const rows = [];
  const add = (k, v) => { if (v !== undefined && v !== null && v !== "") rows.push([k, v]); };
  if (f.age || f.sex) add("患者", [f.age ? `${f.age} 岁` : "", { female: "女", male: "男" }[f.sex] || f.sex || ""].filter(Boolean).join(" · "));
  if (f.tnm) add("TNM", h("span", { class: "mono" }, tnmText(f.tnm)));
  add("组织学", f.histologic_category ? HIST[f.histologic_category] || f.histologic_category : "");
  const drivers = Object.entries(f.driver_mutations || {});
  if (drivers.length) add("驱动基因", h("div", null, drivers.map(([g, v]) => h("span", { class: "gene" + (NEG_RE.test(String(v)) ? " neg" : "") }, g.toUpperCase(), " ", String(v)))));
  if (f.pd_l1) add("PD-L1", Object.entries(f.pd_l1).map(([k, v]) => `${k.toUpperCase()} ${v}%`).join(" · "));
  if (f.ecog_ps !== undefined) add("ECOG", String(f.ecog_ps));
  if (f.ngs_done !== undefined) add("广谱 NGS", f.ngs_done ? "已完成" : "未完成");
  add("可切除性", { RESECTABLE: "可切除", UNRESECTABLE: "不可切除" }[f.resectability_category] || f.resectability_category);
  if (f.operable !== undefined) add("可耐受手术", f.operable ? "是" : "否");
  add("疾病范围", { OLIGOMETASTATIC: "寡转移", POLYMETASTATIC: "广泛转移" }[f.disease_extent] || f.disease_extent);
  if (f.cns_metastases) {
    const c = f.cns_metastases;
    add("脑转移", [{ present: "有", absent: "无" }[c.status] || c.status, c.symptomatic === true ? "有症状" : c.symptomatic === false ? "无症状" : "",
      c.treated === true ? "已局部治疗" : c.treated === false ? "未治疗" : "", c.leptomeningeal ? "软脑膜" : "", c.burden || ""].filter(Boolean).join(" · "));
  }
  if (f.organ_function) {
    const o = f.organ_function; const parts = [];
    if (o.renal && o.renal.crcl_ml_min !== undefined) parts.push(`CrCl ${o.renal.crcl_ml_min} mL/min`);
    if (o.hepatic && o.hepatic.bilirubin_uln !== undefined) parts.push(`胆红素 ${o.hepatic.bilirubin_uln}×ULN`);
    add("器官功能", parts.join(" · ") || JSON.stringify(o));
  }
  if (f.qtc_ms !== undefined) add("QTc", `${f.qtc_ms} ms`);
  if ((f.treatment_history || []).length) add("治疗史", h("div", null, f.treatment_history.map((e) => h("div", null, `${e.line ? `${e.line} 线 · ` : ""}${(e.agents || []).join(" + ")}${e.status ? ` · ${OUTCOME[e.status] || e.status}` : ""}`))));
  if (f.progression_ngs_done !== undefined) add("进展期 NGS", f.progression_ngs_done ? "已完成" : "未完成");
  if (f.progression_findings) add("进展期发现", Object.entries(f.progression_findings).filter(([, v]) => v).map(([k]) => ({ met_amplification: "MET 扩增", c797s: "C797S", small_cell_transformation: "小细胞转化" }[k] || k)).join("、") || "无");
  add("既往全身治疗", f.prior_systemic_therapy ? String(f.prior_systemic_therapy) : "");
  if (f.bleeding_risk) add("出血风险", f.bleeding_risk.hemoptysis_history === true ? "有咯血史" : f.bleeding_risk.hemoptysis_history === false ? "无咯血史" : JSON.stringify(f.bleeding_risk));
  if (f.b12_folate_started !== undefined) add("叶酸/B12", f.b12_folate_started ? "已开始" : "未开始");
  add("吸烟史", f.smoking_history ? String(f.smoking_history) : "");
  add("体重下降", f.weight_loss ? String(f.weight_loss) : "");
  add("治疗目标", f.goals_of_care ? String(f.goals_of_care) : "");
  if ((f.medications || []).length) add("当前用药", f.medications.join("、"));
  if (f.comorbidities) add("合并症", Object.entries(f.comorbidities).filter(([, v]) => v).map(([k]) => k).join("、") || "无");
  const known = new Set(["age", "sex", "tnm", "histologic_category", "driver_mutations", "pd_l1", "ecog_ps", "ngs_done", "resectability_category", "operable", "disease_extent", "cns_metastases", "organ_function", "qtc_ms", "treatment_history", "progression_ngs_done", "progression_findings", "prior_systemic_therapy", "bleeding_risk", "b12_folate_started", "smoking_history", "weight_loss", "goals_of_care", "medications", "comorbidities", "staging_system", "stage_group"]);
  for (const [k, v] of Object.entries(f)) if (!known.has(k) && !k.startsWith("_")) add(k, typeof v === "object" ? JSON.stringify(v).slice(0, 90) : String(v));
  return rows;
}

/* ============================================================ reply text */

function friendly(text) {
  return String(text)
    .replace(/详见 oncologist 视图 guideline_context/g, "详见完整报告「指南」")
    .replace(/详见 prognosis 输出/g, "详见完整报告「预后」");
}
function formatReply(text) {
  const root = h("div", { class: "reply" });
  let list = null; let listTag = "";
  const flush = () => { if (list) root.appendChild(list); list = null; listTag = ""; };
  friendly(text || "").split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) { flush(); return; }
    if (/^\[[a-z_]+\]/.test(line)) { flush(); return; } /* status footer: the pill already says it */
    const bullet = line.match(/^[•·-]\s*(.+)$/);
    const ordered = line.match(/^(\d+)[.、]\s*(.+)$/);
    if (bullet || ordered) {
      const tag = bullet ? "ul" : "ol";
      if (listTag !== tag) { flush(); list = h(tag); listTag = tag; }
      list.appendChild(h("li", null, bullet ? bullet[1] : ordered[2]));
      return;
    }
    flush();
    let cls = null;
    if (line.startsWith("※")) cls = "note";
    else if (line.startsWith("⚠")) cls = "emerg";
    else if (line.startsWith("【")) cls = "warnline";
    else if (i === 0 && line.startsWith("分期")) cls = "lead";
    root.appendChild(h("p", { class: cls }, line));
  });
  flush();
  return root;
}

/* ================================================================ chips */

function regimenChip(rid) {
  const r = Store.catalog && Store.catalog.regimens[rid];
  return h("span", { class: "chip regimen", title: r ? `${r.name}\n${r.label_note || ""}` : rid }, r ? r.name : rid);
}
function trialChip(tid) {
  const t = Store.catalog && Store.catalog.trials[tid];
  return h("span", { class: "chip trial", title: t ? `${t.name}\n${(t.results || []).join("\n")}\n${t.source || ""}` : tid }, tid);
}

/* ======================================================= consult card */

function consultCard(p, m) {
  const onc = p.views && p.views.oncologist;
  if (!onc) return patientCard(p);
  const meta = statusMeta(p.release_status);
  const rx = h("div", { class: "rx" + (m.whatif ? " whatif" : "") });
  const st = onc.staging || {};
  const plan = onc.treatment_plan || {};
  const violations = p.violations || [];
  const blocks = violations.filter((v) => v.severity === "block");
  const emergency = p.release_status === "emergency_action_plan";

  const metrics = h("div", { class: "metrics" },
    emergency ? null : h("span", { class: `metric ${blocks.length ? "bad" : "ok"}` }, icon(blocks.length ? "x" : "lab"),
      blocks.length ? `阻断 ${blocks.length}` : `终审通过 · ${(Store.info && Store.info.counts.rules) || 20} 条规则`),
    violations.length - blocks.length ? h("span", { class: "metric warn" }, `警示 ${violations.length - blocks.length}`) : null,
    (p.claims || []).length ? h("span", { class: "metric" }, `主张 ${p.claims.length}`) : null,
    (p.evidence || []).length ? h("span", { class: "metric" }, `证据 ${p.evidence.length}`) : null,
    m.ms ? h("span", { class: "metric" }, `${m.ms} ms`) : null,
    p.llm_calls ? h("span", { class: "metric" }, icon("sparkle"), `模型 ${p.llm_calls}`) : null);

  rx.appendChild(h("div", { class: "rx-top" },
    h("div", { class: "rx-stage" },
      h("div", { class: "lbl" }, "分期"),
      h("div", { class: "big" }, st.stage_group || "—"),
      h("div", { class: "tnm" }, st.tnm || (st.stage_group ? "" : "未分期"))),
    h("div", { class: `rx-status tone-${meta[0]}` },
      h("div", { class: "st" }, h("span", { class: "g" }, icon(meta[3])), meta[1], m.whatif ? h("span", { class: "chip trial" }, "假设情景") : null),
      h("div", { class: "d" }, meta[2]),
      metrics)));

  if (emergency && onc.emergency_plan) {
    const e = onc.emergency_plan;
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "emerg-grid" },
      h("div", null, h("div", { class: "lbl" }, "立即处置"), fmtList(e.immediate_actions)),
      h("div", null, h("div", { class: "lbl" }, "不要做"), fmtList(e.do_not)),
      h("div", null, h("div", { class: "lbl" }, "升级条件"), fmtList(e.escalate_if)))));
    return rx;
  }
  const options = plan.options || [];
  if (options.length) {
    rx.appendChild(h("div", { class: "rx-sec" },
      h("div", { class: "lbl" }, p.released ? "推荐方案" : "候选方案", p.released ? null : h("span", { class: "chip warn" }, "未放行 · 仅供医师审阅"),
        plan.intent ? h("span", { class: "chip" }, { curative: "根治性", palliative: "姑息性" }[plan.intent] || plan.intent) : null,
        plan.mdt_referral ? h("span", { class: "chip warn" }, "建议 MDT") : null),
      options.map((o, i) => h("div", { class: "opt" },
        h("span", { class: "n" }, i + 1),
        h("div", null, h("div", { class: "nm" }, o.name),
          o.rationale ? h("div", { class: "why" }, o.rationale) : null,
          (o.regimen_ids || []).length ? h("div", { class: "chips" }, o.regimen_ids.map(regimenChip)) : null)))));
  }
  const workup = plan.workup_needed || [];
  const trials = plan.trial_refs || [];
  if (trials.length || workup.length) {
    rx.appendChild(h("div", { class: "rx-sec" },
      trials.length ? h("div", null, h("div", { class: "lbl" }, "循证锚点"), h("div", { class: "chips" }, trials.map(trialChip))) : null,
      workup.length ? h("div", { style: { marginTop: trials.length ? "12px" : 0 } }, h("div", { class: "lbl" }, "待完善检查"),
        h("div", { class: "todo" }, workup.map((w) => h("div", null, icon("alert"), h("span", null, w))))) : null));
  }
  if (blocks.length) {
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "终审拦截原因"),
      blocks.map((v) => h("div", { class: "violation" }, h("span", { class: "sev block" }, "BLOCK"),
        h("div", null, h("div", { class: "rule" }, v.rule_id), h("div", { class: "msg" }, v.message))))));
  }
  return rx;
}

function patientCard(p) {
  const v = p.views && p.views.patient;
  if (!v) return null;
  const meta = statusMeta(p.release_status);
  const rx = h("div", { class: "rx" });
  rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: `rx-status tone-${meta[0]}`, style: { padding: 0 } },
    h("div", { class: "st" }, h("span", { class: "g" }, icon(meta[3])), meta[1]),
    h("div", { class: "d" }, v.release_status_explained || meta[2]))));
  if ((v.options || []).length) {
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "可与医生讨论的方案"),
      v.options.map((o, i) => h("div", { class: "opt" }, h("span", { class: "n" }, i + 1),
        h("div", null, h("div", { class: "nm" }, o.name), o.rationale ? h("div", { class: "why" }, o.rationale) : null)))));
  }
  if ((v.next_tests || []).length) rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "下一步检查"), fmtList(v.next_tests)));
  if (v.note) rx.appendChild(h("div", { class: "rx-sec muted small" }, v.note));
  return rx;
}

/* ====================================================== full report */

function renderResult(res, ms, opts) {
  const onc = (res.views && res.views.oncologist) || null;
  const patient = res.views && res.views.patient;
  const wrap = h("div", { class: "stack" });
  wrap.appendChild(statusBanner(res.release_status, ms ? `${ms} ms · 浏览器内` : null));
  if (!onc) {
    wrap.appendChild(renderPatientView(patient));
    wrap.appendChild(h("div", { class: "callout" }, icon("info"), h("div", null,
      "患者视角只返回患者视图（与命令行同一契约）。在顶部把视角切换为「肿瘤科医师」可查看分期、证据台账与安全审计。")));
    return wrap;
  }
  if (res.release_status === "emergency_action_plan" && onc.emergency_plan) {
    const e = onc.emergency_plan;
    wrap.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, icon("siren"), h("h3", null, "急症处置（固定安全脚本）")),
      h("div", { class: "grid cols-3" },
        h("div", null, h("div", { class: "muted small" }, "立即处置"), fmtList(e.immediate_actions)),
        h("div", null, h("div", { class: "muted small" }, "不要做"), fmtList(e.do_not)),
        h("div", null, h("div", { class: "muted small" }, "升级条件"), fmtList(e.escalate_if)))));
    return wrap;
  }
  const plan = onc.treatment_plan || {};
  const staging = onc.staging || {};
  wrap.appendChild(h("div", { class: "grid cols-2" },
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "分期（确定性引擎）"), h("span", { class: "sub" }, staging.edition || "")),
      staging.stage_group
        ? h("div", null,
            h("div", { class: "stage-big" }, staging.stage_group, h("small", null, staging.tnm || "")),
            h("div", { class: "kv", style: { marginTop: "14px" } },
              h("div", { class: "k" }, "路由模块"), h("div", null, (onc.routing || {}).module_key || "—"),
              h("div", { class: "k" }, "风险模式"), h("div", null, res.risk_mode || "routine")),
            (staging.migration_notes || []).length ? h("div", { style: { marginTop: "10px" } }, h("div", { class: "muted small" }, "版本迁移注记"), fmtList(staging.migration_notes)) : null)
        : h("div", { class: "muted" }, "未分期 — TNM 不完整或被引擎拒绝（歧义不猜）")),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "方案概要"),
        h("div", { class: "right" }, plan.intent ? h("span", { class: "chip" }, plan.intent) : null, plan.mdt_referral ? h("span", { class: "chip warn" }, "MDT 转诊") : null)),
      h("div", null, plan.summary || "—"),
      (plan.trial_refs || []).length ? h("div", { style: { marginTop: "12px" } }, h("div", { class: "chips" }, plan.trial_refs.map(trialChip))) : null)));
  const options = plan.options || [];
  wrap.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "治疗选项"), h("span", { class: "sub" }, `${options.length} 项`),
      res.released ? null : h("span", { class: "right" }, h("span", { class: "chip warn" }, "未放行 · 仅供审阅"))),
    options.length ? options.map((o, i) => h("div", { class: "option" },
      h("div", { class: "name" }, h("span", { class: "idx" }, i + 1), o.name),
      o.rationale ? h("div", { class: "why" }, o.rationale) : null,
      (o.regimen_ids || []).length ? h("div", { class: "chips" }, o.regimen_ids.map(regimenChip)) : null)) : h("div", { class: "muted" }, "无选项")));
  const violations = res.violations || [];
  wrap.appendChild(h("div", { class: "card" }, tabbed([
    { id: "detail", label: "方案细节", render: () => renderPlanDetail(plan, onc) },
    { id: "audit", label: "安全审计", count: violations.length, render: () => renderAudit(res) },
    { id: "evidence", label: "证据与主张", count: (res.claims || []).length, render: () => renderEvidence(res) },
    { id: "indication", label: "适应证", render: () => renderIndications(res.indication_report) },
    { id: "prognosis", label: "预后", render: () => renderPrognosis(onc.prognosis) },
    { id: "kg", label: "指南", render: () => renderGuidelineContext(onc.guideline_context) },
    { id: "patient", label: "患者视图", render: () => renderPatientView(patient) },
    { id: "dose", label: "剂量通道", render: () => renderDose(onc.dose_plan) },
    { id: "trace", label: "执行轨迹", render: () => renderTrace(res, onc, opts || {}) },
  ])));
  return wrap;
}

function tabbed(defs, initial) {
  const bar = h("div", { class: "tabs" });
  const body = h("div");
  let active = initial || defs[0].id;
  const draw = () => {
    clear(bar); clear(body);
    for (const d of defs) bar.appendChild(h("button", { class: d.id === active ? "on" : "", onclick: () => { active = d.id; draw(); } }, d.label, d.count !== undefined ? h("span", { class: "count" }, d.count) : null));
    body.appendChild(defs.find((d) => d.id === active).render());
  };
  draw();
  return h("div", null, bar, body);
}

function renderPlanDetail(plan, onc) {
  const blocks = h("div", { class: "grid cols-2" });
  blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "不确定性与注记"), fmtList(plan.uncertainties)));
  blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "待完善检查"), fmtList(plan.workup_needed)));
  if ((plan.extrapolations || []).length) blocks.appendChild(h("div", null, h("h4", null, "声明的外推"), jsonBlock(plan.extrapolations)));
  if ((plan.provisional_regimens || []).length) blocks.appendChild(h("div", null, h("h4", null, "暂定方案（待补事实）"), jsonBlock(plan.provisional_regimens)));
  if (plan.organ_gates) {
    blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "器官功能闸门"),
      (plan.organ_gates.failed || []).length ? h("div", { class: "chips", style: { marginBottom: "8px" } }, plan.organ_gates.failed.map((f) => h("span", { class: "chip block" }, `${f.gate}: ${f.note}`))) : h("div", { class: "muted small" }, "无不合格闸门"),
      h("div", { class: "muted small", style: { marginTop: "6px" } }, "剂量通道开启前待补："), fmtList(plan.organ_gates.pending)));
  }
  if (plan.cns) {
    const rd = plan.cns.reading || {};
    const tri = (v) => (v === true ? "是" : v === false ? "否" : "未记录");
    blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "CNS 分层读数"),
      h("div", { class: "kv" }, h("div", { class: "k" }, "状态"), h("div", null, rd.status || "未记录"), h("div", { class: "k" }, "有症状"), h("div", null, tri(rd.symptomatic)),
        h("div", { class: "k" }, "已局部治疗"), h("div", null, tri(rd.treated)), h("div", { class: "k" }, "负荷"), h("div", null, rd.burden || "未记录"),
        h("div", { class: "k" }, "软脑膜"), h("div", null, tri(rd.leptomeningeal))),
      (plan.cns.honest_notes || []).length ? h("div", { class: "muted small", style: { marginTop: "8px" } }, plan.cns.honest_notes.join(" ")) : null));
  }
  if (plan.sequencing) {
    blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "后线序贯"),
      h("div", { class: "kv" }, h("div", { class: "k" }, "当前线次"), h("div", null, h("strong", null, `第 ${plan.sequencing.line || "?"} 线`))),
      (plan.sequencing.honest_notes || []).length ? h("div", { style: { marginTop: "8px" } }, h("div", { class: "muted small" }, "覆盖边界（如实声明）"), fmtList(plan.sequencing.honest_notes)) : null));
  }
  if ((onc.open_questions || []).length) blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "待回答问题"), fmtList(onc.open_questions)));
  if ((onc.flags || []).length) blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "运行标记"), fmtList(onc.flags)));
  return blocks;
}
function renderAudit(res) {
  const v = res.violations || [];
  const issues = (res.issues || []).filter((i) => !v.some((x) => i.startsWith(x.rule_id)));
  return h("div", { class: "stack" },
    h("div", { class: "row" }, h("span", { class: "muted small" }, "已运行检查："), h("div", { class: "chips" }, (res.checks_run || []).map((c) => h("span", { class: "chip ok" }, c)))),
    v.length ? h("div", null, v.map((x) => h("div", { class: "violation" },
      h("span", { class: `sev ${x.severity}` }, x.severity === "block" ? "BLOCK" : "WARN"),
      h("div", null, h("div", { class: "rule" }, x.rule_id), h("div", { class: "msg" }, x.message)))))
      : h("div", { class: "callout" }, icon("check"), h("div", null, "规则引擎无违规 — 该方案通过全部确定性安全规则。")),
    issues.length ? h("div", null, h("h4", { style: { margin: "6px 0" } }, "终审其他发现（主张/引用护栏）"), fmtList(issues)) : null);
}
function renderEvidence(res) {
  const claims = res.claims || [];
  const evidence = res.evidence || [];
  return h("div", { class: "stack" },
    h("h4", null, "主张 → 支持证据（逐主张蕴含）"),
    claims.length ? h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["ID", "类型", "主张", "支持关系", "证据"].map((x) => h("th", null, x)))),
      h("tbody", null, claims.map((c) => h("tr", null, h("td", { class: "mono" }, c.claim_id), h("td", null, c.kind), h("td", null, c.text),
        h("td", null, h("span", { class: "chip" }, c.support_relation)), h("td", { class: "mono" }, (c.evidence_ids || []).join(", ") || "—")))))) : h("div", { class: "muted" }, "无主张"),
    h("h4", { style: { marginTop: "8px" } }, "证据台账（工具声明的证据等级）"),
    h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["ID", "等级", "来源", "摘要", "可放行"].map((x) => h("th", null, x)))),
      h("tbody", null, evidence.map((e) => h("tr", null, h("td", { class: "mono" }, e.evidence_id), h("td", null, h("span", { class: "chip" }, e.level)),
        h("td", null, e.source), h("td", null, e.summary), h("td", null, h("span", { class: `chip ${e.releasable ? "ok" : "warn"}` }, e.releasable ? "是" : "否"))))))));
}
function renderIndications(report) {
  if (!report || !report.regimens) return empty("本次方案无方案级适应证判定");
  return h("div", { class: "stack" }, report.regimens.map((r) => h("div", { class: "option" },
    h("div", { class: "name" }, regimenChip(r.regimen_id), h("span", { class: `chip ${r.verdict === "eligible" ? "ok" : r.verdict === "ineligible" ? "block" : "warn"}` }, r.verdict)),
    (r.failed_conditions || []).length ? h("div", { class: "why" }, "不满足：", r.failed_conditions.join("；")) : null,
    (r.unknown_conditions || []).length ? h("div", { class: "why" }, "待补：", r.unknown_conditions.join("；")) : null)),
    report.note ? h("div", { class: "muted small" }, report.note) : null);
}
function renderPrognosis(p) {
  if (!p) return empty("无预后上下文（仅临床视图、仅有分期时提供）");
  const os = p.five_year_os_percent_approx;
  return h("div", { class: "stack" },
    h("div", { class: "grid cols-3" },
      h("div", { class: "stat" }, h("div", { class: "v" }, os !== null && os !== undefined ? `~${os}%` : "—"), h("div", { class: "k" }, `${p.stage_group} 期 5 年总生存（人群队列）`)),
      h("div", { class: "stat" }, h("div", { class: "v" }, (p.modifiers || []).length), h("div", { class: "k" }, "方向性预后因素")),
      h("div", { class: "stat" }, h("div", { class: "v", style: { fontSize: "15px" } }, p.classification_basis || "—"), h("div", { class: "k" }, "分类依据"))),
    h("div", { class: "callout warn" }, icon("alert"), h("div", null, "人群统计，不是个体预测；系统不为预后因素配数字权重。", p.cohort ? ` 来源：${p.cohort}` : "")),
    (p.modifiers || []).length ? jsonBlock(p.modifiers) : null);
}
function renderGuidelineContext(ctx) {
  if (!ctx) return empty("无相关指南条目");
  const block = (label, items) => (items || []).length ? h("div", null, h("h4", { style: { margin: "4px 0 8px" } }, label),
    items.map((x) => h("div", { class: "option" }, h("div", { class: "name" }, h("span", { class: "chip" }, x.rec_id || ""), x.guideline || ""),
      h("div", { class: "why", style: { marginLeft: 0 } }, x.recommendation || JSON.stringify(x))))) : null;
  return h("div", { class: "stack" },
    h("div", { class: "callout warn" }, icon("alert"), h("div", null, ctx.note || "机器抽取、未经临床复核：仅供权衡。")),
    block("相关推荐", ctx.supporting), block("警示条目", ctx.cautions));
}
function renderPatientView(p) {
  if (!p) return empty("无患者视图");
  return h("div", { class: "stack" },
    h("div", { class: "row" }, statusPill(p.release_status), h("span", { class: "muted small" }, p.release_status_explained || "")),
    p.summary ? h("div", null, p.summary) : h("div", { class: "muted" }, "尚无可展示给患者的建议（未放行的方案不会展示给患者）"),
    (p.options || []).length ? fmtList(p.options.map((o) => `${o.name}${o.rationale ? " — " + o.rationale : ""}`)) : null,
    (p.questions_for_you || []).length ? h("div", null, h("h4", { style: { margin: "6px 0" } }, "需要您回答的问题"), fmtList(p.questions_for_you)) : null,
    (p.next_tests || []).length ? h("div", null, h("h4", { style: { margin: "6px 0" } }, "下一步检查"), fmtList(p.next_tests)) : null,
    p.note ? h("div", { class: "muted small" }, p.note) : null);
}
function renderDose(dose) {
  if (!dose) return h("div", { class: "stack" }, empty("未生成剂量草案"),
    h("div", { class: "muted small" }, "剂量只在：肿瘤科医师视角 + 显式开启剂量草案 + 方案无补检/急症信号 + 全部闸门通过 时由确定性方案库产出，状态为「剂量草案 · 待 MDT/医师签核」。"));
  return h("div", { class: "stack" },
    h("div", { class: "callout warn" }, icon("alert"), h("div", null, "剂量草案 — 必须经 MDT/医师签核；数值来自确定性方案库，模型不得产出剂量。")),
    jsonBlock(dose));
}
function renderTrace(res, onc, opts) {
  return h("div", { class: "grid cols-2" },
    h("div", null, h("h4", { style: { marginBottom: "8px" } }, "任务图"),
      h("div", { class: "chips" }, (res.tasks || []).map((t) => h("span", { class: `chip ${t.status === "ok" ? "ok" : String(t.status).startsWith("skipped") ? "" : "warn"}` }, `${t.agent} · ${t.status}`)))),
    h("div", null, h("h4", { style: { marginBottom: "8px" } }, "运行元数据"), jsonBlock(res.run_meta || {})),
    h("div", { style: { gridColumn: "1 / -1" } }, h("div", { class: "row" },
      opts.latest ? h("button", { class: "btn sm", onclick: async () => downloadJSON("nsclc-run.json", await Bridge.call("export_last")) }, icon("download"), "导出完整运行记录") : null,
      h("button", { class: "btn sm", onclick: () => downloadJSON("nsclc-result.json", res) }, icon("download"), "导出本轮结果 JSON"))),
    (onc.warnings || []).length ? h("div", null, h("h4", null, "告警"), fmtList(onc.warnings)) : null);
}

/* ============================================================ markdown */

/** Minimal, safe Markdown → DOM (headings, lists, tables, quotes, code,
 *  bold/italic/inline code). Text only ever lands in text nodes. */
function inlineMd(text) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0; let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**") || t.startsWith("__")) out.push(h("strong", null, t.slice(2, -2)));
    else if (t.startsWith("`")) out.push(h("code", null, t.slice(1, -1)));
    else out.push(h("em", null, t.slice(1, -1)));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
const LIST_RE = /^\s*([-*+•]|\d+[.)、])\s+/;
function md(text) {
  const root = h("div", { class: "md" });
  const lines = friendly(String(text || "")).replace(/\r/g, "").split("\n");
  const splitRow = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { i++; continue; }
    if (/^\[[a-z_]+\]/.test(trimmed)) { i++; continue; }
    if (trimmed.startsWith("```")) {
      const buf = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) buf.push(lines[i++]);
      i++; root.appendChild(h("pre", { class: "code-block" }, buf.join("\n"))); continue;
    }
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) { root.appendChild(h(`h${Math.min(6, head[1].length + 2)}`, null, inlineMd(head[2]))); i++; continue; }
    if (/^([-*_]\s*){3,}$/.test(trimmed)) { root.appendChild(h("hr")); i++; continue; }
    if (/^\|.*\|$/.test(trimmed) && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
      const header = splitRow(line); i += 2;
      const rows = [];
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) rows.push(splitRow(lines[i++]));
      root.appendChild(h("div", { class: "table-wrap md-table" }, h("table", null,
        h("thead", null, h("tr", null, header.map((c) => h("th", null, inlineMd(c))))),
        h("tbody", null, rows.map((r) => h("tr", null, r.map((c) => h("td", null, inlineMd(c)))))))));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      root.appendChild(h("blockquote", null, md(buf.join("\n")))); continue;
    }
    if (LIST_RE.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const list = h(ordered ? "ol" : "ul");
      while (i < lines.length && LIST_RE.test(lines[i])) {
        const indent = lines[i].match(/^\s*/)[0].length;
        const li = h("li", { class: indent >= 2 ? "sub" : null }, inlineMd(lines[i].replace(LIST_RE, "")));
        list.appendChild(li); i++;
        while (i < lines.length && lines[i].trim() && !LIST_RE.test(lines[i]) && /^\s{2,}/.test(lines[i])) {
          li.appendChild(h("br")); append(li, inlineMd(lines[i].trim())); i++;
        }
      }
      root.appendChild(list); continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*>)/.test(lines[i]) && !LIST_RE.test(lines[i])
      && !(/^\|.*\|$/.test(lines[i].trim()) && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim()))) buf.push(lines[i++]);
    if (!buf.length) buf.push(lines[i++]);
    const para = h("p", { class: buf[0].trim().startsWith("※") ? "note" : null });
    buf.forEach((l, k) => { if (k) para.appendChild(h("br")); append(para, inlineMd(l)); });
    root.appendChild(para);
  }
  return root;
}

/* ======================================================== agent mode UI */

const TOOL_LABEL = {
  record_case_facts: "更新病例笔记", stage_tnm: "分期引擎", screen_emergency: "急症筛查", assess_biomarkers: "驱动基因解析",
  search_trials: "检索试验注册表", search_regimens: "检索方案库", regimen_dosing: "方案库参考剂量", check_indication: "适应证核对",
  check_organ_function: "器官功能核对", cns_assessment: "脑转移分层", later_line_options: "后线序贯", protocol_sections: "临床路径章节",
  guideline_search: "指南知识库", prognosis: "预后（人群）", interaction_check: "药物相互作用", rule_review: "规则引擎复核",
  governed_reference: "受治理流水线参考", read_attachment: "读片 / 读报告", citation_verify: "引用核验", pubmed_search: "PubMed 检索",
  update_plan: "更新会诊计划", delegate: "专科会诊", remember: "提议写入记忆", submit_consult: "提交结论", submit_report: "提交专科意见",
};
const TOOL_ICON = { stage_tnm: "staging", guideline_search: "kg", rule_review: "lab", governed_reference: "lab", screen_emergency: "siren",
  record_case_facts: "pen", read_attachment: "image", search_trials: "doc", search_regimens: "consult", regimen_dosing: "consult",
  update_plan: "plan", delegate: "users", remember: "memory" };
const AGENT_TITLE = { radiology: "影像科医师", pathology: "病理与分子病理科医师", thoracic_surgery: "胸外科医师", radiation_oncology: "放疗科医师",
  medical_oncology: "肿瘤内科医师", pharmacy: "临床药师", evidence: "循证医学研究员" };
const HOOK_TITLE = { emergency_screen: "急症筛查", fact_seed: "病例笔记预填", evidence_ledger: "证据台账", rule_review: "规则引擎",
  stage_consistency: "分期一致性", citation_provenance: "引用溯源", dose_provenance: "剂量溯源", emergency_addressed: "急症优先" };
const CONF = { high: ["ok", "把握度 高"], moderate: ["", "把握度 中"], low: ["warn", "把握度 低"] };
function agentTitle(name) {
  const a = AgentCfg.catalog && (AgentCfg.catalog.agents || []).find((x) => x.name === name);
  return (a && a.title) || AGENT_TITLE[name] || name || "专科";
}
function toolLabel(name) {
  if (TOOL_LABEL[name]) return TOOL_LABEL[name];
  const parts = String(name || "").split("__");
  return parts[0] === "mcp" && parts.length >= 3 ? `${parts[1]} · ${parts.slice(2).join("__")}` : name;
}
function argSummary(name, a) {
  a = a || {};
  if (name === "stage_tnm") return [a.prefix || "c", a.t, a.n, a.m].filter(Boolean).join(" ");
  if (name === "record_case_facts") return Object.keys(a.facts || {}).join("、");
  if (name === "update_plan") return `${(a.items || []).length} 步`;
  if (name === "remember") return "";
  if (a.regimen_ids) return [].concat(a.regimen_ids).join(", ");
  if (a.regimen_id) return a.regimen_id;
  if (a.query || a.stage || a.gene) return [a.query, a.stage, a.gene].filter(Boolean).join(" · ");
  if (a.medications) return [].concat(a.medications).join("、");
  if (a.options) return `${a.options.length} 个方案`;
  if (a.kind) return a.kind;
  const s = JSON.stringify(a);
  return s === "{}" ? "" : s.slice(0, 80);
}

const PLAN_MARK = { pending: "", in_progress: "", completed: "✓", cancelled: "✕" };
function planList(items, compact) {
  const list = items || [];
  const done = list.filter((i) => i.status === "completed").length;
  const pct = list.length ? Math.round((done * 100) / list.length) : 0;
  return h("div", { class: "plan" + (compact ? " compact" : "") },
    h("div", { class: "plan-head" }, icon("plan"), h("span", null, "会诊计划"), h("span", { class: "plan-count" }, `${done}/${list.length}`),
      h("span", { class: "plan-bar" }, h("i", { style: { width: `${pct}%` } }))),
    h("ol", null, list.map((i) => h("li", { class: `p-${i.status}` }, h("span", { class: "pm" }, PLAN_MARK[i.status] || ""), h("span", { class: "pc" }, i.content)))));
}
function contextMeter(ctx) {
  const pct = Math.min(100, Math.max(1, Math.round((ctx.ratio || 0) * 100)));
  const hot = (ctx.ratio || 0) > (ctx.compact_at || 0.75) * 0.85;
  return h("div", { class: "meter-wrap" }, h("div", { class: "meter" + (hot ? " warn" : "") }, h("i", { style: { width: `${pct}%` } })),
    h("span", { class: "muted small" }, `${pct}% · 约 ${ctx.tokens} tokens${ctx.compactions ? ` · 已压缩 ${ctx.compactions} 次` : ""}`));
}
function memoryButton(note) {
  const saved = () => (AgentCfg.cfg.instructions || "").includes(note);
  const b = h("button", { class: "btn sm", type: "button", disabled: saved() || null }, icon(saved() ? "check" : "memory"), saved() ? "已在记忆中" : "保存到记忆");
  b.addEventListener("click", async () => {
    try { await AgentCfg.remember(note); b.disabled = true; append(clear(b), [icon("check"), "已保存"]); toast("已写入记忆：之后的会诊都会遵循"); }
    catch (err) { toast(err.message, true); }
  });
  return b;
}
function specList(label, list) {
  return (list || []).length ? h("div", null, h("div", { class: "lbl" }, label), h("ul", null, list.map((x) => h("li", null, x)))) : null;
}
function specialistStep(step, live) {
  const a = step.args || {};
  const r = step.report || {};
  const pending = step.ok === undefined;
  const kids = step.children || [];
  const conf = CONF[r.confidence];
  const kidTools = kids.filter((s) => s.kind === "tool").length;
  const sub = h("div", { class: "timeline sub" }, kids.map((s) => stepItem(s, live)));
  return h("div", { class: "step s-agent" + (pending ? " s-pending" : step.ok ? "" : " s-fail") },
    h("span", { class: "si" }, pending ? h("span", { class: "spinner" }) : icon("users")),
    h("div", { class: "sb" },
      h("div", { class: "sl" }, step.title || agentTitle(a.agent), h("span", { class: "chip agent" }, "专科子智能体"),
        conf ? h("span", { class: `chip ${conf[0]}` }, conf[1]) : null,
        step.ms !== undefined ? h("span", { class: "ms" }, `${(step.ms / 1000).toFixed(1)} s`) : null),
      a.task ? h("div", { class: "task" }, a.task) : null,
      r.report ? h("div", { class: "st" }, r.report) : pending ? null : h("div", { class: "st" }, step.summary || ""),
      (r.key_points || []).length || (r.recommendations || []).length || (r.concerns || []).length
        ? h("div", { class: "spec-grid" }, specList("要点", r.key_points), specList("建议", r.recommendations), specList("顾虑", r.concerns)) : null,
      kids.length ? (live && pending ? sub : h("details", { class: "subtrace" }, h("summary", null, `专科推理 · ${kidTools} 次工具调用`), sub)) : null));
}
function stepItem(step, live) {
  if (step.kind === "thinking") {
    const text = String(step.text || "");
    return h("div", { class: "step s-thinking" }, h("span", { class: "si" }, icon("sparkle")),
      h("div", { class: "sb" }, h("div", { class: "sl" }, "思考"), h("div", { class: "st" }, text.length > 600 && !live ? `${text.slice(0, 600)}…` : text)));
  }
  if (step.kind === "message") {
    return h("div", { class: "step s-note" }, h("span", { class: "si" }, icon("chat")), h("div", { class: "sb" }, h("div", { class: "st" }, step.text)));
  }
  if (step.kind === "hook") {
    return h("div", { class: "step s-hook" }, h("span", { class: "si" }, icon("lab")),
      h("div", { class: "sb" }, h("div", { class: "sl" }, `Hook · ${step.title || HOOK_TITLE[step.name] || step.name}`), h("div", { class: "st" }, String(step.text || "").replace(/^【[^】]*】/, ""))));
  }
  if (step.kind === "tool" && step.name === "delegate") return specialistStep(step, live);
  if (step.kind === "tool" && step.name === "remember") {
    const note = step.memory || (step.args || {}).note || "";
    return h("div", { class: "step s-memory" }, h("span", { class: "si" }, icon("memory")),
      h("div", { class: "sb" }, h("div", { class: "sl" }, "提议写入记忆", h("span", { class: "sa" }, (step.args || {}).scope || "")), h("div", { class: "st" }, note),
        !live && note ? h("div", { style: { marginTop: "6px" } }, memoryButton(note)) : null));
  }
  if (step.kind === "tool") {
    const pending = step.ok === undefined;
    return h("div", { class: "step s-tool" + (pending ? " s-pending" : step.ok ? "" : " s-fail") + (String(step.name).startsWith("mcp__") ? " s-mcp" : "") },
      h("span", { class: "si" }, pending ? h("span", { class: "spinner" }) : icon(TOOL_ICON[step.name] || (String(step.name).startsWith("mcp__") ? "kg" : "settings"))),
      h("div", { class: "sb" },
        h("div", { class: "sl" }, toolLabel(step.name), h("span", { class: "sa" }, argSummary(step.name, step.args)), step.ms !== undefined ? h("span", { class: "ms" }, `${step.ms} ms`) : null),
        pending ? null : h("div", { class: "st" }, step.summary || "")));
  }
  if (step.kind === "review" || step.kind === "submit") {
    const n = step.findings && step.findings.length !== undefined ? step.findings.length : step.findings;
    return h("div", { class: "step s-review" }, h("span", { class: "si" }, icon("lab")),
      h("div", { class: "sb" }, h("div", { class: "sl" }, step.kind === "submit" ? "提交结论 · Hooks 复核" : "Hooks 复核"),
        h("div", { class: "st" }, n ? `${n} 条参考意见${step.unanswered ? `，${step.unanswered} 条交回模型判断` : ""}` : "无复核意见")));
  }
  if (step.kind === "alert") {
    return h("div", { class: "step s-alert" }, h("span", { class: "si" }, icon("siren")), h("div", { class: "sb" }, h("div", { class: "sl" }, "急症筛查命中"), h("div", { class: "st" }, (step.emergency.signals || []).join("、"))));
  }
  if (step.kind === "compact" || step.kind === "info") {
    return h("div", { class: "step s-note" }, h("span", { class: "si" }, icon("info")), h("div", { class: "sb" }, h("div", { class: "st muted" }, step.text)));
  }
  if (step.kind === "error" || step.kind === "limit") {
    return h("div", { class: "step s-fail" }, h("span", { class: "si" }, icon("alert")), h("div", { class: "sb" }, h("div", { class: "st" }, step.text || step.message)));
  }
  return null;
}
function traceBlock(steps, live) {
  const tools = steps.filter((s) => s.kind === "tool" && s.name !== "update_plan").length;
  const specialists = steps.filter((s) => s.kind === "tool" && s.name === "delegate").length;
  const thoughts = steps.filter((s) => s.kind === "thinking").length;
  const body = h("div", { class: "timeline" }, steps.map((s) => stepItem(s, live)));
  if (live) return h("div", { class: "trace live" }, body);
  return h("details", { class: "trace" }, h("summary", null, icon("sparkle"),
    `推理与工具调用 · ${tools} 次工具调用${specialists ? ` · ${specialists} 位专科会诊` : ""}${thoughts ? ` · ${thoughts} 段思考` : ""}`), body);
}
const DECISION = { accepted: ["ok", "已采纳"], overridden: ["warn", "保留方案 · 已说明理由"] };
function reviewPanel(review) {
  const findings = (review && review.findings) || [];
  if (!findings.length) return h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "Hooks 复核"), h("div", { class: "metrics" }, h("span", { class: "metric ok" }, icon("lab"), "无复核意见 · 规则引擎、分期一致性、引用与剂量溯源已核对")));
  const answers = Object.fromEntries((review.responses || []).filter((r) => r && r.rule_id).map((r) => [r.rule_id, r]));
  return h("div", { class: "rx-sec" },
    h("div", { class: "lbl" }, "Hooks 复核", h("span", { class: "chip" }, "参考意见 · 非硬约束")),
    findings.map((f) => {
      const a = answers[f.rule_id];
      const d = a ? DECISION[a.decision] || ["", a.decision] : ["", "模型未回应"];
      return h("div", { class: "finding" },
        h("div", { class: "fh" }, h("span", { class: `sev ${f.severity}` }, f.severity === "block" ? "高" : "提示"), h("span", { class: "rule" }, f.rule_id),
          f.hook ? h("span", { class: "muted small" }, HOOK_TITLE[f.hook] || f.hook) : null, h("span", { class: `chip ${d[0]}` }, d[1])),
        h("div", { class: "msg" }, f.message),
        a && a.reason ? h("div", { class: "why" }, h("b", null, "模型理由："), a.reason) : null);
    }));
}
function agentCard(p, m) {
  const k = p.consult || {};
  const engine = p.engine_stage || {};
  const stage = k.stage_group || engine.stage_group;
  const status = p.error ? "agent_error" : p.emergency ? "agent_emergency" : "agent_done";
  const meta = statusMeta(status);
  const rx = h("div", { class: "rx agent" });
  const conf = CONF[k.confidence];
  const tools = (p.steps || []).filter((s) => s.kind === "tool" && s.name !== "update_plan").length;
  const ctx = p.context || {};
  rx.appendChild(h("div", { class: "rx-top" },
    h("div", { class: "rx-stage" }, h("div", { class: "lbl" }, "分期"), h("div", { class: "big" }, stage || "—"),
      h("div", { class: "tnm" }, k.tnm || engine.tnm || ""),
      engine.staged && k.stage_group && engine.stage_group && String(k.stage_group).toUpperCase() !== String(engine.stage_group).toUpperCase()
        ? h("div", { class: "chip warn", style: { marginTop: "6px" } }, `引擎：${engine.stage_group}`) : null),
    h("div", { class: `rx-status tone-${meta[0]}` },
      h("div", { class: "st" }, h("span", { class: "g" }, icon(meta[3])), meta[1],
        k.intent ? h("span", { class: "chip" }, { curative: "根治性", palliative: "姑息性", supportive: "支持治疗", emergency: "急症处置", undetermined: "待定" }[k.intent] || k.intent) : null,
        conf ? h("span", { class: `chip ${conf[0]}` }, conf[1]) : null),
      k.assessment ? h("div", { class: "d" }, k.assessment) : h("div", { class: "d" }, meta[2]),
      h("div", { class: "metrics" }, h("span", { class: "metric" }, icon("sparkle"), `${p.model || "模型"} · ${p.llm_calls} 次推理`),
        h("span", { class: "metric" }, `${tools} 次工具调用`), m.ms ? h("span", { class: "metric" }, `${(m.ms / 1000).toFixed(1)} s`) : null,
        ctx.ratio !== undefined ? h("span", { class: "metric", title: `约 ${ctx.tokens} / ${ctx.window} tokens` }, `上下文 ${Math.max(1, Math.round(ctx.ratio * 100))}%`) : null))));
  if (p.emergency) {
    rx.appendChild(h("div", { class: "rx-sec emergency" }, h("div", { class: "lbl" }, "急症筛查（标准处置路径，供参考）"),
      fmtList((p.emergency.pathway || {}).immediate_actions || p.emergency.signals)));
  }
  const options = (k.options || []).filter((o) => o && o.name);
  if (options.length) {
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "模型推荐方案"),
      options.map((o, i) => h("div", { class: "opt" }, h("span", { class: "n" + (o.preferred ? " pref" : "") }, o.preferred ? "★" : i + 1),
        h("div", null, h("div", { class: "nm" }, o.name, o.preferred ? h("span", { class: "chip ok", style: { marginLeft: "8px" } }, "首选") : null),
          o.rationale ? h("div", { class: "why" }, o.rationale) : null,
          (o.regimen_ids || []).length || (o.evidence || []).length ? h("div", { class: "chips" },
            (o.regimen_ids || []).map(regimenChip), (o.evidence || []).map((e) => (Store.catalog && Store.catalog.trials[e] ? trialChip(e) : h("span", { class: "chip" }, e)))) : null)))));
  }
  const specialists = (p.steps || []).filter((s) => s.kind === "tool" && s.name === "delegate" && s.ok);
  if (specialists.length) {
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, `多学科会诊 · ${specialists.length} 位专科子智能体`),
      h("div", { class: "spec-cards" }, specialists.map((s) => {
        const r = s.report || {};
        const c2 = CONF[r.confidence];
        return h("div", { class: "spec-card" }, h("div", { class: "sc-h" }, icon("users"), h("b", null, s.title || agentTitle((s.args || {}).agent)), c2 ? h("span", { class: `chip ${c2[0]}` }, c2[1]) : null),
          h("div", { class: "sc-b" }, (r.key_points || [])[0] || r.report || s.summary || ""));
      }))));
  }
  const memory = p.memory || [];
  if (memory.length) {
    rx.appendChild(h("div", { class: "rx-sec" }, h("div", { class: "lbl" }, "智能体提议记住", h("span", { class: "chip" }, "由您决定是否保存")),
      memory.map((n) => h("div", { class: "mem-row" }, icon("memory"), h("span", null, n), memoryButton(n)))));
  }
  const workup = k.workup || [];
  const warnings = k.warnings || [];
  if (workup.length || warnings.length) {
    rx.appendChild(h("div", { class: "rx-sec" },
      workup.length ? h("div", null, h("div", { class: "lbl" }, "待完善检查"), h("div", { class: "todo" }, workup.map((w) => h("div", null, icon("alert"), h("span", null, w))))) : null,
      warnings.length ? h("div", { style: { marginTop: workup.length ? "12px" : 0 } }, h("div", { class: "lbl" }, "注意事项"), fmtList(warnings)) : null));
  }
  rx.appendChild(reviewPanel(p.review));
  return rx;
}
function openAgentReport(m) {
  const p = m.payload;
  const k = p.consult || {};
  openDrawer({ title: "模型会诊详情", sub: [k.stage_group ? `分期 ${k.stage_group}` : null, p.model, new Date(m.ts).toLocaleString()].filter(Boolean).join(" · "), width: 960,
    body: h("div", { class: "card" }, tabbed([
      { id: "steps", label: "推理与工具", count: (p.steps || []).length, render: () => h("div", { class: "timeline full" }, (p.steps || []).map((s) => {
        const item = stepItem(s, true);
        if (item && s.kind === "tool" && s.args && Object.keys(s.args).length) item.querySelector(".sb").appendChild(h("details", null, h("summary", { class: "muted small" }, "参数"), jsonBlock(s.args)));
        return item;
      })) },
      { id: "plan", label: "会诊计划", count: (p.plan || []).length, render: () => ((p.plan || []).length ? planList(p.plan) : empty("本轮没有会诊计划", "plan")) },
      { id: "mdt", label: "专科意见", count: (p.steps || []).filter((s) => s.name === "delegate").length, render: () => {
        const list = (p.steps || []).filter((s) => s.kind === "tool" && s.name === "delegate");
        return list.length ? h("div", { class: "timeline full" }, list.map((s) => specialistStep(s, false))) : empty("本轮没有请专科会诊（可输入 /mdt）", "users");
      } },
      { id: "review", label: "Hooks 复核", count: ((p.review || {}).findings || []).length, render: () => h("div", { class: "rx" }, reviewPanel(p.review)) },
      { id: "facts", label: "病例笔记", render: () => h("div", { class: "stack" }, h("div", { class: "facts" }, factRows(p.facts || {}).map(([a, b]) => [h("div", { class: "k" }, a), h("div", { class: "v" }, b)])),
        p.engine_stage ? h("div", { class: "muted small" }, `分期引擎：${p.engine_stage.staged ? `${p.engine_stage.tnm} → ${p.engine_stage.stage_group}` : p.engine_stage.refusal}`) : null) },
      { id: "consult", label: "结构化结论", render: () => jsonBlock(k) },
      { id: "raw", label: "原始 JSON", render: () => jsonBlock(p) },
    ])) });
}

/* ================================================================ drawer */

function openDrawer({ title, sub, width, body, foot }) {
  const host = $("#drawer-host");
  clear(host);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => {
    host.classList.remove("open");
    document.removeEventListener("keydown", onKey);
    setTimeout(() => { if (!host.classList.contains("open")) clear(host); }, 260);
  };
  const drawer = h("div", { class: "drawer", role: "dialog", "aria-label": title },
    h("div", { class: "drawer-head" }, h("div", { style: { minWidth: 0, flex: 1 } }, h("div", { class: "t" }, title), sub ? h("div", { class: "s" }, sub) : null),
      h("button", { class: "icon-btn ghost", type: "button", title: "关闭", "aria-label": "关闭", onclick: () => close() }, icon("x"))),
    h("div", { class: "drawer-body" }, body),
    foot ? h("div", { class: "drawer-foot" }, foot) : null);
  drawer.style.setProperty("--dw", `${width || 880}px`);
  host.appendChild(h("div", { class: "drawer-backdrop", onclick: () => close() }));
  host.appendChild(drawer);
  document.addEventListener("keydown", onKey);
  requestAnimationFrame(() => host.classList.add("open"));
  return close;
}
function openReport(m, c) {
  const p = m.payload; if (!p) return;
  const onc = p.views && p.views.oncologist;
  const stage = onc && onc.staging && onc.staging.stage_group;
  const latest = c.messages.filter((x) => x.role === "agent").slice(-1)[0] === m && Session.boundId === c.id;
  openDrawer({ title: m.whatif ? "假设情景 · 完整报告" : "完整会诊报告",
    sub: [stage ? `分期 ${stage}` : null, statusMeta(p.release_status)[1], new Date(m.ts).toLocaleString()].filter(Boolean).join(" · "),
    width: 960, body: renderResult(p, m.ms, { latest }) });
}

/* ============================================================ case form */

const T_OPTIONS = ["", "Tis", "T1mi", "T1a", "T1b", "T1c", "T2a", "T2b", "T3", "T4", "TX"];
const N_OPTIONS = ["", "N0", "N1", "N2a", "N2b", "N2", "N3", "NX"];
const M_OPTIONS = ["", "M0", "M1a", "M1b", "M1c1", "M1c2", "M1c", "MX"];
const TRI = [["", "未记录"], ["true", "是"], ["false", "否"]];
const GENES = ["egfr", "alk", "ros1", "ret", "met", "braf", "ntrk", "her2", "kras"];
const DRIVER_HINTS = ["negative", "L858R", "exon 19 deletion", "exon 20 insertion", "G719X", "L858R + T790M", "EML4-ALK fusion", "CD74-ROS1 fusion", "KIF5B-RET fusion", "exon 14 skipping", "V600E", "G12C", "not tested", "阴性", "19外显子缺失"];
const FACT_FIELDS = [
  { g: "临床", key: "histologic_category", label: "组织学", type: "select", options: [["", "未记录"], ["adenocarcinoma", "腺癌"], ["squamous", "鳞癌"], ["adenosquamous", "腺鳞癌"], ["large_cell", "大细胞癌"], ["nsclc_nos", "NSCLC-NOS"]] },
  { g: "临床", key: "ecog_ps", label: "ECOG PS", type: "int", options: [["", "未记录"], ["0", "0"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"]] },
  { g: "临床", key: "pd_l1.tps", label: "PD-L1 TPS (%)", type: "number" },
  { g: "临床", key: "ngs_done", label: "广谱 NGS 已完成", type: "bool" },
  { g: "临床", key: "resectability_category", label: "可切除性", type: "select", options: [["", "未记录"], ["RESECTABLE", "可切除"], ["UNRESECTABLE", "不可切除"]] },
  { g: "临床", key: "operable", label: "可耐受手术", type: "tri" },
  { g: "临床", key: "disease_extent", label: "疾病范围", type: "select", options: [["", "未记录"], ["OLIGOMETASTATIC", "寡转移"], ["POLYMETASTATIC", "广泛转移"]] },
  { g: "临床", key: "medications", label: "当前用药（逗号分隔）", type: "list" },
  ...GENES.map((gene) => ({ g: "驱动基因（报告原文）", key: `driver_mutations.${gene}`, label: gene.toUpperCase(), type: "driver" })),
  { g: "脑转移（CNS）", key: "cns_metastases.status", label: "状态", type: "select", options: [["", "未记录"], ["absent", "无"], ["present", "有"]] },
  { g: "脑转移（CNS）", key: "cns_metastases.symptomatic", label: "有症状", type: "tri" },
  { g: "脑转移（CNS）", key: "cns_metastases.treated", label: "已局部治疗", type: "tri" },
  { g: "脑转移（CNS）", key: "cns_metastases.leptomeningeal", label: "软脑膜病变", type: "tri" },
  { g: "器官功能与合并症", key: "organ_function.renal.crcl_ml_min", label: "CrCl (mL/min)", type: "number" },
  { g: "器官功能与合并症", key: "organ_function.hepatic.bilirubin_uln", label: "胆红素 (×ULN)", type: "number" },
  { g: "器官功能与合并症", key: "qtc_ms", label: "QTc (ms)", type: "number" },
  { g: "器官功能与合并症", key: "comorbidities.ild", label: "间质性肺病史", type: "tri" },
  { g: "器官功能与合并症", key: "comorbidities.active_autoimmune", label: "活动性自身免疫病", type: "tri" },
  { g: "器官功能与合并症", key: "bleeding_risk.hemoptysis_history", label: "咯血史", type: "tri" },
  { g: "进展与耐药", key: "progression_ngs_done", label: "进展期 NGS 已做", type: "bool" },
  { g: "进展与耐药", key: "progression_findings.met_amplification", label: "MET 扩增", type: "bool" },
  { g: "进展与耐药", key: "progression_findings.c797s", label: "C797S", type: "bool" },
  { g: "进展与耐药", key: "progression_findings.small_cell_transformation", label: "小细胞转化", type: "bool" },
];
function getPath(obj, path) { return path.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), obj); }
function setPath(obj, path, value) {
  const keys = path.split("."); let o = obj;
  keys.slice(0, -1).forEach((k) => { if (!o[k] || typeof o[k] !== "object") o[k] = {}; o = o[k]; });
  o[keys[keys.length - 1]] = value;
}
function deepMerge(a, b) {
  const out = Object.assign({}, a);
  for (const [k, v] of Object.entries(b || {})) out[k] = v && typeof v === "object" && !Array.isArray(v) && a[k] && typeof a[k] === "object" ? deepMerge(a[k], v) : v;
  return out;
}
function selectEl(options, value, onchange) {
  return h("select", { onchange: (e) => onchange(e.target.value) },
    options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o || "—"]; return h("option", { value: v, selected: String(v) === String(value) ? true : null }, l); }));
}
function inputEl(type, value, onchange, placeholder, list) {
  return h("input", { type, value: value === undefined || value === null ? "" : value, placeholder: placeholder || "", list: list || null, oninput: (e) => onchange(e.target.value) });
}
function formState(facts) {
  const tnm = facts.tnm || {};
  const fields = {};
  for (const f of FACT_FIELDS) {
    const v = getPath(facts, f.key);
    if (v !== undefined && v !== null) fields[f.key] = f.type === "list" && Array.isArray(v) ? v.join(", ") : String(v);
  }
  const history = (facts.treatment_history || []).map((e) => ({ line: e.line ? String(e.line) : "", agents: (e.agents || []).join(", "), status: e.status || "" }));
  return { t: tnm.t || "", n: tnm.n || "", m: tnm.m || "", prefix: tnm.prefix || "c", fields, history, extra: "", narrative: "" };
}
const normHistory = (list) => list.filter((e) => e.agents.trim()).map((e) => ({
  line: e.line ? parseInt(e.line, 10) : undefined, agents: e.agents.split(/[,，+]/).map((s) => s.trim()).filter(Boolean), status: e.status || undefined }));
/** Only what the clinician CHANGED is sent — restating an unchanged value
 *  would count as confirming it (e.g. a fact the report reader proposed). */
function diffFacts(init, cur) {
  const out = {};
  if (["t", "n", "m", "prefix"].some((k) => (cur[k] || "") !== (init[k] || ""))) {
    const tnm = {};
    for (const k of ["t", "n", "m"]) if (cur[k]) tnm[k] = cur[k];
    if (Object.keys(tnm).length) out.tnm = Object.assign(tnm, { prefix: cur.prefix || "c" });
  }
  for (const f of FACT_FIELDS) {
    const raw = cur.fields[f.key] === undefined ? "" : cur.fields[f.key];
    if (raw === "" || raw === (init.fields[f.key] === undefined ? "" : init.fields[f.key])) continue;
    let value = raw;
    if (f.type === "int") value = parseInt(raw, 10);
    else if (f.type === "number") { value = Number(raw); if (Number.isNaN(value)) continue; }
    else if (f.type === "bool" || f.type === "tri") value = raw === "true";
    else if (f.type === "list") value = raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    setPath(out, f.key, value);
  }
  const hCur = normHistory(cur.history);
  if (hCur.length && JSON.stringify(hCur) !== JSON.stringify(normHistory(init.history))) out.treatment_history = hCur;
  return cur.extra.trim() ? deepMerge(out, JSON.parse(cur.extra)) : out;
}

function openCaseForm(c) {
  const known = caseFacts(c);
  const init = formState(known);
  const cur = JSON.parse(JSON.stringify(init));
  const fieldLabel = (text, control, key, cls) => {
    const label = h("label", { class: "field" + (cls ? ` ${cls}` : "") }, text, control);
    const mark = () => label.classList.toggle("changed", (cur.fields[key] || "") !== (init.fields[key] || ""));
    control.addEventListener("input", mark); control.addEventListener("change", mark);
    return label;
  };
  const body = h("div", { class: "stack" });
  body.appendChild(h("div", { class: "callout" }, icon("info"), h("div", null,
    Object.keys(known).length
      ? "已按当前病例档案预填。只有您修改过的字段（蓝色描边）会提交 —— 未改动的值不会被当作「确认」；清空字段不会删除已记录的事实。"
      : "填写已知信息即可，未填写的字段不会提交。TNM 由确定性引擎分期，N2 / M1c 未细分会被拒绝并提示所需检查。")));
  body.appendChild(h("fieldset", null, h("legend", null, "分期与叙述"),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "T", selectEl(T_OPTIONS, cur.t, (v) => (cur.t = v))),
      h("label", { class: "field" }, "N", selectEl(N_OPTIONS, cur.n, (v) => (cur.n = v))),
      h("label", { class: "field" }, "M", selectEl(M_OPTIONS, cur.m, (v) => (cur.m = v))),
      h("label", { class: "field" }, "前缀", selectEl([["c", "c（临床）"], ["p", "p（病理）"], ["yp", "yp（新辅助后）"]], cur.prefix, (v) => (cur.prefix = v))),
      h("label", { class: "field span-4" }, "补充叙述（可选，随本次提交一起发送）", h("span", { class: "hint" }, "例：PET-CT+脑MRI 确认 M0；无咯血、无下肢无力"),
        h("textarea", { rows: 2, oninput: (e) => (cur.narrative = e.target.value) })))));
  const groups = {};
  for (const f of FACT_FIELDS) (groups[f.g] = groups[f.g] || []).push(f);
  body.appendChild(h("datalist", { id: "driver-hints" }, DRIVER_HINTS.map((d) => h("option", { value: d }))));
  for (const [g, fields] of Object.entries(groups)) {
    const grid = h("div", { class: "form-grid" });
    for (const f of fields) {
      const val = cur.fields[f.key] || "";
      const set = (v) => { cur.fields[f.key] = v; };
      let control;
      if (f.type === "select" || f.type === "int") control = selectEl(f.options, val, set);
      else if (f.type === "tri" || f.type === "bool") control = selectEl(TRI, val, set);
      else if (f.type === "number") control = inputEl("number", val, set);
      else if (f.type === "driver") control = inputEl("text", val, set, "报告原文，如 L858R / negative", "driver-hints");
      else control = inputEl("text", val, set);
      grid.appendChild(fieldLabel(f.label, control, f.key, f.type === "list" ? "span-2" : ""));
    }
    body.appendChild(h("fieldset", null, h("legend", null, g), grid));
  }
  const histHost = h("div", { class: "stack" });
  const drawHistory = () => {
    clear(histHost);
    cur.history.forEach((e, idx) => histHost.appendChild(h("div", { class: "form-grid" },
      h("label", { class: "field" }, "线次", inputEl("number", e.line, (v) => (e.line = v))),
      h("label", { class: "field span-2" }, "药物（逗号分隔）", inputEl("text", e.agents, (v) => (e.agents = v), "osimertinib / carboplatin, pemetrexed")),
      h("label", { class: "field" }, "结局", h("div", { class: "row", style: { flexWrap: "nowrap" } },
        selectEl([["", "未记录"], ["progression", "进展"], ["response", "缓解"], ["stable", "稳定"], ["toxicity", "毒性停药"]], e.status, (v) => (e.status = v)),
        h("button", { class: "icon-btn", type: "button", title: "删除", onclick: () => { cur.history.splice(idx, 1); drawHistory(); } }, icon("x")))))));
    histHost.appendChild(h("button", { class: "btn sm", type: "button", style: { alignSelf: "flex-start" }, onclick: () => { cur.history.push({ line: String(cur.history.length + 1), agents: "", status: "" }); drawHistory(); } }, icon("plus"), "添加一线治疗"));
  };
  drawHistory();
  body.appendChild(h("fieldset", null, h("legend", null, "治疗史（后线序贯只在「进展」时触发）"), histHost));
  body.appendChild(h("details", null, h("summary", { class: "muted small", style: { cursor: "pointer" } }, "高级：附加事实 JSON"),
    h("label", { class: "field", style: { marginTop: "8px" } }, "与上面合并，JSON 优先",
      h("textarea", { class: "code", oninput: (e) => (cur.extra = e.target.value), placeholder: '{"prior_systemic_therapy": "carboplatin-pemetrexed"}' }))));
  const submit = h("button", { class: "btn primary", type: "button" }, icon("send"), "提交给智能体");
  const close = openDrawer({ title: "结构化病例录入", sub: "确定性校验后并入会诊 · 未改动的字段不会提交", width: 760, body,
    foot: [h("span", { class: "muted small", style: { marginRight: "auto" } }, "提交即运行一轮完整受治理会诊"),
      h("button", { class: "btn", type: "button", onclick: () => close() }, "取消"), submit] });
  submit.addEventListener("click", () => {
    let facts;
    try { facts = diffFacts(init, cur); } catch (err) { toast(`附加 JSON 无效：${err.message}`, true); return; }
    const text = cur.narrative.trim();
    if (!Object.keys(facts).length && !text) { toast("没有新增或修改的信息"); return; }
    close();
    Workspace.send({ text: text || "（结构化录入）", facts, structured: true });
  });
}

/* ============================================================ workspace */

const Workspace = {
  text: "", whatif: false, panel: false, busy: false,
  pending: { images: [], reports: [] },
  dossier: LS.get("nsclc.ui.dossier", true),
  ta: null, live: null, liveEl: null, thread: null, stopping: false, palList: [], palIdx: 0, starts: [],

  /* Live agent progress (worker events arrive while the turn runs). Events
     from a specialist sub-agent nest under the lead's delegate step. */
  onEvent(ev) {
    const L = this.live;
    if (!L) return;
    if (ev.type === "plan") { L.plan = ev.items; this.paintLive(); return; }
    if (ev.type === "usage" || ev.type === "turn_start" || ev.type === "turn_end") return;
    let steps = L.steps;
    if (ev.agent && ev.agent !== "lead") {
      const host = [...L.steps].reverse().find((x) => x.kind === "tool" && x.name === "delegate" && (x.args || {}).agent === ev.agent && x.ok === undefined);
      if (!host) return;
      host.children = host.children || [];
      steps = host.children;
      if (ev.type === "subagent_start") { host.title = ev.title; this.paintLive(); return; }
      if (ev.type === "subagent_end") return;
      if (ev.type === "llm_call") { L.calls += 1; this.paintLive(); return; }
    } else if (ev.type === "llm_call") { L.step = ev.step; L.calls += 1; }
    if (ev.type === "thinking") steps.push({ kind: "thinking", text: ev.text });
    else if (ev.type === "message") steps.push({ kind: "message", text: ev.text });
    else if (ev.type === "tool_call") steps.push({ kind: "tool", id: ev.id, name: ev.name, args: ev.args });
    else if (ev.type === "tool_result") {
      const s = steps.find((x) => x.kind === "tool" && x.id && x.id === ev.id)
        || [...steps].reverse().find((x) => x.kind === "tool" && x.name === ev.name && x.ok === undefined);
      if (s) Object.assign(s, ev, { kind: "tool" });
      if (ev.plan) L.plan = ev.plan;
    } else if (ev.type === "review") steps.push({ kind: "review", findings: ev.findings, unanswered: ev.unanswered });
    else if (ev.type === "hook") steps.push({ kind: "hook", name: ev.name, title: ev.title, text: ev.text });
    else if (ev.type === "alert") steps.push({ kind: "alert", emergency: ev.emergency });
    else if (ev.type === "compact_start") steps.push({ kind: "info", text: `正在压缩 ${ev.turns} 轮早期会诊…` });
    else if (ev.type === "compact") steps.push({ kind: "info", text: `上下文已压缩（${ev.by_model ? "模型撰写摘要" : "确定性摘要"}）：${ev.before} → ${ev.after} tokens` });
    else if (ev.type === "mcp") for (const sv of ev.servers || []) steps.push(sv.ok ? { kind: "info", text: `MCP ${sv.name}：已连接，${(sv.tools || []).length} 个工具` } : { kind: "error", message: `MCP ${sv.name} 连接失败：${sv.error}` });
    else if (ev.type === "error") steps.push({ kind: "error", message: ev.message });
    else return;
    this.paintLive();
  },
  paintLive() {
    const el = this.liveEl; if (!el || !this.live) return;
    const L = this.live;
    const thread = this.thread;
    const nearBottom = thread && thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160;
    clear(el);
    const tools = L.steps.filter((x) => x.kind === "tool" && x.name !== "update_plan").length;
    const specialists = L.steps.filter((x) => x.kind === "tool" && x.name === "delegate").length;
    el.appendChild(h("div", { class: "thinking" }, h("span", { class: "dots" }, h("i"), h("i"), h("i")),
      h("span", { class: "shimmer" }, `模型主导会诊中 · 第 ${L.step || 1} 步${tools ? ` · ${tools} 次工具调用` : ""}${specialists ? ` · ${specialists} 位专科会诊` : ""}${L.calls > 1 ? ` · ${L.calls} 次模型调用` : ""}…`),
      h("span", { class: "muted small hide-narrow", style: { marginLeft: "auto" } }, "Esc 停止")));
    if (L.plan && L.plan.length) el.appendChild(planList(L.plan, true));
    if (L.steps.length) el.appendChild(traceBlock(L.steps, true));
    if (nearBottom) thread.scrollTop = thread.scrollHeight;
  },

  reset() { this.text = ""; this.whatif = false; this.pending = { images: [], reports: [] }; },

  render() {
    const c = Cases.current();
    const hasTurns = c.messages.length > 0;
    if (!hasTurns) c.mode = defaultMode(); /* an empty case follows the current settings */
    const root = h("section", { class: "ws" + (this.dossier && hasTurns ? "" : " no-dossier") });
    root.appendChild(this.bar(c));
    const inner = h("div", { class: "thread-inner" });
    const thread = h("div", { class: "thread" }, inner);
    const col = h("div", { class: "thread-col" + (hasTurns ? "" : " is-empty") }, thread);
    const composer = this.composer(c);
    if (!hasTurns) inner.appendChild(this.welcome(composer));
    else {
      this.starts = c.mode === "agent" && !this.busy ? this.turnStarts(c) : [];
      c.messages.forEach((m, i) => inner.appendChild(this.turn(m, c, i)));
      this.liveEl = null;
      if (this.busy && c.mode === "agent" && this.live) {
        this.liveEl = h("div", { class: "turn-body" });
        inner.appendChild(h("div", { class: "turn agent" }, logoMark("avatar"), this.liveEl));
        this.paintLive();
      } else if (this.busy) inner.appendChild(h("div", { class: "turn agent" }, logoMark("avatar"),
        h("div", { class: "turn-body" }, h("div", { class: "thinking" }, h("span", { class: "dots" }, h("i"), h("i"), h("i")),
          h("span", { class: "shimmer" }, llmOn() ? "模型辅助推理中 · 分期与安全规则由确定性内核裁决…" : "正在会诊 · 急症筛查 → 分期 → 方案 → 安全终审…")))));
      const needsModel = c.mode === "agent" && !llmOn();
      col.appendChild(h("div", { class: "dock" }, h("div", { class: "dock-inner" },
        needsModel ? h("div", { class: "callout warn", style: { marginBottom: "10px" } }, icon("key"), h("div", { style: { flex: 1 } },
          "这是模型主导的会诊，当前未接入模型（为安全起见，密钥默认只保存在当前页面内存中，刷新后需重新接入）。"),
          h("button", { class: "btn sm", type: "button", onclick: () => { location.hash = "#/settings"; } }, "接入模型")) : null,
        composer,
        h("div", { class: "disclaimer" }, "NSCLC-Agent 由 IMPF-AI 研发 · 仅供教学与研究，不构成医疗建议，治疗决定须由主治团队确认"))));
    }
    root.appendChild(h("div", { class: "ws-body" }, col, h("aside", { class: "dossier", "aria-label": "病例档案" }, this.dossierPanel(c))));
    this.thread = thread;
    requestAnimationFrame(() => {
      /* Land on the latest exchange: bottom, unless that hides its prompt. */
      thread.scrollTop = thread.scrollHeight;
      const users = inner.querySelectorAll(".turn.user");
      const lastUser = users[users.length - 1];
      if (lastUser) {
        const top = lastUser.getBoundingClientRect().top - thread.getBoundingClientRect().top;
        if (top < 0) thread.scrollTop += top - 16;
      }
      if (this.ta && !this.busy && window.matchMedia("(pointer: fine)").matches) this.ta.focus();
    });
    return root;
  },

  bar(c) {
    const last = lastResult(c);
    const turns = c.messages.filter((m) => m.role === "user").length;
    const roleSel = h("select", { class: "mini-select", title: "视角决定可见内容与剂量授权", "aria-label": "视角", onchange: (e) => this.setRole(c, e.target.value) },
      Object.entries(ROLE_LABEL).map(([v, l]) => h("option", { value: v, selected: c.role === v || null }, l)));
    const dose = c.mode === "agent" ? null : h("label", { class: "switch hide-narrow", title: "仅肿瘤科医师；全部闸门通过时由确定性方案库产出剂量草案，待签核" },
      h("input", { type: "checkbox", checked: c.dose || null, disabled: c.role !== "oncologist" || null, onchange: (e) => this.setDose(c, e.target.checked) }), "剂量草案");
    const modeCtl = c.messages.length
      ? h("span", { class: `pill ${c.mode === "agent" ? "indigo" : ""} hide-narrow`, title: c.mode === "agent" ? "模型主导：自主推理与调用工具，规则仅供参考" : "受治理：确定性内核裁决分期、规则与放行" }, h("span", { class: "dot" }), c.mode === "agent" ? "模型主导" : "受治理")
      : h("div", { class: "seg hide-narrow", role: "group", "aria-label": "会诊模式" },
          h("button", { type: "button", class: c.mode === "agent" ? "on" : "", disabled: !llmOn() || null, title: llmOn() ? "模型主导：自主推理、调用工具，规则引擎复核仅供参考" : "需先在「模型接入」中接入模型", onclick: () => this.setMode("agent") }, "模型主导"),
          h("button", { type: "button", class: c.mode !== "agent" ? "on" : "", title: "受治理：确定性内核裁决分期、规则与放行", onclick: () => this.setMode("governed") }, "受治理"));
    return h("header", { class: "ws-bar" },
      h("button", { class: "icon-btn ghost only-mobile", type: "button", title: "会诊记录", "aria-label": "打开会诊记录", onclick: toggleRail }, icon("menu")),
      h("div", { class: "ws-title" },
        h("div", { class: "t" }, c.title || "新会诊"),
        h("div", { class: "s" }, last ? statusPill(isAgent(last.payload) ? (last.payload.error ? "agent_error" : last.payload.emergency ? "agent_emergency" : "agent_done") : last.payload.release_status) : null,
          turns ? `${turns} 轮` : "描述病例开始会诊", h("span", { class: "hide-narrow" }, `· ${ROLE_LABEL[c.role] || c.role}视角`))),
      h("span", { class: "spacer" }),
      modeCtl, roleSel, dose,
      h("button", { class: "icon-btn ghost", type: "button", title: "导入会诊文件", "aria-label": "导入会诊", onclick: () => this.importCase() }, icon("upload")),
      c.messages.length ? h("button", { class: "icon-btn ghost", type: "button", title: "导出本次会诊", "aria-label": "导出会诊", onclick: () => this.exportCase(c) }, icon("download")) : null,
      h("button", { class: "icon-btn ghost", type: "button", title: "病例档案", "aria-label": "病例档案", onclick: () => this.toggleDossier(c) }, icon("panel")));
  },

  welcome(composer) {
    const tone = (id) => (id === "emergency" ? ["siren", "emergency"] : id === "renal" || id === "cns_symptomatic" ? ["alert", "warn"] : ["consult", ""]);
    const agent = Cases.current().mode === "agent";
    const model = llmOn() ? `${Store.llm.llm.provider} · ${Store.llm.llm.model}` : "";
    return h("div", { class: "stack", style: { gap: "22px" } },
      h("div", { class: "welcome" }, logoMark(),
        h("h1", null, "今天会诊哪位患者？"),
        h("p", null, agent
          ? `由 ${model} 主导会诊：自主规划，调用分期引擎、试验库、方案库、指南知识库等 20 个临床工具，并可并行邀请 7 位专科子智能体（MDT）；Hooks 的安全复核只作参考，由模型逐条判断。输入 / 查看命令。`
          : "描述病例、补充检查结果或上传报告。NSCLC-Agent 完成确定性分期、循证方案与安全终审 —— 未通过终审的方案不会放行。"),
        h("div", { class: "trust" },
          agent ? h("span", null, icon("sparkle"), `模型主导 · ${Store.llm.llm.model}`) : h("span", null, icon("cpu"), "浏览器内运行"),
          llmOn() ? h("span", null, icon("lock"), `数据直连 ${Store.llm.llm.provider}，不经第三方服务器`) : h("span", null, icon("lock"), "病例不离开本机"),
          agent ? h("span", null, icon("users"), "专科子智能体 · 并行会诊") : null,
          agent ? h("span", null, icon("lab"), "Hooks 复核 · 非硬约束") : h("span", null, icon("lab"), `${(Store.info && Store.info.counts.rules) || 20} 条安全规则终审`),
          h("span", null, icon("staging"), "AJCC/UICC 第 9 版分期"))),
      llmOn() ? null : h("div", { class: "callout" }, icon("sparkle"), h("div", null, "当前为确定性模式。在左下角「模型」中接入 Poe 或 MiniMax 后，会诊将由模型主导：自主推理、调用工具，规则仅作参考。")),
      composer,
      h("div", null,
        h("div", { class: "starters-title" }, "从典型病例开始"),
        h("div", { class: "starters" }, (Store.examples || []).map((e) => {
          const [ic, cls] = tone(e.id);
          return h("button", { class: "starter", type: "button", onclick: () => this.runExample(e) },
            h("span", { class: `ic ${cls}` }, icon(ic)), h("span", null, h("div", { class: "t" }, e.title), h("div", { class: "s" }, e.subtitle)));
        }))),
      h("div", { class: "disclaimer" }, "NSCLC-Agent 由 IMPF-AI 研发 · 仅供教学与研究，不构成医疗建议"));
  },

  composer(c) {
    const agentMode = c.mode === "agent";
    const ph = this.whatif ? "描述假设变化，例如：如果 PD-L1 为 10% 会怎样？"
      : agentMode ? "描述病例或补充信息；输入 / 使用命令（/mdt 多学科会诊 · /plan · /review · /rewind …）"
        : "描述病例或补充信息…  例：65岁男性，肺腺癌 cT2aN1M0，EGFR 阴性，PD-L1 60%，脑MRI阴性";
    const ta = h("textarea", { rows: 1, "aria-label": "会诊输入", placeholder: Bridge.ready ? ph : "运行时重启中…" });
    ta.value = this.text;
    const stopMode = this.busy && agentMode;
    const sendBtn = stopMode
      ? h("button", { class: "send stop", type: "button", title: "停止本轮（Esc）", "aria-label": "停止", disabled: this.stopping || null, onclick: () => this.stop() }, this.stopping ? h("span", { class: "spinner" }) : icon("stop"))
      : h("button", { class: "send", type: "button", title: "发送（Enter；Shift+Enter 换行）", "aria-label": "发送", disabled: !this.canSend() || null, onclick: () => this.send() }, icon("send"));
    const grow = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`; };
    /* Slash-command palette (agent mode). */
    const pal = h("div", { class: "palette", role: "listbox", "aria-label": "命令" });
    pal.hidden = true;
    const choose = (x) => { ta.value = `/${x.name}${x.args ? " " : ""}`; this.text = ta.value; paintPal(); if (!stopMode) sendBtn.disabled = !this.canSend(); ta.focus(); };
    const paintPal = () => {
      const v = ta.value;
      const list = agentMode && /^\/\S*$/.test(v) ? (Store.commands || []).filter((x) => x.name.startsWith(v.slice(1).toLowerCase())) : [];
      this.palList = list;
      if (!(this.palIdx < list.length)) this.palIdx = 0;
      clear(pal);
      pal.hidden = !list.length;
      list.forEach((x, i) => pal.appendChild(h("div", { class: "cmd" + (i === this.palIdx ? " on" : ""), role: "option", "aria-selected": i === this.palIdx ? "true" : "false", onmousedown: (e) => { e.preventDefault(); choose(x); } },
        h("span", { class: "cn" }, `/${x.name}`), x.args ? h("span", { class: "ca" }, x.args) : null, h("span", { class: "cd" }, x.description),
        h("span", { class: `ck ${x.kind}` }, x.kind === "prompt" ? "交给智能体" : "本地"))));
    };
    ta.addEventListener("input", () => { this.text = ta.value; grow(); paintPal(); if (!stopMode) sendBtn.disabled = !this.canSend(); });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && stopMode) { e.preventDefault(); this.stop(); return; }
      const list = pal.hidden ? [] : this.palList || [];
      if (list.length) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); this.palIdx = (this.palIdx + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length; paintPal(); return; }
        if (e.key === "Escape") { e.preventDefault(); pal.hidden = true; return; }
        if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.isComposing && ta.value !== `/${list[this.palIdx].name}`)) { e.preventDefault(); choose(list[this.palIdx]); return; }
      }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); this.send(); }
    });
    requestAnimationFrame(() => { grow(); paintPal(); });
    const hasBaseline = !!lastResult(c);
    const attach = h("div", { class: "attach-list" });
    for (const kind of ["images", "reports"]) this.pending[kind].forEach((f, i) => attach.appendChild(h("span", { class: "chip" }, icon(kind === "images" ? "image" : "doc"), f.name,
      h("button", { type: "button", title: "移除", onclick: () => { this.pending[kind].splice(i, 1); render(); } }, "×"))));
    const pick = async (kind) => {
      const files = await pickFile("image/*", true);
      for (const file of files) this.pending[kind].push({ name: file.name, bytes: await file.arrayBuffer() });
      if (files.length && !visionOn()) toast("读片/读报告需要在「模型接入」中配置视觉模型（如 Poe · gemini-3.1-pro）；未配置时附件会被标记跳过。");
      render();
    };
    const tool = (ic, label, title, onclick, on, disabled) => h("button", { class: "tool" + (on ? " on" : ""), type: "button", title, "aria-label": label, "aria-pressed": on ? "true" : null, disabled: disabled || null, onclick }, icon(ic), h("span", { class: "lbl" }, label));
    this.ta = ta;
    return h("div", { class: "composer-wrap" }, pal, h("div", { class: "composer" + (this.whatif ? " whatif" : "") },
      attach.childNodes.length ? attach : null,
      ta,
      h("div", { class: "composer-tools" },
        tool("form", "结构化录入", "用表单录入 TNM、驱动基因、器官功能、治疗史", () => openCaseForm(c), false, this.busy),
        tool("image", "影像", "上传 CT/MRI 影像（需视觉模型）", () => pick("images")),
        tool("doc", "报告", "上传病理/NGS/影像报告图片（需视觉模型）", () => pick("reports")),
        agentMode
          ? tool("users", "MDT", "召集多学科会诊：主诊智能体并行邀请专科子智能体评估，再综合（/mdt）", () => { this.text = `/mdt ${this.text.trim()}`.trim(); this.send(); }, false, this.busy || (!this.text.trim() && !hasBaseline))
          : tool("users", "MDT 面板", llmOn() ? "召集多学科会诊面板" : "需在「模型接入」中接入模型", () => { this.panel = !this.panel; render(); }, this.panel && llmOn(), !llmOn()),
        agentMode
          ? tool("branch", "假设推演", "让模型在不改变病例笔记的前提下推演一个假设（/whatif）", () => this.insert("/whatif "), false, !hasBaseline || this.busy)
          : tool("branch", "假设推演", hasBaseline ? "what-if：在当前病例上推演一个假设变化，不写入病例" : "需要先有一轮会诊结果", () => { this.whatif = !this.whatif; render(); }, this.whatif, !hasBaseline),
        sendBtn)));
  },

  canSend() { return Bridge.ready && !this.busy && !!(this.text.trim() || this.pending.images.length || this.pending.reports.length); },

  async send(opts) {
    if (this.busy || !Bridge.ready) return;
    const c = Cases.current();
    const fromComposer = !opts;
    const text = (fromComposer ? this.text : opts.text || "").trim();
    const facts = (opts && opts.facts) || null;
    const uploads = fromComposer ? { images: this.pending.images, reports: this.pending.reports } : { images: [], reports: [] };
    if (!text && !facts && !uploads.images.length && !uploads.reports.length) return;
    /* A structured submission or an example is always a real turn. */
    if (!c.messages.length) c.mode = defaultMode();
    const agent = c.mode === "agent";
    if (agent && !llmOn()) { toast("这是模型主导的会诊：请先在「模型接入」中接入模型，或新建一个受治理会诊。", true); return; }
    let prompt = text;
    let command = null;
    if (agent && text.startsWith("/")) {
      let ex;
      try { ex = await this.command(c, text); } catch (err) { toast(err.message, true); return; }
      if (!ex) return;
      prompt = ex.prompt; command = ex.command;
    }
    const whatif = !agent && fromComposer && this.whatif && !!lastResult(c);
    Cases.commit(c);
    const userMsg = { id: uid(), role: "user", ts: Date.now(), text: text || "（附件）", command, whatif, facts, attachments: [...uploads.images, ...uploads.reports].map((f) => f.name) };
    c.messages.push(userMsg);
    if (fromComposer) { this.text = ""; this.pending = { images: [], reports: [] }; }
    this.busy = true;
    this.live = agent ? { steps: [], step: 1, calls: 0, plan: null } : null;
    Cases.touch(c); Cases.save(); render();
    try {
      await Session.bind(c);
      if (agent) {
        const out = await Bridge.timed("agent_turn", { message: prompt, facts }, uploads);
        const r = out.result;
        c.messages.push({ id: uid(), role: "agent", ts: Date.now(), whatif: false, ms: out.ms, payload: r });
        const k = r.consult || {};
        c.status = r.error ? "agent_error" : r.emergency ? "agent_emergency" : "agent_done";
        c.stage = k.stage_group || (r.engine_stage && r.engine_stage.stage_group) || c.stage;
        const f = r.facts || {};
        c.title = [c.stage, keyDriver(f) || (f.histologic_category ? HIST[f.histologic_category] : "")].filter(Boolean).join(" · ") || c.title || (command ? `/${command}` : text.slice(0, 18));
        c.session = await Bridge.call("agent_export");
        return;
      }
      const out = whatif
        ? await Bridge.timed("chat_whatif", { description: text, facts })
        : await Bridge.timed("chat_turn", { message: text, facts, enable_panel: this.panel && llmOn() }, uploads);
      c.messages.push({ id: uid(), role: "agent", ts: Date.now(), whatif, ms: out.ms, payload: out.result });
      if (whatif) this.whatif = false;
      else {
        const r = out.result;
        const onc = r.views && r.views.oncologist;
        const f = r.session_facts || {};
        c.status = r.release_status;
        c.stage = (onc && onc.staging && onc.staging.stage_group) || c.stage;
        c.title = [c.stage, keyDriver(f) || (f.histologic_category ? HIST[f.histologic_category] : "")].filter(Boolean).join(" · ")
          || c.title || (r.release_status === "emergency_action_plan" ? "肿瘤急症" : text.slice(0, 18));
      }
      c.session = await Bridge.call("chat_export");
    } catch (err) {
      if (err.message === STOPPED) {
        /* The worker was restarted: the case resumes from its last saved turn. */
        userMsg.stopped = true;
        c.messages.push({ id: uid(), role: "note", ts: Date.now(), text: "已停止本轮。本轮未计入会诊记忆，会诊已恢复到上一轮结束时的状态；原输入已放回输入框。" });
        if (!this.text.trim()) this.text = text;
      } else c.messages.push({ id: uid(), role: "error", ts: Date.now(), text: err.message });
      Session.invalidate();
    } finally {
      this.busy = false; this.live = null;
      Cases.touch(c); Cases.save(); render();
    }
  },

  /* Slash commands: prompt commands come back as the prompt to send; local
     ones run here (rewind and clear are page-level: they change the thread). */
  async command(c, text) {
    const [, rawName = "", rawArg = ""] = /^\/(\S*)\s*([\s\S]*)$/.exec(text.trim()) || [];
    const name = rawName.toLowerCase();
    if (name === "clear") {
      this.text = ""; this.reset(); Cases.newDraft();
      if (currentRoute().caseId) location.hash = "#/"; else render();
      toast("已开始新会诊 · 记忆与设置保留");
      return null;
    }
    if (name === "rewind") {
      const starts = this.turnStarts(c);
      if (!starts.length) { toast("还没有可回退的轮次", true); return null; }
      const n = parseInt(rawArg, 10);
      const idx = Number.isFinite(n) ? starts[n - 1] : starts[starts.length - 1];
      if (idx === undefined) { toast(`没有第 ${n} 轮（共 ${starts.length} 轮）`, true); return null; }
      this.text = "";
      await this.rewindTo(c, idx);
      return null;
    }
    await Session.bind(c);
    const ex = await Bridge.call("agent_command", { text });
    if (ex.kind === "unknown") { toast(ex.message, true); return null; }
    if (ex.kind !== "local") return { prompt: ex.prompt || text, command: ex.command || null };
    this.text = "";
    if (c.messages.length) {
      c.messages.push({ id: uid(), role: "note", ts: Date.now(), command: ex.command, text: ex.text });
      if (ex.command === "compact") c.session = await Bridge.call("agent_export");
      Cases.touch(c); Cases.save(); render();
    } else {
      render();
      openDrawer({ title: `/${ex.command}`, sub: "本地命令", width: 680, body: h("div", { class: "card" }, md(ex.text)) });
    }
    return null;
  },

  /* Index of the user message that started each completed agent turn. */
  turnStarts(c) {
    const out = [];
    let user = -1;
    c.messages.forEach((x, i) => {
      if (x.role === "user" && !x.stopped) user = i;
      else if (x.role === "agent" && isAgent(x.payload) && user >= 0) { out.push(user); user = -1; }
    });
    return out;
  },

  async rewindTo(c, idx) {
    if (this.busy) return;
    const m = c.messages[idx];
    const turn = c.messages.slice(0, idx).filter((x) => x.role === "agent" && isAgent(x.payload)).length;
    try {
      await Session.bind(c);
      await Bridge.call("agent_rewind", { turn });
      c.messages = c.messages.slice(0, idx);
      c.session = await Bridge.call("agent_export");
      const last = lastResult(c);
      c.status = last ? (last.payload.error ? "agent_error" : last.payload.emergency ? "agent_emergency" : "agent_done") : null;
      c.stage = last ? ((last.payload.consult || {}).stage_group || (last.payload.engine_stage || {}).stage_group || null) : null;
      if (m && m.text && m.text !== "（附件）") this.text = m.text;
      Cases.touch(c); Cases.save(); render();
      toast(`已回退到第 ${turn + 1} 轮之前 · 病例笔记、会诊计划与证据台账已恢复`);
    } catch (err) { toast(`回退失败：${err.message}`, true); Session.invalidate(); }
  },

  async stop() {
    if (!this.busy || this.stopping) return;
    this.stopping = true;
    render();
    try {
      await Bridge.restart();
      await Settings.apply(true);
      await AgentCfg.apply();
      toast("已停止本轮 · 运行时已重新就绪");
    } catch (err) { toast(`重启运行时失败：${err.message}`, true); }
    finally { this.stopping = false; render(); }
  },

  setMode(mode) {
    if (mode === "agent" && !llmOn()) { toast("模型主导需要先接入模型"); location.hash = "#/settings"; return; }
    LS.set("nsclc.pref.mode", mode);
    toast(mode === "agent" ? "新会诊将由模型主导：自主推理与调用工具，规则仅作参考" : "新会诊使用受治理流水线：确定性内核裁决");
    render();
  },

  runExample(e) {
    const k = e.case || {};
    const facts = JSON.parse(JSON.stringify(k.facts || {}));
    if (k.t || k.n || k.m) { facts.tnm = { prefix: k.prefix || "c" }; for (const x of ["t", "n", "m"]) if (k[x]) facts.tnm[x] = k[x]; }
    this.send({ text: [k.presentation, k.question].filter(Boolean).join(" "), facts: Object.keys(facts).length ? facts : null });
  },

  turn(m, c, idx) {
    if (m.role === "note") {
      return h("div", { class: "turn note" }, h("div", { class: "note-card" },
        h("div", { class: "note-h" }, m.command ? h("span", { class: "chip agent" }, `/${m.command}`) : icon("info"), h("span", { class: "muted small" }, timeAgo(m.ts))),
        md(m.text)));
    }
    if (m.role === "user") {
      const chips = factChips(m.facts);
      const canRewind = this.starts.includes(idx);
      return h("div", { class: "turn user" + (m.whatif ? " whatif" : "") + (m.stopped ? " stopped" : "") }, h("div", { class: "ucol" },
        h("div", { class: "bubble" + (m.command ? " cmd" : "") },
          m.whatif ? h("div", { class: "small", style: { color: "var(--indigo)", fontWeight: 700, marginBottom: "4px" } }, "假设推演") : null,
          m.text,
          chips.length ? h("div", { class: "chips" }, chips.map((x) => h("span", { class: "chip" }, x))) : null,
          (m.attachments || []).length ? h("div", { class: "chips" }, m.attachments.map((a) => h("span", { class: "chip" }, icon("doc"), a))) : null),
        m.stopped || canRewind ? h("div", { class: "uact" },
          m.stopped ? h("span", { class: "chip warn" }, "已停止 · 未计入会诊") : null,
          canRewind ? h("button", { class: "act", type: "button", title: "回退到这一轮之前：撤销这一轮及之后的会诊，病例笔记、会诊计划与证据台账一并恢复", onclick: () => this.rewindTo(c, idx) }, icon("undo"), "回退到此") : null) : null));
    }
    if (m.role === "error") {
      return h("div", { class: "turn agent" }, logoMark("avatar"), h("div", { class: "turn-body" },
        h("div", { class: "callout warn" }, icon("alert"), h("div", null, `本轮运行失败：${m.text}`,
          h("div", { class: "small muted" }, "会诊记录已保留，可修改后重试；若接入了模型，请在「模型接入」检查密钥与网络。")))));
    }
    const p = m.payload;
    const isLastAgent = c.messages.slice(idx + 1).every((x) => x.role !== "agent");
    if (isAgent(p)) {
      const k = p.consult || {};
      const questions = k.questions || [];
      return h("div", { class: "turn agent" }, logoMark("avatar"),
        h("div", { class: "turn-body" },
          h("div", { class: "turn-head" }, h("b", null, "NSCLC-Agent"), statusPill(p.error ? "agent_error" : p.emergency ? "agent_emergency" : "agent_done"),
            h("span", { class: "chip trial" }, "模型主导"), h("span", { class: "muted small" }, timeAgo(m.ts))),
          (p.steps || []).length ? traceBlock(p.steps, false) : null,
          md(p.reply),
          agentCard(p, m),
          isLastAgent && questions.length ? h("div", null,
            h("div", { class: "starters-title", style: { margin: "2px 2px 8px" } }, "智能体需要您补充"),
            h("div", { class: "qs" }, questions.map((q) => h("button", { class: "q", type: "button", onclick: () => this.insert(`${q}\n答：`) },
              icon("question"), h("span", null, q), h("span", { class: "go" }, "回答"))))) : null,
          h("div", { class: "turn-actions" },
            h("button", { class: "act", type: "button", onclick: () => openAgentReport(m) }, icon("doc"), "会诊详情"),
            h("button", { class: "act", type: "button", onclick: () => copyText(p.reply) }, icon("copy"), "复制"),
            h("button", { class: "act", type: "button", onclick: () => downloadJSON("nsclc-agent-turn.json", p) }, icon("download"), "JSON"))));
    }
    if (!p && m.text) {
      return h("div", { class: "turn agent" }, logoMark("avatar"), h("div", { class: "turn-body" },
        h("div", { class: "turn-head" }, h("b", null, "NSCLC-Agent"), h("span", { class: "muted small" }, timeAgo(m.ts))), md(m.text)));
    }
    const onc = p && p.views && p.views.oncologist;
    const questions = p ? ((onc && onc.open_questions) || (p.views && p.views.patient && p.views.patient.questions_for_you) || []) : [];
    return h("div", { class: "turn agent" }, logoMark("avatar"),
      h("div", { class: "turn-body" },
        h("div", { class: "turn-head" }, h("b", null, "NSCLC-Agent"), p ? statusPill(p.release_status) : null,
          m.whatif ? h("span", { class: "chip trial" }, "假设推演 · 不写入病例") : null,
          p && p.plan_reused ? h("span", { class: "chip" }, "方案复用") : null,
          h("span", { class: "muted small" }, timeAgo(m.ts))),
        formatReply(p ? p.reply : m.text),
        p ? consultCard(p, m) : null,
        isLastAgent && questions.length && !m.whatif ? h("div", null,
          h("div", { class: "starters-title", style: { margin: "2px 2px 8px" } }, "智能体需要您补充"),
          h("div", { class: "qs" }, questions.map((q) => h("button", { class: "q", type: "button", onclick: () => this.insert(`${q}\n答：`) },
            icon("question"), h("span", null, q), h("span", { class: "go" }, "回答"))))) : null,
        p ? h("div", { class: "turn-actions" },
          h("button", { class: "act", type: "button", onclick: () => openReport(m, c) }, icon("doc"), "完整报告"),
          !m.whatif && p.release_status !== "emergency_action_plan" ? h("button", { class: "act", type: "button", onclick: () => { this.whatif = true; render(); } }, icon("branch"), "假设推演") : null,
          h("button", { class: "act", type: "button", onclick: () => copyText(p.reply) }, icon("copy"), "复制"),
          h("button", { class: "act", type: "button", onclick: () => downloadJSON("nsclc-turn.json", p) }, icon("download"), "JSON")) : null));
  },

  insert(snippet) {
    this.text = this.text.trim() ? `${this.text.trim()}\n${snippet}` : snippet;
    render();
    requestAnimationFrame(() => { const ta = this.ta; if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } });
  },

  dossierPanel(c) {
    const inner = h("div", { class: "dossier-inner" });
    const m = lastResult(c);
    inner.appendChild(h("h3", null, icon("consult"), "病例档案", m ? h("span", { style: { marginLeft: "auto" } },
      statusPill(isAgent(m.payload) ? (m.payload.error ? "agent_error" : m.payload.emergency ? "agent_emergency" : "agent_done") : m.payload.release_status)) : null));
    if (!m) {
      inner.appendChild(h("div", { class: "dossier-empty" }, icon("form"), "开始会诊后，智能体会在这里整理分期、关键事实、当前方案与待办。"));
      return inner;
    }
    const p = m.payload;
    if (isAgent(p)) return this.agentDossier(c, m, inner);
    const onc = p.views && p.views.oncologist;
    const st = onc && onc.staging;
    const f = p.session_facts || {};
    if (st && st.stage_group) inner.appendChild(h("div", { class: "stage-tile" }, h("div", { class: "muted small" }, "分期 · 确定性引擎"), h("div", { class: "big" }, st.stage_group), h("div", { class: "tnm" }, `${st.tnm || ""} · ${st.edition || ""}`)));
    else if (f.tnm) inner.appendChild(h("div", { class: "stage-tile" }, h("div", { class: "muted small" }, onc ? "TNM · 未能分期" : "TNM（患者视角不显示分期细节）"), h("div", { class: "tnm" }, tnmText(f.tnm))));
    const rows = factRows(f);
    if (rows.length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "关键事实"), h("div", { class: "facts" }, rows.map(([k, v]) => [h("div", { class: "k" }, k), h("div", { class: "v" }, v)]))));
    const plan = onc && onc.treatment_plan;
    if (plan && (plan.options || []).length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, p.released ? "当前方案" : "候选方案（未放行）"),
      h("div", { class: "todo" }, plan.options.map((o, i) => h("div", null, h("b", { style: { color: "var(--accent)" } }, `${i + 1}.`), h("span", null, o.name))))));
    else if (!onc && p.views && p.views.patient && (p.views.patient.options || []).length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "方案"), fmtList(p.views.patient.options.map((o) => o.name))));
    const todo = [...((plan && plan.workup_needed) || []), ...((onc && onc.open_questions) || [])];
    if (todo.length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, `待办 · ${todo.length}`), h("div", { class: "todo" }, todo.slice(0, 8).map((t) => h("div", null, icon("alert"), h("span", null, t))))));
    if (onc) {
      const v = p.violations || [];
      inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "安全终审"),
        h("div", { class: "metrics" }, h("span", { class: `metric ${v.some((x) => x.severity === "block") ? "bad" : "ok"}` }, icon("lab"), v.length ? `${v.length} 项发现` : "无违规"),
          h("span", { class: "metric" }, `证据 ${(p.evidence || []).length}`), h("span", { class: "metric" }, `主张 ${(p.claims || []).length}`))));
    }
    inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "会诊"), h("div", { class: "facts" },
      h("div", { class: "k" }, "视角"), h("div", { class: "v" }, ROLE_LABEL[c.role] || c.role),
      h("div", { class: "k" }, "模型"), h("div", { class: "v" }, llmOn() ? `${Store.llm.llm.provider} · ${Store.llm.llm.model}` : "确定性模式"),
      h("div", { class: "k" }, "开始于"), h("div", { class: "v" }, new Date(c.created).toLocaleString()))));
    inner.appendChild(h("div", { class: "row" },
      h("button", { class: "btn sm", type: "button", onclick: () => openReport(m, c) }, icon("doc"), "完整报告"),
      h("button", { class: "btn sm", type: "button", onclick: () => openCaseForm(c) }, icon("pen"), "编辑事实")));
    return inner;
  },

  agentDossier(c, m, inner) {
    const p = m.payload;
    const k = p.consult || {};
    const engine = p.engine_stage || {};
    const f = p.facts || {};
    const stage = k.stage_group || engine.stage_group;
    if (stage || f.tnm) inner.appendChild(h("div", { class: "stage-tile" }, h("div", { class: "muted small" }, `分期 · 模型判断${engine.staged ? `（引擎：${engine.stage_group}）` : ""}`),
      h("div", { class: "big" }, stage || "—"), h("div", { class: "tnm" }, k.tnm || engine.tnm || tnmText(f.tnm))));
    if ((p.plan || []).length) inner.appendChild(h("div", { class: "dsec" }, planList(p.plan, true)));
    const rows = factRows(f);
    if (rows.length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "病例笔记（模型维护）"), h("div", { class: "facts" }, rows.map(([a, b]) => [h("div", { class: "k" }, a), h("div", { class: "v" }, b)]))));
    const options = (k.options || []).filter((o) => o && o.name);
    if (options.length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "模型推荐方案"),
      h("div", { class: "todo" }, options.map((o, i) => h("div", null, h("b", { style: { color: "var(--accent)" } }, o.preferred ? "★" : `${i + 1}.`), h("span", null, o.name))))));
    const todo = [...(k.workup || []), ...(k.questions || [])];
    if (todo.length) inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, `待办 · ${todo.length}`), h("div", { class: "todo" }, todo.slice(0, 8).map((t) => h("div", null, icon("alert"), h("span", null, t))))));
    const findings = (p.review && p.review.findings) || [];
    const answered = new Set(((p.review && p.review.responses) || []).map((r) => r && r.rule_id));
    inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "Hooks 复核（参考）"),
      h("div", { class: "metrics" }, h("span", { class: `metric ${findings.length ? "warn" : "ok"}` }, icon("lab"), findings.length ? `${findings.length} 条意见` : "无意见"),
        findings.length ? h("span", { class: "metric" }, `模型已回应 ${findings.filter((x) => answered.has(x.rule_id)).length}`) : null,
        h("span", { class: "metric" }, `${(p.steps || []).filter((s) => s.kind === "tool" && s.name !== "update_plan").length} 次工具调用`))));
    const ctx = p.context || {};
    const usage = p.usage || {};
    inner.appendChild(h("div", { class: "dsec" }, h("div", { class: "lbl" }, "会诊"), h("div", { class: "facts" },
      h("div", { class: "k" }, "模式"), h("div", { class: "v" }, "模型主导"),
      h("div", { class: "k" }, "视角"), h("div", { class: "v" }, ROLE_LABEL[c.role] || c.role),
      h("div", { class: "k" }, "模型"), h("div", { class: "v" }, p.model || "—"),
      usage.calls ? [h("div", { class: "k" }, "模型调用"), h("div", { class: "v" }, `${usage.calls} 次${usage.subagent_calls ? `（专科 ${usage.subagent_calls}）` : ""}`)] : null,
      ctx.window ? [h("div", { class: "k" }, "上下文"), h("div", { class: "v" }, contextMeter(ctx))] : null,
      h("div", { class: "k" }, "开始于"), h("div", { class: "v" }, new Date(c.created).toLocaleString()))));
    inner.appendChild(h("div", { class: "row" },
      h("button", { class: "btn sm", type: "button", onclick: () => openAgentReport(m) }, icon("doc"), "会诊详情"),
      h("button", { class: "btn sm", type: "button", onclick: () => openCaseForm(c) }, icon("pen"), "编辑事实")));
    return inner;
  },

  toggleDossier(c) {
    if (window.matchMedia("(max-width: 1200px)").matches) {
      openDrawer({ title: "病例档案", sub: c.title || "新会诊", width: 420, body: this.dossierPanel(c) });
      return;
    }
    this.dossier = !this.dossier; LS.set("nsclc.ui.dossier", this.dossier); render();
  },

  setRole(c, role) {
    c.role = role; if (role !== "oncologist") c.dose = false;
    LS.set("nsclc.pref.role", role);
    if (Session.boundId === c.id) Session.invalidate();
    if (Cases.get(c.id)) Cases.save();
    toast(`已切换为${ROLE_LABEL[role]}视角 · 下一轮生效（权限来自本次设置，不来自记录）`);
    render();
  },
  setDose(c, on) {
    c.dose = !!on;
    if (Session.boundId === c.id) Session.invalidate();
    if (Cases.get(c.id)) Cases.save();
    toast(on ? "已开启剂量草案：全部闸门通过时由确定性方案库产出，待签核" : "已关闭剂量草案");
    render();
  },

  exportCase(c) {
    downloadJSON(`nsclc-case-${(c.title || "untitled").replace(/[\s/·]+/g, "_")}.json`,
      { format: "nsclc-agent-case/1", title: c.title, created: c.created, mode: c.mode || "governed", messages: c.messages, session: c.session });
  },
  async importCase() {
    const [file] = await pickFile("application/json");
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      /* Authority (role, dose) is NOT taken from the file. */
      const base = { id: uid(), created: Date.now(), updated: Date.now(), role: Cases.current().role || DEFAULT_ROLE, dose: false, status: null, stage: null };
      let c;
      if (data.format === "nsclc-agent-case/1") {
        const mode = data.mode === "agent" || (data.session && /^nsclc-agent-session\//.test(data.session.format || "")) ? "agent" : "governed";
        c = Object.assign(base, { title: data.title || "导入的会诊", mode, messages: Array.isArray(data.messages) ? data.messages : [], session: data.session || null });
      } else if (/^nsclc-agent-session\//.test(data.format || "")) { /* a raw agent session (CLI `agent --session`) */
        const messages = (data.turns || []).flatMap((t) => [{ id: uid(), role: "user", ts: Date.now(), text: t.message || "" }, { id: uid(), role: "agent", ts: Date.now(), text: t.reply || "" }]);
        c = Object.assign(base, { title: "导入的会诊", mode: "agent", messages, session: data });
      } else if (Array.isArray(data.transcript) && Array.isArray(data.narrative)) { /* a raw session export (CLI / earlier builds) */
        const turns = data.transcript.filter((t) => t.kind !== "what_if");
        let k = 0;
        const messages = data.transcript.flatMap((t) => {
          const whatif = t.kind === "what_if";
          const text = whatif ? t.description || "（假设推演）" : turns.length === data.narrative.length ? data.narrative[k++] : "（历史轮次）";
          return [{ id: uid(), role: "user", ts: Date.now(), text, whatif }, { id: uid(), role: "agent", ts: Date.now(), whatif, text: t.reply || "" }];
        });
        c = Object.assign(base, { title: "导入的会诊", mode: "governed", messages, session: data });
      } else throw new Error("不是 NSCLC-Agent 会诊文件");
      const last = lastResult(c);
      if (last && isAgent(last.payload)) {
        c.status = "agent_done"; c.stage = (last.payload.consult || {}).stage_group || null;
      } else if (last) {
        c.status = last.payload.release_status;
        const onc = last.payload.views && last.payload.views.oncologist;
        c.stage = onc && onc.staging && onc.staging.stage_group;
      }
      Cases.list.unshift(c); Cases.save();
      location.hash = `#/case/${c.id}`;
      toast("已导入会诊 · 视角与剂量权限以当前设置为准，不来自文件");
    } catch (err) { toast(`导入失败：${err.message}`, true); }
  },
};

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => toast("已复制"), () => toast("复制失败", true));
  else toast("当前浏览器不支持复制", true);
}

/* ================================================================ tools */

const TOOLS = {
  agent: { title: "智能体", sub: "运行时 · 专科子智能体 · Hooks · 记忆 · MCP · 运行预算", icon: "agent" },
  staging: { title: "分期计算器", sub: "AJCC/UICC 第 9 版 · 唯一分期权威 · 歧义即拒绝", icon: "staging" },
  kg: { title: "指南知识库", sub: "六部指南 2,960 条推荐 · 机器抽取、默认未经临床复核", icon: "kg" },
  lab: { title: "安全实验室", sub: "把构造的方案直接交给规则引擎 — 与审计探针同一调用", icon: "lab" },
  eval: { title: "评测看板", sub: "金标准病例 · 临床错误分类学 · 该拦未拦率", icon: "eval" },
  settings: { title: "模型接入", sub: "Poe · MiniMax · Azure OpenAI · 离线 Mock — 密钥只在本页内存中", icon: "settings" },
  about: { title: "关于", sub: "IMPF-AI 研发", icon: "about" },
};
const VIEWS = {};

const StagingView = { t: "T2b", n: "N2b", m: "M0", prefix: "c", matrix: null };
VIEWS.staging = () => {
  const s = StagingView;
  const out = h("div");
  const compute = async () => {
    try {
      const r = await Bridge.call("stage", { t: s.t, n: s.n, m: s.m, prefix: s.prefix });
      clear(out).appendChild(r.refused
        ? h("div", { class: "stack" }, statusBanner("needs_staging_workup"), h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "引擎拒绝分期（不猜）")), h("div", null, r.reason)))
        : h("div", { class: "card" },
            h("div", { class: "row" }, h("div", { class: "stage-big" }, r.stage_group, h("small", null, r.tnm)), h("span", { style: { flex: 1 } }),
              h("span", { class: "chip regimen" }, r.module && r.module.module_key), h("span", { class: "chip" }, r.edition)),
            (r.migration_notes || []).length ? h("div", { style: { marginTop: "14px" } }, h("h4", null, "第 8 → 9 版迁移"), fmtList(r.migration_notes)) : null,
            (r.descriptor_notes || []).length ? h("div", { style: { marginTop: "10px" } }, h("h4", null, "描述符注记"), fmtList(r.descriptor_notes)) : null));
    } catch (err) { toast(err.message, true); }
  };
  const controls = h("div", { class: "card" },
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "T", selectEl(T_OPTIONS.slice(1), s.t, (v) => { s.t = v; compute(); })),
      h("label", { class: "field" }, "N", selectEl(N_OPTIONS.slice(1), s.n, (v) => { s.n = v; compute(); })),
      h("label", { class: "field" }, "M", selectEl(M_OPTIONS.slice(1), s.m, (v) => { s.m = v; compute(); })),
      h("label", { class: "field" }, "前缀", selectEl([["c", "c"], ["p", "p"], ["yp", "yp"]], s.prefix, (v) => { s.prefix = v; compute(); }))),
    h("div", { class: "muted small", style: { marginTop: "10px" } }, "试试 N2（未分 a/b）或 M1c（未分 c1/c2）：引擎会拒绝并说明需要哪项检查。"));
  const matrixHost = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "M0 分期矩阵（第 9 版）"), h("span", { class: "sub" }, "点击任意格子")), h("div", { class: "muted small" }, "计算中…"));
  const Ts = ["T1mi", "T1a", "T1b", "T1c", "T2a", "T2b", "T3", "T4"];
  const Ns = ["N0", "N1", "N2a", "N2b", "N3"];
  const drawMatrix = async () => {
    if (!s.matrix) {
      const m = {};
      for (const t of Ts) for (const n of Ns) { const r = await Bridge.call("stage", { t, n, m: "M0" }); m[`${t}|${n}`] = r.refused ? "—" : r.stage_group; }
      s.matrix = m;
    }
    const grid = h("div", { class: "tn-grid" }, h("div"), Ns.map((n) => h("div", { class: "h" }, n)));
    for (const t of Ts) {
      grid.appendChild(h("div", { class: "h" }, t));
      for (const n of Ns) {
        const g = s.matrix[`${t}|${n}`];
        const lvl = g.startsWith("IV") ? 4 : g.startsWith("III") ? 3 : g.startsWith("II") ? 2 : g.startsWith("I") ? 1 : 0;
        grid.appendChild(h("div", { class: `tn-cell s${lvl}${t === s.t && n === s.n && s.m === "M0" ? " sel" : ""}`, onclick: () => { s.t = t; s.n = n; s.m = "M0"; render(); } }, g));
      }
    }
    matrixHost.replaceChild(grid, matrixHost.lastChild);
  };
  setTimeout(() => { compute(); drawMatrix(); }, 0);
  return h("div", { class: "grid cols-2" }, h("div", { class: "stack" }, controls, out), matrixHost);
};

const KG = { query: "osimertinib", stage: "", gene: "", histology: "", jurisdiction: "", hits: null };
VIEWS.kg = () => {
  const root = h("div", { class: "stack" });
  const results = h("div", { class: "stack" });
  const infoHost = h("div");
  const drawResults = () => {
    clear(results);
    if (!KG.hits) return;
    if (!KG.hits.length) { results.appendChild(empty("无匹配推荐")); return; }
    for (const hit of KG.hits) {
      const grade = hit.grade || {};
      results.appendChild(h("div", { class: "card flat" },
        h("div", { class: "row" }, h("span", { class: "chip mono" }, hit.rec_id), h("strong", null, hit.guideline), h("span", { class: "chip" }, hit.region),
          hit.direction ? h("span", { class: `chip ${hit.direction === "recommend" ? "ok" : hit.direction.includes("against") ? "block" : ""}` }, hit.direction) : null,
          grade.strength ? h("span", { class: "chip trial" }, `${grade.scheme || ""} ${grade.strength_original || grade.strength}/${grade.evidence_original || grade.evidence}`) : null,
          h("span", { style: { flex: 1 } }),
          h("span", { class: `chip ${hit.curation_status === "clinician_verified" ? "ok" : "warn"}` }, hit.curation_status === "clinician_verified" ? "已临床复核" : "机器抽取 · 未复核")),
        hit.question ? h("div", { class: "muted small", style: { marginTop: "8px" } }, hit.question) : null,
        h("div", { style: { marginTop: "6px", fontWeight: 500 } }, hit.recommendation),
        h("div", { class: "row small muted", style: { marginTop: "8px" } }, hit.topic ? h("span", null, `主题：${hit.topic}`) : null, hit.line ? h("span", null, `线次：${hit.line}`) : null,
          hit.provenance && hit.provenance.page ? h("span", null, `原文第 ${hit.provenance.page} 页`) : null)));
    }
  };
  const search = async (btn) => {
    const done = btn ? busy(btn, "检索中…") : null;
    try {
      const r = await Bridge.call("kg_search", { query: KG.query, stage: KG.stage, gene: KG.gene, histology: KG.histology, jurisdiction: KG.jurisdiction, limit: 20 });
      KG.hits = r.hits; drawResults();
    } catch (err) { toast(err.message, true); } finally { if (done) done(); }
  };
  const btn = h("button", { class: "btn primary" }, icon("kg"), "检索");
  btn.addEventListener("click", () => search(btn));
  const q = inputEl("text", KG.query, (v) => (KG.query = v), "关键词：osimertinib / 围术期 / PD-L1 …");
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) search(btn); });
  root.appendChild(h("div", { class: "card" }, h("div", { class: "form-grid" },
    h("label", { class: "field span-2" }, "关键词", q),
    h("label", { class: "field" }, "分期", selectEl([["", "全部"], ...["IA1", "IA2", "IA3", "IB", "IIA", "IIB", "IIIA", "IIIB", "IIIC", "IVA", "IVB"].map((x) => [x, x])], KG.stage, (v) => (KG.stage = v))),
    h("label", { class: "field" }, "基因", selectEl([["", "全部"], ...["EGFR", "ALK", "ROS1", "RET", "MET", "BRAF", "NTRK", "HER2", "KRAS"].map((x) => [x, x])], KG.gene, (v) => (KG.gene = v))),
    h("label", { class: "field" }, "组织学", selectEl([["", "全部"], ["adenocarcinoma", "腺癌"], ["squamous", "鳞癌"]], KG.histology, (v) => (KG.histology = v))),
    h("label", { class: "field" }, "地区", selectEl([["", "全部"], ["US", "美国（NCCN）"], ["EU", "欧洲（ESMO）"], ["CN", "中国（CSCO 等）"]], KG.jurisdiction, (v) => (KG.jurisdiction = v))),
    h("div", { class: "span-2", style: { display: "flex", alignItems: "flex-end", justifyContent: "flex-end" } }, btn))));
  root.appendChild(h("div", { class: "callout warn" }, icon("alert"), h("div", null, "知识库条目为机器抽取、默认未经临床复核：在智能体中只作为权衡上下文（不可单独支撑放行），且已做剂量深度清洗。")));
  root.appendChild(infoHost);
  root.appendChild(results);
  Bridge.call("kg_info").then((info) => {
    append(infoHost, [h("div", { class: "grid cols-4" }, stat(info.recommendations, "推荐条目"), stat(Object.keys(info.guidelines || {}).length, "指南"), stat(info.clusters, "跨区域聚类"), stat((info.curation || {}).clinician_verified || 0, "已临床复核"))]);
  }).catch(() => {});
  if (KG.hits) drawResults(); else search();
  return root;
};
function stat(v, k) { return h("div", { class: "stat" }, h("div", { class: "v" }, v === undefined ? "—" : v), h("div", { class: "k" }, k)); }

const Lab = { probes: null, sel: null, staging: '{\n  "stage_group": "IVB"\n}', facts: '{\n  "driver_mutations": {"egfr": "L858R", "alk": "negative"},\n  "histologic_category": "adenocarcinoma"\n}', plan: '{\n  "regimen_ids": ["pembro_monotherapy"],\n  "options": [{"name": "Pembrolizumab monotherapy", "regimen_ids": ["pembro_monotherapy"]}]\n}', result: null };
VIEWS.lab = () => {
  const root = h("div", { class: "grid side" });
  const out = h("div", { class: "stack" });
  const editors = h("div", { class: "grid cols-3" });
  const ed = (label, key) => h("label", { class: "field" }, label, h("textarea", { class: "code", style: { minHeight: "220px" }, oninput: (e) => (Lab[key] = e.target.value) }, Lab[key]));
  const drawEditors = () => { clear(editors); append(editors, [ed("分期 staging", "staging"), ed("事实 facts", "facts"), ed("方案 plan（模型可能写出的方案）", "plan")]); };
  drawEditors();
  const runBtn = h("button", { class: "btn primary" }, icon("lab"), "交给规则引擎终审");
  const drawOut = () => {
    clear(out);
    const r = Lab.result; if (!r) return;
    const probe = Lab.sel;
    let verdict = null;
    if (probe && probe.expect) {
      const fired = new Set(r.violations.map((v) => `${v.rule_id}|${v.severity}`));
      const firedIds = new Set(r.violations.map((v) => v.rule_id));
      const required = (probe.expect.violations_required || []).every((w) => (w.severity ? fired.has(`${w.rule_id}|${w.severity}`) : firedIds.has(w.rule_id)));
      const forbidden = (probe.expect.violations_forbidden || []).every((id) => !firedIds.has(id));
      verdict = required && forbidden;
    }
    out.appendChild(statusBanner(r.blocked ? "blocked" : "treatment_recommendation", verdict === null ? null : verdict ? "✓ 与探针期望一致" : "✗ 与探针期望不符"));
    out.appendChild(h("div", { class: "card" }, renderAudit({ violations: r.violations, checks_run: ["safety_rule_engine"] })));
  };
  runBtn.addEventListener("click", async () => {
    let staging, facts, plan;
    try { staging = JSON.parse(Lab.staging); facts = JSON.parse(Lab.facts); plan = JSON.parse(Lab.plan); } catch (err) { toast(`JSON 无效：${err.message}`, true); return; }
    const done = busy(runBtn, "审计中…");
    try { Lab.result = await Bridge.call("audit_plan", { staging, facts, plan }); drawOut(); } catch (err) { toast(err.message, true); } finally { done(); }
  });
  const probeList = h("div", { class: "stack", style: { maxHeight: "560px", overflowY: "auto" } });
  const drawProbes = () => {
    clear(probeList);
    for (const p of Lab.probes || []) {
      probeList.appendChild(h("button", { class: "example", style: Lab.sel && Lab.sel.id === p.id ? { borderColor: "var(--accent)" } : null, onclick: () => {
        Lab.sel = p; Lab.staging = JSON.stringify(p.audit_staging || {}, null, 2); Lab.facts = JSON.stringify(p.audit_facts || {}, null, 2); Lab.plan = JSON.stringify(p.audit_plan || {}, null, 2);
        Lab.result = null; drawEditors(); drawOut(); drawProbes();
      } }, h("div", { class: "t mono", style: { fontSize: "12px" } }, p.id), h("div", { class: "s" }, p.comment)));
    }
  };
  Bridge.call("golden_cases").then((cases) => { Lab.probes = cases.filter((c) => c.kind === "audit"); drawProbes(); }).catch((e) => toast(e.message, true));
  root.appendChild(h("div", { class: "stack" },
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "构造方案"), h("span", { class: "sub" }, "rules.check_plan(staging, facts, plan)")),
      editors, h("div", { class: "row", style: { marginTop: "12px" } }, runBtn, h("span", { class: "muted small" }, "试试：把 EGFR 改成 negative，或在 options 里写一个没有 regimen_ids 的药名。"))),
    out));
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "安全网探针"), h("span", { class: "sub" }, "点击载入")),
    probeList, h("div", { class: "hr" }),
    h("div", { class: "muted small", style: { marginBottom: "6px" } }, "规则清单"),
    h("div", { class: "chips" }, ((Store.info && Store.info.rule_ids) || []).map((r) => h("span", { class: "chip mono", style: { fontSize: "11px" } }, r)))));
  if (Lab.result) drawOut();
  return root;
};

const Evalv = { report: null, ms: 0, filter: "all" };
const TAXO = { major_harmful: "方向性伤害", unsafe_release: "该拦未拦", overblocking: "过度拦截", false_alarm: "误报", omission: "遗漏", missing_workup: "漏补检", incorrect_release: "放行状态错误", staging_error: "分期错误", routing_error: "路由错误" };
VIEWS.eval = () => {
  const root = h("div", { class: "stack" });
  const host = h("div", { class: "stack" });
  const btn = h("button", { class: "btn primary" }, icon("play"), "在浏览器内运行全部金标准");
  const draw = () => {
    clear(host);
    const r = Evalv.report; if (!r) { host.appendChild(empty("点击上方按钮运行评测：每个病例都走完整的受治理流水线，安全网探针直接交给规则引擎。", "eval")); return; }
    const s = r.summary;
    host.appendChild(statusBanner(s.all_passed ? "treatment_recommendation" : "blocked", `${s.passed}/${s.total} 通过 · ${(Evalv.ms / 1000).toFixed(1)} s`));
    const unsafe = s.unsafe_release_rate || "";
    host.appendChild(h("div", { class: "grid cols-4" },
      h("div", { class: "stat" }, h("div", { class: "v" + (unsafe.startsWith("0/") ? " ok" : "") }, unsafe), h("div", { class: "k" }, "该拦未拦率（unsafe release）")),
      stat(s.staging_accuracy, "分期准确"), stat(s.regimen_accuracy, "方案正确"), stat(s.safety_clean_rate, "流水线安全净空")));
    const tax = s.error_taxonomy || {};
    const max = Math.max(1, ...Object.values(tax));
    host.appendChild(h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "临床错误分类学"), h("span", { class: "sub" }, "失败是被分类的临床事件")),
        Object.entries(tax).map(([k, v]) => h("div", { class: "bar-row" }, h("span", null, TAXO[k] || k), h("div", { class: "bar" + (v ? " bad" : "") }, h("span", { style: { width: `${v ? (100 * v) / max : 0}%` } })), h("span", { class: "mono" }, v)))),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "双医师裁定覆盖")),
        h("div", { class: "kv" }, h("div", { class: "k" }, "病例数"), h("div", null, (s.adjudication || {}).cases), h("div", { class: "k" }, "双人裁定"), h("div", null, (s.adjudication || {}).dual_adjudicated),
          h("div", { class: "k" }, "未裁定"), h("div", null, (s.adjudication || {}).unadjudicated)),
        h("div", { class: "muted small", style: { marginTop: "10px" } }, "裁定本身是人的工作；台账追加式、分歧并存、内容寻址作废。"))));
    const filterSeg = h("div", { class: "seg" }, [["all", "全部"], ["pipeline", "流水线"], ["audit", "探针"], ["failed", "失败"]].map(([k, l]) => h("button", { class: Evalv.filter === k ? "on" : "", onclick: () => { Evalv.filter = k; draw(); } }, l)));
    const rows = r.results.filter((x) => Evalv.filter === "all" || (Evalv.filter === "failed" ? !x.passed : x.kind === Evalv.filter));
    host.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "逐例结果"), h("div", { class: "right" }, filterSeg)),
      h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, ["病例", "类型", "结果", "分期 / 放行", "方案 / 触发规则"].map((x) => h("th", null, x)))),
        h("tbody", null, rows.map((x) => h("tr", null,
          h("td", { class: "mono" }, x.id), h("td", null, h("span", { class: `chip ${x.kind === "audit" ? "trial" : ""}` }, x.kind === "audit" ? "探针" : "流水线")),
          h("td", null, h("span", { class: `chip ${x.passed ? "ok" : "block"}` }, x.passed ? "通过" : "失败"), x.failures.length ? h("div", { class: "small", style: { marginTop: "4px" } }, x.failures.map((f) => `${TAXO[f.taxonomy] || f.taxonomy}: ${f.detail}`).join("；")) : null),
          h("td", null, x.kind === "audit" ? "—" : `${x.stage_group || "—"} · ${x.release_status}`),
          h("td", { class: "small" }, x.kind === "audit" ? (x.violations || []).map((v) => v.join(":")).join(", ") : (x.regimen_ids || []).join(", ")))))))));
  };
  btn.addEventListener("click", async () => {
    const done = busy(btn, "评测中…");
    try { const { result, ms } = await Bridge.timed("run_eval"); Evalv.report = result; Evalv.ms = ms; draw(); } catch (err) { toast(err.message, true); } finally { done(); }
  });
  root.appendChild(h("div", { class: "card" }, h("div", { class: "row" }, btn, h("span", { class: "muted small" }, "约 70 例，通常数秒完成；与命令行 `python -m nsclc_agent eval` 同一份代码、同一结果。"))));
  root.appendChild(host);
  draw();
  return root;
};

/* ======================================================= agent runtime */

/** Agent runtime settings (specialists, hooks, memory, MCP, budgets). Saved
 *  in this browser; MCP auth headers stay in page memory only. */
const AgentCfg = {
  cfg: LS.get("nsclc.agent.cfg", {}),
  catalog: null,
  persist() {
    const keep = Object.assign({}, this.cfg, { mcp_servers: (this.cfg.mcp_servers || []).map((x) => ({ name: x.name, url: x.url, enabled: x.enabled !== false })) });
    LS.set("nsclc.agent.cfg", keep);
  },
  async apply(patch) {
    if (patch) this.cfg = Object.assign({}, this.cfg, patch);
    const headers = Object.fromEntries((this.cfg.mcp_servers || []).map((x) => [x.url, x.headers]));
    this.cfg = await Bridge.call("agent_configure", { config: this.cfg });
    for (const x of this.cfg.mcp_servers || []) if (headers[x.url]) x.headers = headers[x.url];
    this.persist();
    this.catalog = await Bridge.call("agent_info");
    return this.cfg;
  },
  async remember(note) {
    const cur = (this.cfg.instructions || "").trimEnd();
    if (cur.includes(note)) return;
    await this.apply({ instructions: `${cur}${cur ? "\n" : ""}- ${note}` });
  },
};
const EVENT_LABEL = { user_prompt_submit: "消息提交时", post_tool_use: "工具调用后", stop: "提交结论时" };

VIEWS.agent = () => {
  const cfg = AgentCfg.cfg;
  const cat = AgentCfg.catalog || {};
  const root = h("div", { class: "stack" });
  const save = async (patch, msg) => {
    try { await AgentCfg.apply(patch); if (msg) toast(msg); render(); } catch (err) { toast(err.message, true); }
  };
  const metric = (text) => h("span", { class: "metric" }, text);
  const hooks = cat.hooks || [];
  const subOn = cfg.subagents !== false;

  root.appendChild(h("section", { class: "arch card" },
    h("div", { class: "arch-flow" }, ["主诊智能体", "会诊计划", "临床工具 ∥", "专科子智能体 ∥", "Hooks 复核", "结论"].map((t, i) => [i ? h("span", { class: "arrow" }, "→") : null, h("span", { class: "node" + (i === 0 ? " lead" : "") }, t)])),
    h("p", { class: "muted", style: { margin: "12px 0" } }, "主流智能体运行时架构：同一个 ReAct 循环驱动主诊与每位专科；同一步的只读工具并行执行（浏览器内顺序执行）；专科子智能体在独立上下文中用各自的工具集工作，只把结构化意见交回主诊；Hooks 在固定时点做确定性复核，全部是参考意见，由模型逐条采纳或说明理由；记忆写入每轮系统提示；上下文接近上限时由模型压缩；每一轮都有检查点，可回退。"),
    h("div", { class: "metrics" }, metric(`${(cat.tools || []).length} 个工具`), metric(`${subOn ? (cat.agents || []).length : 0} 位专科子智能体`),
      metric(`${hooks.filter((x) => x.enabled).length}/${hooks.length} 个 Hooks`), metric(`${(cfg.mcp_servers || []).length} 个 MCP 服务器`),
      metric(`记忆 ${(cfg.instructions || "").length} 字`), metric(`最多 ${cfg.max_steps || 24} 步`))));

  /* --- specialists */
  const disabled = new Set(cfg.disabled_agents || []);
  const custom = cfg.custom_agents || [];
  const toolChoices = (cat.tools || []).filter((t) => (t.category === "clinical" && t.name !== "record_case_facts") || t.category === "mcp").map((t) => t.name);
  const tile = (a, extra) => h("div", { class: "agent-tile" + (disabled.has(a.name) || !subOn ? " off" : "") },
    h("div", { class: "at-h" }, icon("users"), h("div", { class: "at-t" }, h("b", null, a.title), h("div", { class: "mono muted small" }, a.name)), extra),
    h("div", { class: "muted small" }, a.description),
    h("div", { class: "chips" }, (a.tools || []).map((t) => h("span", { class: "chip" }, toolLabel(t)))));
  const form = { name: "", title: "", description: "", prompt: "", tools: new Set() };
  const addForm = h("details", { class: "add-agent" }, h("summary", null, icon("plus"), "添加自定义专科（如老年肿瘤科、营养科、心理科）"),
    h("div", { class: "form-grid", style: { marginTop: "12px" } },
      h("label", { class: "field" }, "标识（英文）", inputEl("text", "", (v) => (form.name = v), "geriatric_oncology")),
      h("label", { class: "field" }, "名称", inputEl("text", "", (v) => (form.title = v), "老年肿瘤科医师")),
      h("label", { class: "field span-2" }, "职责（主诊据此决定是否邀请）", inputEl("text", "", (v) => (form.description = v), "老年综合评估、治疗强度与耐受性")),
      h("div", { class: "field span-4" }, "可用工具", h("div", { class: "tool-checks" }, toolChoices.map((t) => h("label", { class: "check small" },
        h("input", { type: "checkbox", onchange: (e) => (e.target.checked ? form.tools.add(t) : form.tools.delete(t)) }), toolLabel(t))))),
      h("label", { class: "field span-4" }, "专科提示词", h("textarea", { rows: 4, placeholder: "关注：……；不要给出……", oninput: (e) => (form.prompt = e.target.value) }))),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary sm", type: "button", onclick: () => {
      if (!form.name.trim() || !form.title.trim()) { toast("请填写标识与名称", true); return; }
      save({ custom_agents: [...custom.filter((a) => a.name !== form.name.trim()), { name: form.name.trim(), title: form.title.trim(), description: form.description.trim(), prompt: form.prompt, tools: [...form.tools] }] }, `已添加专科：${form.title}`);
    } }, icon("plus"), "添加")));
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "专科子智能体（MDT）"), h("span", { class: "sub hide-narrow" }, "主诊用 delegate 工具邀请；同一步可并行；每位专科有独立上下文与工具集，只读不写"),
      h("div", { class: "right" }, h("label", { class: "switch" }, h("input", { type: "checkbox", checked: subOn || null, onchange: (e) => save({ subagents: e.target.checked }, e.target.checked ? "已启用专科子智能体" : "已停用专科子智能体") }), "启用"))),
    h("div", { class: "agent-grid" },
      (cat.builtin_agents || []).map((a) => tile(a, h("label", { class: "switch", title: disabled.has(a.name) ? "已停用" : "已启用" }, h("input", { type: "checkbox", checked: !disabled.has(a.name) || null, disabled: !subOn || null,
        onchange: (e) => { const d = new Set(disabled); if (e.target.checked) d.delete(a.name); else d.add(a.name); save({ disabled_agents: [...d] }); } })))),
      custom.map((a, i) => tile(Object.assign({}, a, { title: `${a.title} · 自定义` }), h("button", { class: "icon-btn ghost", type: "button", title: "删除", "aria-label": "删除", onclick: () => save({ custom_agents: custom.filter((_, j) => j !== i) }, "已删除") }, icon("trash"))))),
    h("div", { class: "hr" }), addForm,
    h("div", { class: "muted small", style: { marginTop: "8px" } }, "命令行：把同样的定义写成 .nsclc-agent/agents/*.md（front matter：name / title / description / tools）。")));

  /* --- hooks */
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "Hooks"), h("span", { class: "sub" }, "确定性安全网 · 在固定时点运行 · 全部为参考意见，不阻断、不改写")),
    h("div", { class: "hook-list" }, hooks.map((x) => h("div", { class: "hook-row" },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: x.enabled || null, onchange: (e) => save({ hooks: Object.assign({}, cfg.hooks, { [x.name]: e.target.checked }) }) })),
      h("div", { style: { minWidth: 0 } }, h("div", { class: "row", style: { gap: "8px" } }, h("b", null, x.title), h("span", { class: "chip" }, EVENT_LABEL[x.event] || x.event), h("span", { class: "mono muted small" }, x.name)),
        h("div", { class: "muted small", style: { marginTop: "3px" } }, x.description)))))));

  /* --- memory */
  const memTa = h("textarea", { rows: 7, placeholder: "例如：\n- 本院优先参考 CSCO 指南的推荐排序\n- 奥希替尼不在本院目录，需替代时请说明\n- 回答先给结论，再给依据" });
  memTa.value = cfg.instructions || "";
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "记忆 · 机构规范与偏好"), h("span", { class: "sub" }, "写入每一轮的系统提示（等同命令行的 NSCLC.md）")),
    h("label", { class: "field" }, memTa),
    h("div", { class: "row", style: { marginTop: "10px" } }, h("button", { class: "btn primary sm", type: "button", onclick: () => save({ instructions: memTa.value }, "记忆已保存 · 下一轮生效") }, icon("check"), "保存记忆"),
      h("span", { class: "muted small" }, "智能体在会诊中用 remember 提议的条目会出现在结论卡片里，由您决定是否保存。仅保存在本浏览器。"))));

  /* --- MCP */
  const servers = cfg.mcp_servers || [];
  const draft = { name: "", url: "", headers: "" };
  const mcpOut = h("div", { class: "stack", style: { gap: "8px" } });
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "MCP 服务器"), h("span", { class: "sub" }, "Model Context Protocol · Streamable HTTP · 工具名 mcp__服务器__工具")),
    servers.length ? h("div", { class: "hook-list" }, servers.map((x, i) => h("div", { class: "hook-row" },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: x.enabled !== false || null, onchange: (e) => save({ mcp_servers: servers.map((y, j) => (j === i ? Object.assign({}, y, { enabled: e.target.checked }) : y)) }) })),
      h("div", { style: { minWidth: 0, flex: 1 } }, h("b", null, x.name), h("div", { class: "mono muted small", style: { wordBreak: "break-all" } }, x.url)),
      h("button", { class: "btn sm", type: "button", onclick: async (e) => {
        const done = busy(e.currentTarget, "连接中…");
        try { const r = await Bridge.call("mcp_check", { url: x.url, name: x.name, headers: x.headers || {} }); mcpOut.prepend(h("div", { class: `callout${r.ok ? "" : " warn"}` }, icon(r.ok ? "check" : "alert"), h("div", null, r.ok ? `${x.name}：已连接，${(r.tools || []).length} 个工具 — ${(r.tools || []).join("、")}` : `${x.name}：${r.error}`))); }
        catch (err) { toast(err.message, true); } finally { done(); }
      } }, "测试"),
      h("button", { class: "icon-btn ghost", type: "button", title: "删除", "aria-label": "删除", onclick: () => save({ mcp_servers: servers.filter((_, j) => j !== i) }, "已删除") }, icon("trash"))))) : null,
    h("div", { class: "form-grid", style: { marginTop: servers.length ? "14px" : 0 } },
      h("label", { class: "field" }, "名称", inputEl("text", "", (v) => (draft.name = v), "formulary")),
      h("label", { class: "field span-2" }, "URL", inputEl("text", "", (v) => (draft.url = v), "https://mcp.example.org/mcp")),
      h("label", { class: "field" }, "请求头 JSON（可选）", inputEl("text", "", (v) => (draft.headers = v), '{"Authorization": "Bearer …"}'))),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn sm", type: "button", onclick: () => {
      let headers = {};
      try { headers = draft.headers.trim() ? JSON.parse(draft.headers) : {}; } catch (_) { toast("请求头不是合法 JSON", true); return; }
      if (!/^https?:\/\//.test(draft.url.trim())) { toast("请填写 http(s) URL", true); return; }
      save({ mcp_servers: [...servers, { name: draft.name.trim() || `mcp${servers.length + 1}`, url: draft.url.trim(), headers, enabled: true }] }, "已添加 MCP 服务器 · 下一轮连接");
    } }, icon("plus"), "添加服务器"),
      h("span", { class: "muted small" }, "服务器需允许浏览器跨域（CORS）并暴露 Mcp-Session-Id；鉴权请求头只保存在本页内存中。")),
    mcpOut));

  /* --- budgets */
  const budget = { max_steps: cfg.max_steps || 24, max_review_rounds: cfg.max_review_rounds === undefined ? 1 : cfg.max_review_rounds, context_window: cfg.context_window || 128000 };
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "运行预算")),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "主诊最多步数", inputEl("number", budget.max_steps, (v) => (budget.max_steps = Number(v)))),
      h("label", { class: "field" }, "Hooks 复核轮数", selectEl([["0", "0（只记录不交回）"], ["1", "1"], ["2", "2"], ["3", "3"]], String(budget.max_review_rounds), (v) => (budget.max_review_rounds = Number(v)))),
      h("label", { class: "field span-2" }, "模型上下文窗口（tokens，用于自动压缩）", inputEl("number", budget.context_window, (v) => (budget.context_window = Number(v))))),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn sm", type: "button", onclick: () => save(budget, "已保存") }, icon("check"), "保存"))));

  /* --- commands */
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "命令"), h("span", { class: "sub" }, "在会诊输入框输入 / 唤出；命令行 REPL 同样可用")),
    h("div", { class: "cmd-table" }, (Store.commands || []).map((x) => h("div", { class: "cmd-row" },
      h("span", { class: "mono" }, `/${x.name}${x.args ? ` ${x.args}` : ""}`), h("span", { class: "muted" }, x.description), h("span", { class: `ck ${x.kind}` }, x.kind === "prompt" ? "交给智能体" : "本地"))))));
  return root;
};

const PROVIDERS = [
  { id: "none", name: "确定性模式", desc: "无需密钥 · 规则模式方案 · 完整安全治理" },
  { id: "poe", name: "Poe", desc: "一个密钥接入 Claude / Gemini / GPT；支持读片" },
  { id: "minimax", name: "MiniMax", desc: "MiniMax-M3 · 中国区 / 国际区" },
  { id: "azure", name: "Azure OpenAI", desc: "企业部署（需开启浏览器 CORS）" },
  { id: "mock", name: "离线 Mock", desc: "驱动完整工具循环与 MDT 面板的离线桩" },
];
const Settings = {
  cfg: { provider: "none", api_key: "", model: "", base_url: "", region: "china", group_id: "", endpoint: "", api_version: "", vision: true, vision_model: "", remember: false },
  async apply(silent) {
    const r = await Bridge.call("configure_llm", Object.fromEntries(Object.entries(this.cfg).filter(([k]) => k !== "remember")));
    Store.llm = r;
    Session.invalidate(); /* the worker dropped its session along with the old clients */
    /* Keys are kept only when asked; the key-less offline mock is always remembered for the tab. */
    try { if (this.cfg.remember || this.cfg.provider === "mock") sessionStorage.setItem("nsclc-llm", JSON.stringify(this.cfg)); else sessionStorage.removeItem("nsclc-llm"); } catch (_) { /* storage unavailable */ }
    renderRail();
    if (!silent) toast(r.llm.available ? `已接入 ${r.llm.provider} · ${r.llm.model} · 新会诊由模型主导` : "已切换为确定性模式");
    return r;
  },
  async restore() {
    try {
      const saved = JSON.parse(sessionStorage.getItem("nsclc-llm") || "null");
      if (saved && saved.provider && saved.provider !== "none") { Object.assign(this.cfg, saved); await this.apply(true); return; }
    } catch (_) { /* ignore */ }
    Store.llm = Store.info.llm;
  },
};
VIEWS.settings = () => {
  const c = Settings.cfg;
  const root = h("div", { class: "stack" });
  const form = h("div");
  const out = h("div", { class: "stack" });
  const cards = h("div", { class: "grid", style: { gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" } });
  const drawOut = () => {
    const l = Store.llm || {};
    out.appendChild(h("div", { class: "card flat" }, h("div", { class: "card-head" }, h("h3", null, "当前状态")),
      h("div", { class: "kv" }, h("div", { class: "k" }, "新会诊模式"), h("div", null,
          llmOn() ? h("div", { class: "seg" },
            h("button", { type: "button", class: defaultMode() === "agent" ? "on" : "", onclick: () => { LS.set("nsclc.pref.mode", "agent"); render(); } }, "模型主导"),
            h("button", { type: "button", class: defaultMode() !== "agent" ? "on" : "", onclick: () => { LS.set("nsclc.pref.mode", "governed"); render(); } }, "受治理"))
            : "确定性（未接入模型）",
          h("div", { class: "muted small", style: { marginTop: "6px" } }, llmOn()
            ? (defaultMode() === "agent" ? "模型自主规划、调用 20 个临床工具、邀请专科子智能体；Hooks 复核意见交回模型逐条判断，不作硬约束。专科、Hooks、记忆与 MCP 在「智能体」页配置。" : "模型只能提议，分期、规则与放行由确定性内核裁决。")
            : "接入模型后默认由模型主导会诊。")),
        h("div", { class: "k" }, "文本模型"), h("div", null, l.llm && l.llm.available ? `${l.llm.provider} · ${l.llm.model}` : "无"),
        h("div", { class: "k" }, "视觉模型"), h("div", null, l.vision && l.vision.provider !== "none" ? `${l.vision.provider} · ${l.vision.model}` : "无"))));
  };
  const drawForm = () => {
    clear(form);
    const grid = h("div", { class: "form-grid" });
    const key = h("label", { class: "field span-2" }, "API Key", h("input", { type: "password", value: c.api_key, autocomplete: "off", oninput: (e) => (c.api_key = e.target.value), placeholder: c.provider === "poe" ? "poe.com/api/keys" : "sk-…" }));
    if (c.provider === "poe") {
      append(grid, [key,
        h("label", { class: "field" }, "文本模型", h("span", { class: "hint" }, "Poe 目录 id（小写）"), inputEl("text", c.model, (v) => (c.model = v), "claude-sonnet-4.5")),
        h("label", { class: "field" }, "视觉模型（读片/报告）", inputEl("text", c.vision_model, (v) => (c.vision_model = v), "gemini-3.1-pro")),
        h("label", { class: "field span-4" }, "Base URL（可选）", inputEl("text", c.base_url, (v) => (c.base_url = v), "https://api.poe.com/v1"))]);
    } else if (c.provider === "minimax") {
      append(grid, [key,
        h("label", { class: "field" }, "区域", selectEl([["china", "中国区 api.minimaxi.com"], ["global", "国际区 api.minimax.io"]], c.region, (v) => (c.region = v))),
        h("label", { class: "field" }, "模型", inputEl("text", c.model, (v) => (c.model = v), "MiniMax-M3")),
        h("label", { class: "field span-2" }, "GroupId（可选）", inputEl("text", c.group_id, (v) => (c.group_id = v))),
        h("label", { class: "field span-2" }, "视觉模型（可选）", inputEl("text", c.vision_model, (v) => (c.vision_model = v)))]);
    } else if (c.provider === "azure") {
      append(grid, [key,
        h("label", { class: "field span-2" }, "Endpoint", inputEl("text", c.endpoint, (v) => (c.endpoint = v), "https://<resource>.openai.azure.com")),
        h("label", { class: "field" }, "Deployment", inputEl("text", c.model, (v) => (c.model = v))),
        h("label", { class: "field" }, "API Version", inputEl("text", c.api_version, (v) => (c.api_version = v), "2024-10-21"))]);
    } else {
      grid.appendChild(h("div", { class: "span-4 muted" }, c.provider === "mock" ? "离线模型桩：驱动完整的工具循环与 MDT 面板，不产生真实临床推理，用于演示治理机制。" : "无需任何配置。规则模式产出真实临床形状的方案，并经过与模型模式完全相同的安全治理。"));
    }
    if (["poe", "minimax", "azure"].includes(c.provider)) grid.appendChild(h("label", { class: "check span-4" }, h("input", { type: "checkbox", checked: c.remember || null, onchange: (e) => (c.remember = e.target.checked) }), "在本标签页记住（sessionStorage，关闭标签页即清除）"));
    form.appendChild(grid);
    const applyBtn = h("button", { class: "btn primary" }, icon("check"), "保存并应用");
    applyBtn.addEventListener("click", async () => { const done = busy(applyBtn, "应用中…"); try { await Settings.apply(); drawOut(); } catch (err) { toast(err.message, true); } finally { done(); } });
    const pingBtn = h("button", { class: "btn" }, icon("sparkle"), "测试连接");
    pingBtn.addEventListener("click", async () => {
      const done = busy(pingBtn, "请求中…");
      try { await Settings.apply(true); const r = await Bridge.call("ping_llm"); out.prepend(h("div", { class: `callout${r.ok ? "" : " warn"}` }, icon(r.ok ? "check" : "alert"), h("div", null, r.ok ? `连接成功：${r.model} 回复「${r.reply}」` : `连接失败：${r.error}`))); }
      catch (err) { toast(err.message, true); } finally { done(); }
    });
    const catBtn = c.provider === "poe" ? h("button", { class: "btn" }, "核对 Poe 模型目录") : null;
    if (catBtn) catBtn.addEventListener("click", async () => {
      const done = busy(catBtn, "核对中…");
      try {
        for (const m of [c.model || "claude-sonnet-4.5", c.vision_model || "gemini-3.1-pro"]) {
          const r = await Bridge.call("poe_catalog_check", { model: m });
          out.prepend(h("div", { class: `callout${r.exact ? "" : " warn"}` }, icon(r.exact ? "check" : "alert"), h("div", null,
            !r.catalog_reachable ? `目录不可达：${r.error || ""}` : r.exact ? `${m}：在 Poe 目录中 · 工具调用 ${r.tools ? "✓" : "✗"} · 图像输入 ${r.image_input ? "✓" : "✗"}` : `${m}：不在目录中${r.suggestion ? `，是否为 ${r.suggestion}？` : ""}`)));
        }
      } catch (err) { toast(err.message, true); } finally { done(); }
    });
    form.appendChild(h("div", { class: "row", style: { marginTop: "14px" } }, applyBtn, pingBtn, catBtn));
  };
  const drawCards = () => { clear(cards); for (const p of PROVIDERS) cards.appendChild(h("button", { class: `provider${c.provider === p.id ? " on" : ""}`, onclick: () => { c.provider = p.id; drawCards(); drawForm(); } }, h("div", { class: "t" }, p.name), h("div", { class: "s" }, p.desc))); };
  drawCards(); drawForm(); drawOut();
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "选择模型服务")), cards, h("div", { class: "hr" }), form));
  root.appendChild(h("div", { class: "callout" }, icon("key"), h("div", null,
    "密钥只保存在本页的 Web Worker 内存中，请求从您的浏览器直接发往所选服务商（Poe 与 MiniMax 均允许浏览器跨域调用，已实测）。IMPF-AI 与 GitHub Pages 不经手您的密钥或病例。模型只能提议：分期、适应证、器官闸门、证据蕴含与终审规则仍由确定性内核裁决，剂量永远只来自确定性方案库。")));
  root.appendChild(out);
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "本机数据")),
    h("div", { class: "row" }, h("span", { class: "muted small", style: { flex: 1 } }, `会诊记录只保存在本浏览器（localStorage），当前 ${Cases.list.length} 个会诊，不会上传到任何服务器。`),
      h("button", { class: "btn danger", onclick: () => {
        if (!confirm("清除本机全部会诊记录？此操作不可撤销。")) return;
        Cases.list = []; Cases.save(); Session.invalidate(); Cases.newDraft(); toast("已清除本机会诊记录"); location.hash = "#/";
      } }, icon("trash"), "清除全部会诊记录"))));
  return root;
};

VIEWS.about = () => {
  const i = Store.info || {};
  const b = Store.build || {};
  return h("div", { class: "stack" },
    h("section", { class: "hero" },
      h("span", { class: "hero-badge" }, h("span", { class: "impf-dot" }), "IMPF-AI 研发"),
      h("h2", null, "NSCLC-Agent"),
      h("p", null, "由 IMPF-AI 研发的非小细胞肺癌多学科会诊智能体：分期确定、证据治理、安全终审。网页端在浏览器内以 WebAssembly 运行与命令行完全相同的 Python 代码 —— 无服务器，病例数据不离开本机。")),
    h("div", { class: "grid cols-3" },
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "版本"),
        h("div", { class: "kv" }, h("div", { class: "k" }, "智能体"), h("div", null, `v${i.version || ""}`), h("div", { class: "k" }, "运行时"), h("div", null, i.runtime || ""),
          h("div", { class: "k" }, "构建"), h("div", { class: "mono" }, `${b.hash || ""} · ${b.built || ""}`),
          h("div", { class: "k" }, "规则 / 方案"), h("div", null, `${(i.counts || {}).rules || "—"} / ${(i.counts || {}).regimens || "—"}`))),
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "设计原则"),
        fmtList(["确定性分期是唯一分期权威", "模型只能提议，确定性内核裁决", "每个事实只有一种读法", "未放行的方案不展示给患者", "剂量只来自确定性方案库", "文件与日志不授予任何权限"])),
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "重要声明"),
        h("div", { class: "muted" }, "本系统仅供教学与研究，不是医疗器械，不构成医疗建议；内置试验注册表、方案库与指南知识库为教学规模语料，须经本机构医师与药师复核。任何治疗决定请与主治团队确认。"))),
    h("div", { class: "card" }, h("div", { class: "row" },
      h("div", null, h("div", { style: { fontWeight: 700 } }, "© 2026 IMPF-AI 研发"), h("div", { class: "muted small" }, "NSCLC-Agent · MIT License")),
      h("span", { style: { flex: 1 } }),
      h("a", { class: "btn", href: "https://github.com/psknlr/NSCLC-Agent", target: "_blank", rel: "noopener" }, "GitHub 仓库"))));
};

/* ================================================================ router */

function currentRoute() {
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  if (TOOLS[name]) return { tool: name };
  return { tool: null, caseId: name === "case" ? arg : null };
}
function renderRail() {
  const route = currentRoute();
  const c = Bridge.ready ? Cases.current() : null;
  const newBtn = clear($("#new-case"));
  append(newBtn, [icon("plus"), "新建会诊"]);
  const list = clear($("#case-list"));
  const q = ($("#case-search").value || "").trim().toLowerCase();
  const cases = Cases.list.filter((x) => !q || (x.title || "").toLowerCase().includes(q) || x.messages.some((m) => (m.text || "").toLowerCase().includes(q)));
  if (!cases.length) list.appendChild(h("div", { class: "case-empty" }, q ? "没有匹配的会诊" : "暂无会诊记录"));
  for (const x of cases) {
    const on = !route.tool && c && c.id === x.id;
    const open = () => { location.hash = `#/case/${x.id}`; };
    list.appendChild(h("div", { class: "case-item" + (on ? " on" : ""), role: "button", tabindex: "0", onclick: open, onkeydown: (e) => { if (e.key === "Enter") open(); } },
      h("span", { class: `dot ${x.status ? statusMeta(x.status)[0] : ""}` }),
      h("span", { style: { minWidth: 0 } }, h("div", { class: "t" }, x.title || "新会诊"), h("div", { class: "s" }, `${x.status ? statusMeta(x.status)[1] + " · " : ""}${timeAgo(x.updated)}`)),
      h("button", { class: "del", type: "button", title: "删除会诊", "aria-label": "删除会诊", onclick: (e) => {
        e.stopPropagation();
        if (!confirm(`删除会诊「${x.title || "新会诊"}」？`)) return;
        Cases.remove(x.id); toast("已删除");
        if (on) location.hash = "#/"; else render();
      } }, icon("trash"))));
  }
  const nav = clear($("#tool-nav"));
  for (const [id, t] of Object.entries(TOOLS)) nav.appendChild(h("a", { href: `#/${id}`, class: route.tool === id ? "on" : null }, icon(t.icon), t.title));
  const mb = clear($("#model-btn"));
  mb.className = "model-btn" + (llmOn() ? " on" : "");
  append(mb, [h("span", { class: "dot" }), h("span", { class: "lbl" }, h("span", { class: "muted" }, llmOn() ? (defaultMode() === "agent" ? "模型主导" : "模型 · 受治理") : "模型"),
    h("b", null, llmOn() ? `${Store.llm.llm.provider} · ${Store.llm.llm.model}` : "确定性模式（点此接入模型）"))]);
}
function render() {
  renderRail();
  const stage = clear($("#stage"));
  if (!Bridge.ready) return;
  const route = currentRoute();
  if (route.tool) {
    const t = TOOLS[route.tool];
    document.title = `${t.title} · NSCLC-Agent · IMPF-AI`;
    let body;
    try { body = VIEWS[route.tool](); } catch (err) { body = empty(`渲染失败：${err.message}`, "alert"); console.error(err); }
    stage.appendChild(h("div", { class: "page" },
      h("div", { class: "page-head" },
        h("button", { class: "icon-btn ghost only-mobile", type: "button", title: "菜单", "aria-label": "菜单", onclick: toggleRail }, icon("menu")),
        h("a", { class: "icon-btn ghost", href: "#/", title: "返回会诊", "aria-label": "返回会诊" }, icon("back")),
        h("div", { style: { minWidth: 0 } }, h("h1", null, t.title), h("div", { class: "crumb" }, t.sub))),
      h("div", { class: "page-body" }, body)));
    return;
  }
  const c = Cases.current();
  document.title = `${c.title || "新会诊"} · NSCLC-Agent · IMPF-AI`;
  stage.appendChild(Workspace.render());
}
function onRoute() {
  const route = currentRoute();
  if (!route.tool && Bridge.ready) {
    if (route.caseId) { if (route.caseId !== Cases.currentId) { Cases.select(route.caseId); Workspace.reset(); } }
    else if (Cases.currentId) { Cases.newDraft(); Workspace.reset(); }
  }
  $("#shell").classList.remove("rail-open");
  render();
}
function toggleRail() { $("#shell").classList.toggle("rail-open"); }

/* ================================================================ theme */

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("nsclc-theme"); } catch (_) { /* ignore */ }
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = $("#theme-btn");
  const isDark = () => document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const paint = () => { clear(btn).appendChild(icon(isDark() ? "sun" : "moon")); };
  btn.addEventListener("click", () => {
    document.documentElement.dataset.theme = isDark() ? "light" : "dark";
    try { localStorage.setItem("nsclc-theme", document.documentElement.dataset.theme); } catch (_) { /* ignore */ }
    paint();
  });
  paint();
}

/* ================================================================ start */

initTheme();
$("#new-case").addEventListener("click", () => {
  Workspace.reset();
  Cases.newDraft();
  if (currentRoute().tool || currentRoute().caseId) location.hash = "#/"; else onRoute();
});
$("#case-search").addEventListener("input", () => renderRail());
$("#model-btn").addEventListener("click", () => { location.hash = "#/settings"; });
$("#scrim").addEventListener("click", () => $("#shell").classList.remove("rail-open"));
window.addEventListener("hashchange", onRoute);
renderRail();
Bridge.start();
