/* NSCLC-Agent web app · IMPF-AI
 * Vanilla JS single-page app. The agent itself runs in worker.js (Pyodide);
 * this file only renders. Every value from the agent is inserted with
 * textContent — no HTML from data is ever parsed.
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
      else if (k === "html") el.innerHTML = v; /* static markup only (icons) */
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
const $ = (sel, root = document) => root.querySelector(sel);

const ICONS = {
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  consult: '<path d="M9 3h6l1 3H8z"/><rect x="5" y="5" width="14" height="16" rx="2"/><path d="M9 12h6M9 16h4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z"/>',
  staging: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  kg: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.2 7l7.4.3M7.2 8.2l3.8 7.6M16.9 9.2 13 15.8"/>',
  lab: '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9 12l2 2 4-4"/>',
  eval: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  about: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
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
  send: '<path d="M4 12l16-8-6 16-2-7z"/>',
  flask: '<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
};
function icon(name, cls) {
  return h("span", { class: cls || "", style: { display: "inline-flex" },
    html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>` });
}

function toast(text, bad) {
  const node = h("div", { class: "toast" + (bad ? " bad" : "") }, text);
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), bad ? 7000 : 3800);
}
function jsonBlock(value) { return h("pre", { class: "json" }, JSON.stringify(value, null, 2)); }
function downloadJSON(name, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click(); a.remove();
}
function busy(button, label) {
  const original = Array.from(button.childNodes);
  button.disabled = true;
  clear(button); append(button, [h("span", { class: "spinner" }), label || "运行中…"]);
  return () => { button.disabled = false; clear(button); append(button, original); };
}
function empty(text, iconName) {
  return h("div", { class: "empty" }, icon(iconName || "info"), h("div", null, text));
}
function fmtList(items) {
  if (!items || !items.length) return h("div", { class: "muted small" }, "无");
  return h("ul", { class: "list" }, items.map((x) => h("li", null, typeof x === "string" ? x : JSON.stringify(x))));
}

/* ================================================================ status */

const STATUS = {
  treatment_recommendation: ["ok", "已放行 · 循证治疗建议", "分期内、证据支撑、通过全部安全规则与终审", "check"],
  draft_for_tumor_board: ["accent", "剂量草案 · 待 MDT/医师签核", "唯一含剂量的状态：确定性剂量通道且全部闸门通过", "doc"],
  approved_by_tumor_board: ["ok", "已签核", "MDT/医师已签核", "check"],
  needs_more_information: ["warn", "需补充信息 · 方案为临时", "存在未完成的检查或问诊轴，补齐后再放行", "info"],
  needs_staging_workup: ["warn", "需完成分期检查", "TNM 不完整或存在歧义，分期引擎拒绝猜测", "info"],
  insufficient_evidence: ["neutral", "证据不足 · 未放行", "引用或主张未通过证据护栏", "info"],
  blocked: ["bad", "安全规则拦截 · 未放行", "终审发现阻断级违规，方案不得交付", "x"],
  failed_closed: ["bad", "故障关闭", "运行异常，按安全默认关闭", "x"],
  emergency_action_plan: ["emergency", "肿瘤急症 · 固定处置脚本", "急症短路：固定安全脚本，永不经模型改写", "siren"],
};
function statusPill(status) {
  const meta = STATUS[status] || ["neutral", status];
  const cls = { ok: "ok", accent: "accent", warn: "warn", neutral: "", bad: "bad", emergency: "bad" }[meta[0]];
  return h("span", { class: `pill ${cls}` }, h("span", { class: "dot" }), meta[1]);
}
function statusBanner(status, right) {
  const meta = STATUS[status] || ["neutral", status, "", "info"];
  return h("div", { class: `banner ${meta[0]}` },
    h("div", { class: "glyph" }, icon(meta[3])),
    h("div", null, h("div", { class: "t" }, meta[1]), h("div", { class: "d" }, meta[2])),
    right ? h("div", { class: "right" }, right) : null);
}

/* ================================================================ bridge */

const Bridge = {
  worker: null, seq: 0, pending: new Map(), ready: false,
  start() {
    this.worker = new Worker("worker.js");
    this.worker.onmessage = (e) => this.onMessage(e.data);
    this.worker.onerror = (e) => Boot.fatal(e.message || "worker error");
  },
  onMessage(msg) {
    if (msg.type === "progress") return Boot.step(msg.stage);
    if (msg.type === "ready") return Boot.done(msg.info, msg.build);
    if (msg.type === "fatal") return Boot.fatal(msg.error);
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
  /* result + elapsed */
  async timed(name, payload, uploads) {
    const t0 = performance.now();
    const result = await this.call(name, payload, uploads);
    return { result, ms: Math.round(performance.now() - t0) };
  },
};

const Store = { info: null, build: null, catalog: null, examples: [], llm: null };

const Boot = {
  order: ["runtime", "package", "init"],
  step(stage) {
    const idx = this.order.indexOf(stage);
    document.querySelectorAll("#boot-steps [data-step]").forEach((el) => {
      const i = this.order.indexOf(el.dataset.step);
      el.classList.toggle("done", i < idx);
      el.classList.toggle("on", i === idx);
      el.querySelector(".mark").textContent = i < idx ? "✓" : i === idx ? "●" : "○";
    });
  },
  async done(info, build) {
    Bridge.ready = true;
    Store.info = info; Store.build = build;
    this.step("zz");
    const pill = $("#runtime-pill");
    pill.className = "pill ok"; clear(pill);
    append(pill, [h("span", { class: "dot" }), "浏览器内运行 · 就绪"]);
    pill.title = `${info.runtime} · v${info.version}`;
    $("#side-version").textContent = `v${info.version} · 浏览器内运行`;
    $("#foot-version").textContent = `v${info.version}`;
    try {
      [Store.catalog, Store.examples] = await Promise.all([Bridge.call("catalog"), Bridge.call("examples")]);
      await Settings.restore();
    } catch (err) { toast(`初始化数据失败：${err.message}`, true); }
    updateModelPill(Store.info.llm);
    $("#boot").classList.add("hide");
    Router.render();
  },
  fatal(error) {
    $("#boot-note").textContent = `运行时加载失败：${error}。请检查网络（需访问 cdn.jsdelivr.net）后刷新。`;
    $("#boot-note").style.color = "var(--bad)";
    const pill = $("#runtime-pill"); pill.className = "pill bad"; clear(pill);
    append(pill, [h("span", { class: "dot" }), "运行时加载失败"]);
  },
};

function updateModelPill(llm) {
  const pill = $("#model-pill"); clear(pill);
  const on = llm && llm.llm && llm.llm.available;
  pill.className = "pill " + (on ? "indigo" : "");
  append(pill, [h("span", { class: "dot" }), on ? `模型：${llm.llm.provider} · ${llm.llm.model}` : "确定性模式"]);
}

/* ================================================================ router */

const ROUTES = [
  { id: "home", label: "概览", icon: "home", section: "工作台", title: "概览", sub: "证据治理 · 分期确定 · 安全终审" },
  { id: "consult", label: "会诊工作台", icon: "consult", section: "工作台", title: "会诊工作台", sub: "单次完整受治理运行：分期 → 路由 → 方案 → 终审 → 放行" },
  { id: "chat", label: "多轮会诊", icon: "chat", section: "工作台", title: "多轮会诊", sub: "累积事实、方案指纹复用、假设推演（what-if）" },
  { id: "staging", label: "分期引擎", icon: "staging", section: "临床引擎", title: "TNM 分期引擎", sub: "AJCC/UICC 第 9 版 · 唯一分期权威 · 歧义即拒绝" },
  { id: "kg", label: "指南知识图谱", icon: "kg", section: "临床引擎", title: "指南知识图谱", sub: "六部指南 2,960 条推荐 · 机器抽取、默认未经临床复核" },
  { id: "lab", label: "安全网实验室", icon: "lab", section: "安全与评测", title: "安全网实验室", sub: "把构造的方案直接交给规则引擎 — 审计探针同款调用" },
  { id: "eval", label: "评测看板", icon: "eval", section: "安全与评测", title: "金标准评测看板", sub: "临床错误分类学 · 该拦未拦率（unsafe release rate）" },
  { id: "settings", label: "模型接入", icon: "settings", section: "系统", title: "模型接入", sub: "Poe · MiniMax · Azure OpenAI · 离线 Mock — 密钥只在本页内存中" },
  { id: "about", label: "关于", icon: "about", section: "系统", title: "关于", sub: "IMPF-AI 研发" },
];

const Router = {
  current() {
    const id = (location.hash || "#/home").replace(/^#\/?/, "").split("?")[0];
    return ROUTES.find((r) => r.id === id) || ROUTES[0];
  },
  go(id) { location.hash = `#/${id}`; },
  buildNav() {
    const nav = $("#nav"); const mobile = $("#mobile-nav");
    let section = "";
    for (const r of ROUTES) {
      if (r.section !== section) { section = r.section; nav.appendChild(h("div", { class: "nav-label" }, section)); }
      nav.appendChild(h("a", { href: `#/${r.id}`, "data-route": r.id }, icon(r.icon), r.label));
      mobile.appendChild(h("a", { href: `#/${r.id}`, "data-route": r.id }, icon(r.icon), r.label));
    }
  },
  render() {
    const route = this.current();
    document.querySelectorAll("[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === route.id));
    $("#page-title").textContent = route.title;
    $("#page-sub").textContent = route.sub;
    document.title = `${route.title} · NSCLC-Agent · IMPF-AI`;
    const view = clear($("#view"));
    if (!Bridge.ready) { view.appendChild(empty("运行时加载中…", "info")); return; }
    const renderer = VIEWS[route.id];
    try { append(view, [renderer()]); } catch (err) { view.appendChild(empty(`渲染失败：${err.message}`, "alert")); console.error(err); }
    window.scrollTo({ top: 0 });
  },
};

/* ================================================================ result */

function regimenChip(rid) {
  const r = Store.catalog && Store.catalog.regimens[rid];
  return h("span", { class: "chip regimen", title: r ? `${r.name}\n${r.label_note || ""}` : rid }, r ? r.name : rid);
}
function trialChip(tid) {
  const t = Store.catalog && Store.catalog.trials[tid];
  return h("span", { class: "chip trial", title: t ? `${t.name}\n${(t.results || []).join("\n")}\n${t.source || ""}` : tid }, tid);
}

function renderResult(res, ms) {
  const onc = (res.views && res.views.oncologist) || null;
  const patient = res.views && res.views.patient;
  const wrap = h("div", { class: "stack" });
  wrap.appendChild(statusBanner(res.release_status, ms ? `${ms} ms · 浏览器内` : null));

  const emergency = (onc && onc.emergency_plan) || (patient && patient.emergency_plan);
  if (res.release_status === "emergency_action_plan" && emergency) {
    wrap.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, icon("siren"), h("h3", null, "急症处置（固定安全脚本）")),
      h("div", { class: "grid cols-3" },
        h("div", null, h("div", { class: "muted small" }, "立即处置"), fmtList(emergency.immediate_actions)),
        h("div", null, h("div", { class: "muted small" }, "不要做"), fmtList(emergency.do_not)),
        h("div", null, h("div", { class: "muted small" }, "升级条件"), fmtList(emergency.escalate_if)))));
    return wrap;
  }

  if (!onc) {
    wrap.appendChild(renderPatientView(patient));
    wrap.appendChild(h("div", { class: "callout" }, icon("info"), h("div", null,
      "患者角色的运行只返回患者视图（与命令行同一契约）。切换为「肿瘤科医师」角色可查看分期、证据台账与安全审计。")));
    return wrap;
  }

  const plan = onc.treatment_plan || {};
  const staging = onc.staging || {};
  const top = h("div", { class: "grid cols-2" });
  top.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "分期（确定性引擎）"), h("span", { class: "sub" }, staging.edition || "")),
    staging.stage_group
      ? h("div", null,
          h("div", { class: "stage-big" }, staging.stage_group, h("small", null, staging.tnm || "")),
          h("div", { class: "kv", style: { marginTop: "14px" } },
            h("div", { class: "k" }, "路由模块"), h("div", null, (onc.routing || {}).module_key || "—"),
            h("div", { class: "k" }, "风险模式"), h("div", null, res.risk_mode || "routine")),
          (staging.migration_notes || []).length ? h("div", { style: { marginTop: "10px" } },
            h("div", { class: "muted small" }, "版本迁移注记"), fmtList(staging.migration_notes)) : null)
      : h("div", { class: "muted" }, "未分期 — TNM 不完整或被引擎拒绝（歧义不猜）")));
  top.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "方案概要"),
      h("div", { class: "right" }, plan.intent ? h("span", { class: "chip" }, plan.intent) : null,
        plan.mdt_referral ? h("span", { class: "chip warn" }, "MDT 转诊") : null)),
    h("div", null, plan.summary || "—"),
    (plan.trial_refs || []).length ? h("div", { style: { marginTop: "12px" } },
      h("div", { class: "muted small", style: { marginBottom: "6px" } }, "试验锚点"),
      h("div", { class: "chips" }, plan.trial_refs.map(trialChip))) : null));
  wrap.appendChild(top);

  const options = plan.options || [];
  wrap.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "治疗选项"), h("span", { class: "sub" }, `${options.length} 项`),
      res.released ? null : h("span", { class: "right" }, h("span", { class: "chip warn" }, "未放行 · 仅供审阅"))),
    options.length ? options.map((o, i) => h("div", { class: "option" },
      h("div", { class: "name" }, h("span", { class: "idx" }, i + 1), o.name),
      o.rationale ? h("div", { class: "why" }, o.rationale) : null,
      (o.regimen_ids || []).length ? h("div", { class: "chips" }, o.regimen_ids.map(regimenChip)) : null))
      : h("div", { class: "muted" }, "无选项")));

  const violations = res.violations || [];
  const tabs = [
    { id: "detail", label: "方案细节", render: () => renderPlanDetail(plan, onc) },
    { id: "audit", label: "安全审计", count: violations.length, render: () => renderAudit(res) },
    { id: "evidence", label: "证据与主张", count: (res.claims || []).length, render: () => renderEvidence(res) },
    { id: "indication", label: "适应证判定", render: () => renderIndications(res.indication_report) },
    { id: "prognosis", label: "预后（人群）", render: () => renderPrognosis(onc.prognosis) },
    { id: "kg", label: "指南 KG 上下文", render: () => renderGuidelineContext(onc.guideline_context) },
    { id: "patient", label: "患者视图", render: () => renderPatientView(patient) },
    { id: "dose", label: "剂量通道", render: () => renderDose(onc.dose_plan) },
    { id: "trace", label: "执行轨迹", render: () => renderTrace(res, onc) },
  ];
  wrap.appendChild(h("div", { class: "card" }, tabbed(tabs)));
  return wrap;
}

function tabbed(defs, initial) {
  const bar = h("div", { class: "tabs" });
  const body = h("div");
  let active = initial || defs[0].id;
  const draw = () => {
    clear(bar); clear(body);
    for (const d of defs) {
      bar.appendChild(h("button", { class: d.id === active ? "on" : "", onclick: () => { active = d.id; draw(); } },
        d.label, d.count !== undefined ? h("span", { class: "count" }, d.count) : null));
    }
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
      (plan.organ_gates.failed || []).length ? h("div", { class: "chips", style: { marginBottom: "8px" } },
        plan.organ_gates.failed.map((f) => h("span", { class: "chip block" }, `${f.gate}: ${f.note}`))) : h("div", { class: "muted small" }, "无不合格闸门"),
      h("div", { class: "muted small", style: { marginTop: "6px" } }, "剂量通道开启前待补："), fmtList(plan.organ_gates.pending)));
  }
  if (plan.cns) {
    const rd = plan.cns.reading || {};
    const tri = (v) => (v === true ? "是" : v === false ? "否" : "未记录");
    blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "CNS 分层读数"),
      h("div", { class: "kv" },
        h("div", { class: "k" }, "状态"), h("div", null, rd.status || "未记录"),
        h("div", { class: "k" }, "有症状"), h("div", null, tri(rd.symptomatic)),
        h("div", { class: "k" }, "已局部治疗"), h("div", null, tri(rd.treated)),
        h("div", { class: "k" }, "负荷"), h("div", null, rd.burden || "未记录"),
        h("div", { class: "k" }, "软脑膜"), h("div", null, tri(rd.leptomeningeal)),
        h("div", { class: "k" }, "来源"), h("div", null, rd.source || "—")),
      (plan.cns.honest_notes || []).length ? h("div", { class: "muted small", style: { marginTop: "8px" } }, plan.cns.honest_notes.join(" ")) : null));
  }
  if (plan.sequencing) {
    blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "后线序贯"),
      h("div", { class: "kv" }, h("div", { class: "k" }, "当前线次"), h("div", null, h("strong", null, `第 ${plan.sequencing.line || "?"} 线`))),
      (plan.sequencing.honest_notes || []).length ? h("div", { style: { marginTop: "8px" } },
        h("div", { class: "muted small" }, "覆盖边界（如实声明）"), fmtList(plan.sequencing.honest_notes)) : null));
  }
  if ((onc.open_questions || []).length) blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "待回答问题"), fmtList(onc.open_questions)));
  const workup = onc.workup_plan && onc.workup_plan.steps;
  if (workup && workup.length) blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "检查计划"), fmtList(workup.map((s) => `${s.gap || ""} → ${s.test || ""}`))));
  if ((onc.flags || []).length) blocks.appendChild(h("div", null, h("h4", { style: { marginBottom: "6px" } }, "运行标记"), fmtList(onc.flags)));
  return blocks;
}

function renderAudit(res) {
  const v = res.violations || [];
  const issues = (res.issues || []).filter((i) => !v.some((x) => i.startsWith(x.rule_id)));
  return h("div", { class: "stack" },
    h("div", { class: "row" }, h("span", { class: "muted small" }, "已运行检查："),
      h("div", { class: "chips" }, (res.checks_run || []).map((c) => h("span", { class: "chip ok" }, c)))),
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
      h("tbody", null, claims.map((c) => h("tr", null,
        h("td", { class: "mono" }, c.claim_id), h("td", null, c.kind), h("td", null, c.text),
        h("td", null, h("span", { class: "chip" }, c.support_relation)),
        h("td", { class: "mono" }, (c.evidence_ids || []).join(", ") || "—")))))) : h("div", { class: "muted" }, "无主张"),
    h("h4", { style: { marginTop: "8px" } }, "证据台账（工具声明的证据等级）"),
    h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["ID", "等级", "来源", "摘要", "可放行"].map((x) => h("th", null, x)))),
      h("tbody", null, evidence.map((e) => h("tr", null,
        h("td", { class: "mono" }, e.evidence_id), h("td", null, h("span", { class: "chip" }, e.level)),
        h("td", null, e.source), h("td", null, e.summary),
        h("td", null, h("span", { class: `chip ${e.releasable ? "ok" : "warn"}` }, e.releasable ? "是" : "否"))))))));
}

function renderIndications(report) {
  if (!report || !report.regimens) return empty("本次方案无方案级适应证判定");
  return h("div", { class: "stack" }, report.regimens.map((r) => h("div", { class: "option" },
    h("div", { class: "name" }, regimenChip(r.regimen_id),
      h("span", { class: `chip ${r.verdict === "eligible" ? "ok" : r.verdict === "ineligible" ? "block" : "warn"}` }, r.verdict)),
    (r.failed_conditions || []).length ? h("div", { class: "why" }, "不满足：", r.failed_conditions.join("；")) : null,
    (r.unknown_conditions || []).length ? h("div", { class: "why" }, "待补：", r.unknown_conditions.join("；")) : null)),
    report.note ? h("div", { class: "muted small" }, report.note) : null);
}

function renderPrognosis(p) {
  if (!p) return empty("无预后上下文（仅临床视图、仅有分期时提供）");
  return h("div", { class: "stack" },
    h("div", { class: "grid cols-3" },
      h("div", { class: "stat" }, h("div", { class: "v" }, p.five_year_os_percent_approx !== null && p.five_year_os_percent_approx !== undefined ? `~${p.five_year_os_percent_approx}%` : "—"), h("div", { class: "k" }, `${p.stage_group} 期 5 年总生存（人群队列）`)),
      h("div", { class: "stat" }, h("div", { class: "v" }, (p.modifiers || []).length), h("div", { class: "k" }, "方向性预后因素")),
      h("div", { class: "stat" }, h("div", { class: "v", style: { fontSize: "15px" } }, p.classification_basis || "—"), h("div", { class: "k" }, "分类依据"))),
    h("div", { class: "callout warn" }, icon("alert"), h("div", null, "人群统计，不是个体预测；系统不为预后因素配数字权重。", p.cohort ? ` 来源：${p.cohort}` : "")),
    (p.modifiers || []).length ? jsonBlock(p.modifiers) : null);
}

function renderGuidelineContext(ctx) {
  if (!ctx) return empty("无相关指南 KG 条目");
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
    h("div", { class: "muted small" }, "剂量只在：肿瘤科医师角色 + 显式开启剂量规划 + 方案无补检/急症信号 + 全部闸门通过 时由确定性剂量库产出，状态为「剂量草案 · 待 MDT/医师签核」。"));
  return h("div", { class: "stack" },
    h("div", { class: "callout warn" }, icon("alert"), h("div", null, "剂量草案 — 必须经 MDT/医师签核；数值来自确定性方案库，模型不得产出剂量。")),
    jsonBlock(dose));
}

function renderTrace(res, onc) {
  return h("div", { class: "grid cols-2" },
    h("div", null, h("h4", { style: { marginBottom: "8px" } }, "任务图"),
      h("div", { class: "chips" }, (res.tasks || []).map((t) => h("span", { class: `chip ${t.status === "ok" ? "ok" : t.status.startsWith("skipped") ? "" : "warn"}` }, `${t.agent} · ${t.status}`)))),
    h("div", null, h("h4", { style: { marginBottom: "8px" } }, "运行元数据"), jsonBlock(res.run_meta || {})),
    h("div", { style: { gridColumn: "1 / -1" } }, h("div", { class: "row" },
      h("button", { class: "btn sm", onclick: async () => downloadJSON("nsclc-run.json", await Bridge.call("export_last")) }, icon("download"), "导出完整运行记录 JSON"),
      h("span", { class: "muted small" }, "证据载荷、执行轨迹、预算 — 与 CLI 的审计输出同一对象"))),
    (onc.warnings || []).length ? h("div", null, h("h4", null, "告警"), fmtList(onc.warnings)) : null);
}

/* ================================================================ views */

const VIEWS = {};

VIEWS.home = () => {
  const i = Store.info || { counts: {} };
  const c = i.counts || {};
  const root = h("div", { class: "stack", style: { gap: "22px" } });
  root.appendChild(h("section", { class: "hero" },
    h("span", { class: "hero-badge" }, h("span", { class: "impf-dot" }), "IMPF-AI 研发 · v", i.version),
    h("h2", null, "非小细胞肺癌 ", h("span", { class: "grad" }, "多学科决策智能体")),
    h("p", null, "确定性 AJCC/UICC 第 9 版分期是唯一分期权威；每个方案都要过适应证谓词、器官功能闸门、逐主张证据蕴含与终审规则引擎，未通过就不放行。整个智能体以 WebAssembly 在您的浏览器内运行 — 无服务器，病例数据不离开本机。"),
    h("div", { class: "hero-actions" },
      h("button", { class: "btn primary", onclick: () => Router.go("consult") }, icon("play"), "开始会诊"),
      h("button", { class: "btn ghost", onclick: () => Router.go("chat") }, icon("chat"), "多轮会诊"),
      h("button", { class: "btn ghost", onclick: () => Router.go("eval") }, icon("eval"), "查看安全评测"))));

  root.appendChild(h("div", { class: "grid cols-4" },
    stat(c.rules, "确定性安全规则"), stat(c.regimens, "方案库（摘要无剂量）"),
    stat(c.trials, "试验注册表条目"), stat(`${c.golden_cases}`, `金标准病例（${c.audit_probes} 个安全网探针）`)));

  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "受治理的执行流水线"), h("span", { class: "sub" }, "模型可以提议，确定性内核决定")),
    h("div", { class: "pipeline" },
      pipe("01", "问诊与急症筛查", "子句级否定；急症直接短路到固定脚本"),
      pipe("02", "确定性分期", "第 9 版表 + 拒绝表；歧义不猜", true),
      pipe("03", "路由与方案", "驱动本体、CNS 分层、后线序贯"),
      pipe("04", "适应证与器官闸门", "40 条机器可执行人群声明", true),
      pipe("05", "逐主张证据蕴含", "方案、人群、数字三层核对"),
      pipe("06", "终审规则引擎", "20 条规则；阻断即不放行", true),
      pipe("07", "放行状态机", "患者只见已放行内容"))));

  const ex = h("div", { class: "grid cols-4" });
  for (const e of Store.examples || []) {
    ex.appendChild(h("button", { class: "example", onclick: () => { Consult.load(e); Router.go("consult"); Consult.autorun = true; } },
      h("div", { class: "t" }, e.title), h("div", { class: "s" }, e.subtitle)));
  }
  root.appendChild(h("div", null,
    h("div", { class: "section-title" }, "一键示例"),
    h("div", { class: "section-sub" }, "每个示例覆盖一项独立的安全性质 — 点击后在会诊工作台完整运行。"), ex));

  root.appendChild(h("div", { class: "grid cols-3" },
    feature("staging", "分期是唯一权威", "模型可以查询分期，但无权改写本次运行的分期；N2 未分 a/b 等歧义直接拒绝并告诉您用哪项检查解决。"),
    feature("lab", "规则引擎独立终审", "规划器和终审器各自执行同一份人群声明：变异类错配、驱动一线、N3 手术、CNS 未处理等都会被独立拦截。"),
    feature("key", "密钥只在您的浏览器", "接入 Poe / MiniMax 时，请求从本页直接发往您选择的服务商；IMPF-AI 与 GitHub 都不会经手您的密钥或病例。")));
  return root;
};
function stat(v, k) { return h("div", { class: "stat" }, h("div", { class: "v" }, v === undefined ? "—" : v), h("div", { class: "k" }, k)); }
function pipe(n, t, d, gate) { return h("div", { class: "pipe-step" + (gate ? " gate" : "") }, h("div", { class: "n" }, n), h("div", { class: "t" }, t), h("div", { class: "d" }, d)); }
function feature(ic, t, d) { return h("div", { class: "card" }, h("div", { class: "row", style: { marginBottom: "8px" } }, h("span", { class: "feature-ic" }, icon(ic)), h("h3", { style: { fontSize: "15px" } }, t)), h("div", { class: "muted" }, d)); }

/* ---------------------------------------------------------------- consult */

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
function deletePath(obj, path) {
  const keys = path.split("."); let o = obj;
  for (const k of keys.slice(0, -1)) { if (!o || typeof o !== "object") return; o = o[k]; }
  if (o && typeof o === "object") delete o[keys[keys.length - 1]];
}
function pruneEmpty(obj) {
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === "object" && !Array.isArray(v)) { pruneEmpty(v); if (!Object.keys(v).length) delete obj[k]; }
  }
  return obj;
}

const Consult = {
  state: { t: "", n: "", m: "", prefix: "c", presentation: "", question: "", role: "oncologist", dose: false, panel: false, fields: {}, history: [], extra: "" },
  result: null, ms: 0, autorun: false,
  load(example) {
    const c = example.case || {};
    const facts = JSON.parse(JSON.stringify(c.facts || {}));
    const fields = {};
    for (const f of FACT_FIELDS) {
      const v = getPath(facts, f.key);
      if (v !== undefined) {
        fields[f.key] = f.type === "list" && Array.isArray(v) ? v.join(", ") : String(v);
        deletePath(facts, f.key);
      }
    }
    const history = (facts.treatment_history || []).map((e) => ({ line: e.line || "", agents: (e.agents || []).join(", "), status: e.status || "" }));
    delete facts.treatment_history;
    pruneEmpty(facts);
    Object.assign(this.state, { t: c.t || "", n: c.n || "", m: c.m || "", prefix: c.prefix || "c", presentation: c.presentation || "", question: c.question || "", fields, history, extra: Object.keys(facts).length ? JSON.stringify(facts, null, 2) : "" });
    this.result = null;
  },
  facts() {
    let facts = {};
    for (const f of FACT_FIELDS) {
      const raw = this.state.fields[f.key];
      if (raw === undefined || raw === "") continue;
      let value = raw;
      if (f.type === "int") value = parseInt(raw, 10);
      else if (f.type === "number") { value = Number(raw); if (Number.isNaN(value)) continue; }
      else if (f.type === "bool" || f.type === "tri") value = raw === "true";
      else if (f.type === "list") value = raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      setPath(facts, f.key, value);
    }
    const history = this.state.history.filter((e) => e.agents.trim()).map((e) => ({
      line: e.line ? parseInt(e.line, 10) : undefined,
      agents: e.agents.split(/[,，+]/).map((s) => s.trim()).filter(Boolean),
      status: e.status || undefined,
    }));
    if (history.length) facts.treatment_history = history;
    if (this.state.extra.trim()) {
      const extra = JSON.parse(this.state.extra);
      facts = deepMerge(facts, extra);
    }
    return facts;
  },
};
function deepMerge(a, b) {
  const out = Object.assign({}, a);
  for (const [k, v] of Object.entries(b || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && a[k] && typeof a[k] === "object" ? deepMerge(a[k], v) : v;
  }
  return out;
}

function selectEl(options, value, onchange) {
  return h("select", { onchange: (e) => onchange(e.target.value) },
    options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o || "—"]; return h("option", { value: v, selected: String(v) === String(value) ? true : null }, l); }));
}
function inputEl(type, value, onchange, placeholder, list) {
  return h("input", { type, value: value || "", placeholder: placeholder || "", list: list || null, oninput: (e) => onchange(e.target.value) });
}

VIEWS.consult = () => {
  const s = Consult.state;
  const root = h("div", { class: "grid side" });
  const left = h("div", { class: "stack" });
  const resultHost = h("div", { class: "stack" });

  const exRow = h("div", { class: "chips" }, (Store.examples || []).map((e) => h("button", { class: "btn sm", onclick: () => { Consult.load(e); Router.render(); } }, e.title)));
  left.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", null, "病例输入"), h("span", { class: "sub" }, "结构化事实优先；叙述文本用于急症筛查与问诊")),
    h("div", { class: "muted small", style: { marginBottom: "8px" } }, "载入示例："), exRow,
    h("div", { class: "hr" }),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "T", selectEl(T_OPTIONS, s.t, (v) => (s.t = v))),
      h("label", { class: "field" }, "N", selectEl(N_OPTIONS, s.n, (v) => (s.n = v))),
      h("label", { class: "field" }, "M", selectEl(M_OPTIONS, s.m, (v) => (s.m = v))),
      h("label", { class: "field" }, "前缀", selectEl([["c", "c（临床）"], ["p", "p（病理）"], ["yp", "yp（新辅助后）"]], s.prefix, (v) => (s.prefix = v))),
      h("label", { class: "field span-4" }, "病情叙述", h("span", { class: "hint" }, "急症信号按子句级否定筛查；明确阴性的脑影像陈述会被识别"),
        h("textarea", { oninput: (e) => (s.presentation = e.target.value), placeholder: "例：多站N2b，MDT判定不可切除。PET-CT+脑MRI确认M0。无咯血、无下肢无力、无发热。" }, s.presentation)),
      h("label", { class: "field span-4" }, "临床问题（可选）", inputEl("text", s.question, (v) => (s.question = v), "例：根治性方案与巩固治疗？")))));

  const groups = {};
  for (const f of FACT_FIELDS) (groups[f.g] = groups[f.g] || []).push(f);
  const factCard = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "结构化事实")));
  factCard.appendChild(h("datalist", { id: "driver-hints" }, DRIVER_HINTS.map((d) => h("option", { value: d }))));
  for (const [g, fields] of Object.entries(groups)) {
    const fs = h("fieldset", { style: { marginBottom: "12px" } }, h("legend", null, g));
    const grid = h("div", { class: "form-grid" });
    for (const f of fields) {
      const val = s.fields[f.key] || "";
      const set = (v) => { s.fields[f.key] = v; };
      let control;
      if (f.type === "select" || f.type === "int") control = selectEl(f.options, val, set);
      else if (f.type === "tri") control = selectEl(TRI, val, set);
      else if (f.type === "bool") control = selectEl([["", "未记录"], ["true", "是"], ["false", "否"]], val, set);
      else if (f.type === "number") control = inputEl("number", val, set);
      else if (f.type === "driver") control = inputEl("text", val, set, "报告原文，如 L858R / negative", "driver-hints");
      else control = inputEl("text", val, set);
      grid.appendChild(h("label", { class: "field" + (f.type === "list" ? " span-2" : "") }, f.label, control));
    }
    fs.appendChild(grid);
    factCard.appendChild(fs);
  }
  const histHost = h("div", { class: "stack" });
  const drawHistory = () => {
    clear(histHost);
    s.history.forEach((e, idx) => histHost.appendChild(h("div", { class: "form-grid" },
      h("label", { class: "field" }, "线次", inputEl("number", e.line, (v) => (e.line = v))),
      h("label", { class: "field span-2" }, "药物（逗号分隔）", inputEl("text", e.agents, (v) => (e.agents = v), "osimertinib / carboplatin, pemetrexed")),
      h("label", { class: "field" }, "结局", h("div", { class: "row", style: { flexWrap: "nowrap" } },
        selectEl([["", "未记录"], ["progression", "进展"], ["response", "缓解"], ["stable", "稳定"], ["toxicity", "毒性停药"]], e.status, (v) => (e.status = v)),
        h("button", { class: "icon-btn", title: "删除", onclick: () => { s.history.splice(idx, 1); drawHistory(); } }, icon("x")))))));
    histHost.appendChild(h("button", { class: "btn sm", onclick: () => { s.history.push({ line: String(s.history.length + 1), agents: "", status: "" }); drawHistory(); } }, "+ 添加一线治疗"));
  };
  drawHistory();
  factCard.appendChild(h("fieldset", { style: { marginBottom: "12px" } }, h("legend", null, "治疗史（后线序贯只在「进展」时触发）"), histHost));
  factCard.appendChild(h("label", { class: "field" }, "高级：附加事实 JSON（与上面合并，JSON 优先）",
    h("textarea", { class: "code", oninput: (e) => (s.extra = e.target.value), placeholder: '{"prior_systemic_therapy": "carboplatin-pemetrexed"}' }, s.extra)));
  left.appendChild(factCard);

  const runBtn = h("button", { class: "btn primary", style: { width: "100%" } }, icon("play"), "运行完整会诊");
  const run = async () => {
    let facts;
    try { facts = Consult.facts(); } catch (err) { toast(`附加事实 JSON 无效：${err.message}`, true); return; }
    const done = busy(runBtn, "受治理运行中…");
    try {
      const { result, ms } = await Bridge.timed("run_case", {
        t: s.t, n: s.n, m: s.m, prefix: s.prefix, presentation: s.presentation, question: s.question,
        facts, role: s.role, allow_dose_planning: s.dose, enable_panel: s.panel,
      });
      Consult.result = result; Consult.ms = ms;
      clear(resultHost).appendChild(renderResult(result, ms));
      resultHost.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) { toast(`运行失败：${err.message}`, true); } finally { done(); }
  };
  runBtn.addEventListener("click", run);

  const right = h("div", { class: "stack", style: { position: "sticky", top: "84px" } },
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "运行设置")),
      h("div", { class: "stack" },
        h("label", { class: "field" }, "角色（决定可见内容与剂量授权）",
          selectEl([["oncologist", "肿瘤科医师"], ["patient", "患者"], ["researcher", "研究者"]], s.role, (v) => (s.role = v))),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.dose || null, onchange: (e) => (s.dose = e.target.checked) }), "开启剂量规划（仅医师）"),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.panel || null, onchange: (e) => (s.panel = e.target.checked) }), "召集 MDT 会诊面板（需接入模型）"),
        runBtn,
        h("div", { class: "muted small" }, "当前：", (Store.llm && Store.llm.llm && Store.llm.llm.available) ? `模型辅助（${Store.llm.llm.provider}）` : "确定性模式（无需任何密钥）"))),
    h("div", { class: "callout" }, icon("info"), h("div", null, "分期由确定性引擎计算，模型无权改写；方案须通过适应证、器官闸门、证据蕴含与终审后才会放行。")));

  root.appendChild(h("div", { class: "stack" }, left, resultHost));
  root.appendChild(right);
  if (Consult.result) resultHost.appendChild(renderResult(Consult.result, Consult.ms));
  if (Consult.autorun) { Consult.autorun = false; setTimeout(run, 50); }
  return root;
};

/* ---------------------------------------------------------------- chat */

const Chat = { messages: [], last: null, role: "oncologist", dose: false, started: false, whatif: false, pending: { images: [], reports: [] } };

VIEWS.chat = () => {
  const root = h("div", { class: "grid side" });
  const log = h("div", { class: "chat-log" });
  const detailHost = h("div");
  const draw = () => {
    clear(log);
    if (!Chat.messages.length) {
      log.appendChild(h("div", { class: "empty" }, icon("chat"),
        h("div", null, "用自然语言描述病例，例如："),
        h("div", { class: "small" }, "“65岁男性，吸烟40包年，肺腺癌，cT2aN1M0，ECOG 1，EGFR阴性，PD-L1 60%，脑MRI阴性，无咯血无骨痛无头痛。”")));
    }
    for (const m of Chat.messages) {
      const bubble = h("div", { class: `msg ${m.role}${m.whatif ? " whatif" : ""}` }, m.text);
      if (m.meta) {
        bubble.appendChild(h("div", { class: "meta" }, statusPill(m.meta.release_status),
          m.meta.plan_reused ? h("span", { class: "chip" }, "方案复用") : null,
          m.meta.whatif ? h("span", { class: "chip trial" }, "假设推演 · 不写入会话") : null,
          h("span", { class: "chip" }, `${m.meta.ms} ms`),
          m.meta.llm_calls ? h("span", { class: "chip" }, `模型调用 ${m.meta.llm_calls}`) : null));
      }
      if (m.attachments) bubble.appendChild(h("div", { class: "meta" }, m.attachments.map((a) => h("span", { class: "chip" }, icon("doc"), a))));
      log.appendChild(bubble);
    }
    log.scrollTop = log.scrollHeight;
  };

  const input = h("textarea", { placeholder: "输入本轮信息或问题（Ctrl/⌘ + Enter 发送）…" });
  const factsInput = h("textarea", { class: "code", style: { minHeight: "70px" }, placeholder: '可选：结构化确认事实 JSON，如 {"treatment_history":[{"line":1,"agents":["osimertinib"],"status":"progression"}]}' });
  const attachList = h("div", { class: "attach-list" });
  const drawAttach = () => {
    clear(attachList);
    for (const kind of ["images", "reports"]) Chat.pending[kind].forEach((f, i) => attachList.appendChild(h("span", { class: "chip" }, kind === "images" ? "影像" : "报告", "：", f.name,
      h("button", { class: "btn sm ghost", style: { padding: "0 4px" }, onclick: () => { Chat.pending[kind].splice(i, 1); drawAttach(); } }, "×"))));
  };
  const picker = (kind) => h("input", { type: "file", accept: "image/*", multiple: true, style: { display: "none" }, onchange: async (e) => {
    for (const file of e.target.files) Chat.pending[kind].push({ name: file.name, bytes: await file.arrayBuffer() });
    e.target.value = ""; drawAttach();
    if (!(Store.llm && Store.llm.vision && Store.llm.vision.provider !== "none")) toast("读片/读报告需要在「模型接入」中配置视觉模型（如 Poe · gemini-3.1-pro）。未配置时附件会被标记跳过。");
  } });
  const imgPick = picker("images"); const repPick = picker("reports");
  const whatifToggle = h("label", { class: "check" }, h("input", { type: "checkbox", checked: Chat.whatif || null, onchange: (e) => (Chat.whatif = e.target.checked) }), "假设推演（what-if，不写入会话记忆）");
  const sendBtn = h("button", { class: "btn primary" }, icon("send"), "发送");

  const send = async () => {
    const text = input.value.trim();
    let facts = null;
    if (factsInput.value.trim()) { try { facts = JSON.parse(factsInput.value); } catch (err) { toast(`事实 JSON 无效：${err.message}`, true); return; } }
    if (!text && !facts && !Chat.pending.images.length && !Chat.pending.reports.length) return;
    const done = busy(sendBtn, "思考中…");
    try {
      if (!Chat.started) { await Bridge.call("chat_new", { role: Chat.role, allow_dose_planning: Chat.dose }); Chat.started = true; }
      const attachments = [...Chat.pending.images.map((f) => f.name), ...Chat.pending.reports.map((f) => f.name)];
      Chat.messages.push({ role: "user", text: text || (Chat.whatif ? "（假设推演）" : "（结构化事实）"), whatif: Chat.whatif, attachments: attachments.length ? attachments : null });
      draw();
      let out;
      if (Chat.whatif) out = await Bridge.timed("chat_whatif", { description: text, facts });
      else {
        const uploads = { images: Chat.pending.images.map((f) => ({ name: f.name, bytes: f.bytes })), reports: Chat.pending.reports.map((f) => ({ name: f.name, bytes: f.bytes })) };
        out = await Bridge.timed("chat_turn", { message: text, facts }, uploads);
        Chat.pending = { images: [], reports: [] }; drawAttach();
      }
      const r = out.result;
      Chat.last = r;
      Chat.messages.push({ role: "agent", text: r.reply, whatif: r.what_if, meta: { release_status: r.release_status, plan_reused: r.plan_reused, ms: out.ms, llm_calls: r.llm_calls, whatif: r.what_if } });
      input.value = ""; factsInput.value = "";
      draw(); drawSide(); drawDetail();
    } catch (err) { toast(`会诊失败：${err.message}`, true); Chat.messages.pop(); draw(); } finally { done(); }
  };
  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); });

  const chatCard = h("div", { class: "card chat" }, log,
    h("div", { class: "composer" }, input,
      h("details", null, h("summary", { class: "muted small", style: { cursor: "pointer" } }, "结构化事实确认（高级）"), factsInput),
      attachList,
      h("div", { class: "row" }, whatifToggle, h("span", { style: { flex: 1 } }),
        imgPick, repPick,
        h("button", { class: "btn sm", onclick: () => imgPick.click() }, icon("upload"), "影像"),
        h("button", { class: "btn sm", onclick: () => repPick.click() }, icon("doc"), "报告"),
        sendBtn)));

  const side = h("div", { class: "stack", style: { position: "sticky", top: "84px" } });
  const drawSide = () => {
    clear(side);
    const r = Chat.last;
    side.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "会话"), h("span", { class: "right" }, Chat.started ? h("span", { class: "chip ok" }, "进行中") : h("span", { class: "chip" }, "未开始"))),
      h("div", { class: "stack" },
        h("label", { class: "field" }, "角色（新会话生效）", selectEl([["oncologist", "肿瘤科医师"], ["patient", "患者"]], Chat.role, (v) => (Chat.role = v))),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: Chat.dose || null, onchange: (e) => (Chat.dose = e.target.checked) }), "开启剂量规划（新会话生效）"),
        h("div", { class: "row" },
          h("button", { class: "btn sm", onclick: async () => { await Bridge.call("chat_new", { role: Chat.role, allow_dose_planning: Chat.dose }); Chat.started = true; Chat.messages = []; Chat.last = null; draw(); drawSide(); drawDetail(); toast("已开始新会话"); } }, "新会话"),
          h("button", { class: "btn sm", disabled: !Chat.started || null, onclick: async () => downloadJSON("nsclc-session.json", await Bridge.call("chat_export")) }, icon("download"), "导出"),
          h("button", { class: "btn sm", onclick: () => importInput.click() }, icon("upload"), "导入")),
        h("div", { class: "muted small" }, "会话文件只携带记忆、不携带授权：导入时角色与剂量权限以上面的设置为准，方案缓存不从文件恢复。"))));
    if (r) {
      side.appendChild(h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h3", null, "最新一轮")),
        h("div", { class: "stack" }, statusPill(r.release_status),
          r.views && r.views.oncologist && r.views.oncologist.staging && r.views.oncologist.staging.stage_group
            ? h("div", { class: "kv" }, h("div", { class: "k" }, "分期"), h("div", null, h("strong", null, r.views.oncologist.staging.stage_group)),
                h("div", { class: "k" }, "方案"), h("div", { class: "chips" }, ((r.views.oncologist.treatment_plan || {}).regimen_ids || []).map(regimenChip)))
            : null,
          (r.notes || []).length ? h("details", null, h("summary", { class: "small" }, `提取备注 ${r.notes.length} 条`), fmtList(r.notes)) : null)));
      side.appendChild(h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h3", null, "累计事实"), h("span", { class: "sub" }, "会话记忆")),
        Object.keys(r.session_facts || {}).length ? jsonBlock(r.session_facts) : h("div", { class: "muted small" }, "暂无")));
    }
  };
  const importInput = h("input", { type: "file", accept: "application/json", style: { display: "none" }, onchange: async (e) => {
    const file = e.target.files[0]; if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const r = await Bridge.call("chat_import", { data, role: Chat.role, allow_dose_planning: Chat.dose });
      const transcript = data.transcript || [];
      const narrative = data.narrative || [];
      const turns = transcript.filter((t) => t.kind !== "what_if");
      let k = 0; /* user text is stored as narrative; pair it only when the counts line up */
      Chat.started = true; Chat.last = null;
      Chat.messages = transcript.flatMap((t) => {
        const whatif = t.kind === "what_if";
        const text = whatif ? (t.description || "（假设推演）") : (turns.length === narrative.length ? narrative[k++] : "（历史轮次）");
        return [{ role: "user", text, whatif }, { role: "agent", text: t.reply || "", whatif, meta: t.release_status ? { release_status: t.release_status, plan_reused: t.plan_reused, ms: Math.round((t.duration_s || 0) * 1000), whatif } : null }];
      });
      toast(`已导入会话：${r.turns} 轮，角色 ${r.role}`); draw(); drawSide();
    } catch (err) { toast(`导入失败：${err.message}`, true); }
    e.target.value = "";
  } });
  const drawDetail = () => {
    clear(detailHost);
    if (Chat.last && !Chat.last.what_if) {
      detailHost.appendChild(h("details", { class: "card", style: { marginTop: "18px" } },
        h("summary", { style: { cursor: "pointer", fontWeight: 650 } }, "本轮完整审计详情（分期、方案、安全审计、证据台账…）"),
        h("div", { style: { marginTop: "14px" } }, renderResult(Chat.last))));
    }
  };
  root.appendChild(h("div", null, chatCard, detailHost));
  root.appendChild(h("div", null, side, importInput));
  draw(); drawSide(); drawDetail(); drawAttach();
  return root;
};

/* ---------------------------------------------------------------- staging */

const StagingView = { t: "T2b", n: "N2b", m: "M0", prefix: "c", matrix: null };

VIEWS.staging = () => {
  const s = StagingView;
  const out = h("div");
  const compute = async () => {
    try {
      const r = await Bridge.call("stage", { t: s.t, n: s.n, m: s.m, prefix: s.prefix });
      clear(out).appendChild(r.refused
        ? h("div", { class: "stack" }, statusBanner("needs_staging_workup"),
            h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "引擎拒绝分期（不猜）")), h("div", null, r.reason)))
        : h("div", { class: "card" },
            h("div", { class: "row" }, h("div", { class: "stage-big" }, r.stage_group, h("small", null, r.tnm)),
              h("span", { style: { flex: 1 } }), h("span", { class: "chip regimen" }, r.module && r.module.module_key), h("span", { class: "chip" }, r.edition)),
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
  const matrixHost = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "M0 分期矩阵（第 9 版）"), h("span", { class: "sub" }, "点击任意格子查看")), h("div", { class: "muted small" }, "计算中…"));
  const Ts = ["T1mi", "T1a", "T1b", "T1c", "T2a", "T2b", "T3", "T4"];
  const Ns = ["N0", "N1", "N2a", "N2b", "N3"];
  const drawMatrix = async () => {
    if (!s.matrix) {
      s.matrix = {};
      for (const t of Ts) for (const n of Ns) {
        const r = await Bridge.call("stage", { t, n, m: "M0" });
        s.matrix[`${t}|${n}`] = r.refused ? "—" : r.stage_group;
      }
    }
    const grid = h("div", { class: "tn-grid" }, h("div"), Ns.map((n) => h("div", { class: "h" }, n)));
    for (const t of Ts) {
      grid.appendChild(h("div", { class: "h" }, t));
      for (const n of Ns) {
        const g = s.matrix[`${t}|${n}`];
        const lvl = g.startsWith("IV") ? 4 : g.startsWith("III") ? 3 : g.startsWith("II") ? 2 : g.startsWith("I") ? 1 : 0;
        grid.appendChild(h("div", { class: `tn-cell s${lvl}${t === s.t && n === s.n && s.m === "M0" ? " sel" : ""}`, onclick: () => { s.t = t; s.n = n; s.m = "M0"; Router.render(); } }, g));
      }
    }
    const body = matrixHost.lastChild; matrixHost.replaceChild(grid, body);
  };
  setTimeout(() => { compute(); drawMatrix(); }, 0);
  return h("div", { class: "grid cols-2" }, h("div", { class: "stack" }, controls, out), matrixHost);
};

/* ---------------------------------------------------------------- KG */

const KG = { query: "osimertinib", stage: "", gene: "", histology: "", jurisdiction: "", hits: null, info: null };

VIEWS.kg = () => {
  const root = h("div", { class: "stack" });
  const results = h("div", { class: "stack" });
  const infoHost = h("div");
  const search = async (btn) => {
    const done = btn ? busy(btn, "检索中…") : null;
    try {
      const r = await Bridge.call("kg_search", { query: KG.query, stage: KG.stage, gene: KG.gene, histology: KG.histology, jurisdiction: KG.jurisdiction, limit: 20 });
      KG.hits = r.hits; drawResults();
    } catch (err) { toast(err.message, true); } finally { if (done) done(); }
  };
  const drawResults = () => {
    clear(results);
    if (!KG.hits) return;
    if (!KG.hits.length) { results.appendChild(empty("无匹配推荐")); return; }
    for (const hit of KG.hits) {
      const grade = hit.grade || {};
      results.appendChild(h("div", { class: "card flat" },
        h("div", { class: "row" }, h("span", { class: "chip mono" }, hit.rec_id), h("strong", null, hit.guideline),
          h("span", { class: "chip" }, hit.region), hit.direction ? h("span", { class: `chip ${hit.direction === "recommend" ? "ok" : hit.direction.includes("against") ? "block" : ""}` }, hit.direction) : null,
          grade.strength ? h("span", { class: "chip trial" }, `${grade.scheme || ""} ${grade.strength_original || grade.strength}/${grade.evidence_original || grade.evidence}`) : null,
          h("span", { style: { flex: 1 } }),
          h("span", { class: `chip ${hit.curation_status === "clinician_verified" ? "ok" : "warn"}` }, hit.curation_status === "clinician_verified" ? "已临床复核" : "机器抽取 · 未复核")),
        hit.question ? h("div", { class: "muted small", style: { marginTop: "8px" } }, hit.question) : null,
        h("div", { style: { marginTop: "6px", fontWeight: 500 } }, hit.recommendation),
        h("div", { class: "row small muted", style: { marginTop: "8px" } },
          hit.topic ? h("span", null, `主题：${hit.topic}`) : null, hit.line ? h("span", null, `线次：${hit.line}`) : null,
          hit.provenance && hit.provenance.page ? h("span", null, `原文第 ${hit.provenance.page} 页`) : null)));
    }
  };
  const btn = h("button", { class: "btn primary" }, icon("kg"), "检索");
  btn.addEventListener("click", () => search(btn));
  const q = inputEl("text", KG.query, (v) => (KG.query = v), "关键词：osimertinib / 围术期 / PD-L1 …");
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(btn); });
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "form-grid" },
      h("label", { class: "field span-2" }, "关键词", q),
      h("label", { class: "field" }, "分期", selectEl([["", "全部"], "IA1", "IA2", "IA3", "IB", "IIA", "IIB", "IIIA", "IIIB", "IIIC", "IVA", "IVB"].map((x) => (Array.isArray(x) ? x : [x, x])), KG.stage, (v) => (KG.stage = v))),
      h("label", { class: "field" }, "基因", selectEl([["", "全部"], ...["EGFR", "ALK", "ROS1", "RET", "MET", "BRAF", "NTRK", "HER2", "KRAS"].map((x) => [x, x])], KG.gene, (v) => (KG.gene = v))),
      h("label", { class: "field" }, "组织学", selectEl([["", "全部"], ["adenocarcinoma", "腺癌"], ["squamous", "鳞癌"]], KG.histology, (v) => (KG.histology = v))),
      h("label", { class: "field" }, "地区", selectEl([["", "全部"], ["US", "美国（NCCN）"], ["EU", "欧洲（ESMO）"], ["CN", "中国（CSCO 等）"]], KG.jurisdiction, (v) => (KG.jurisdiction = v))),
      h("div", { class: "field span-2", style: { justifyContent: "flex-end" } }, btn))));
  root.appendChild(h("div", { class: "callout warn" }, icon("alert"), h("div", null, "知识图谱条目为机器抽取、默认未经临床复核：在智能体中它们只作为权衡上下文（证据等级 kg_llm_extracted，不可单独支撑放行），且已做剂量深度清洗。")));
  root.appendChild(infoHost);
  root.appendChild(results);
  Bridge.call("kg_info").then((info) => {
    KG.info = info;
    append(infoHost, [h("div", { class: "grid cols-4" }, stat(info.recommendations, "推荐条目"), stat(Object.keys(info.guidelines || {}).length, "指南"), stat(info.clusters, "跨区域聚类"), stat((info.curation || {}).clinician_verified || 0, "已临床复核"))]);
  }).catch(() => {});
  if (KG.hits) drawResults(); else search();
  return root;
};

/* ---------------------------------------------------------------- lab */

const Lab = { probes: null, sel: null, staging: '{\n  "stage_group": "IVB"\n}', facts: '{\n  "driver_mutations": {"egfr": "L858R", "alk": "negative"},\n  "histologic_category": "adenocarcinoma"\n}', plan: '{\n  "regimen_ids": ["pembro_monotherapy"],\n  "options": [{"name": "Pembrolizumab monotherapy", "regimen_ids": ["pembro_monotherapy"]}]\n}', result: null };

VIEWS.lab = () => {
  const root = h("div", { class: "grid side" });
  const out = h("div", { class: "stack" });
  const editors = h("div", { class: "grid cols-3" });
  const ed = (label, key) => h("label", { class: "field" }, label, h("textarea", { class: "code", style: { minHeight: "220px" }, oninput: (e) => (Lab[key] = e.target.value) }, Lab[key]));
  const drawEditors = () => { clear(editors); append(editors, [ed("分期 staging", "staging"), ed("事实 facts", "facts"), ed("方案 plan（模型可能写出的方案）", "plan")]); };
  drawEditors();
  const runBtn = h("button", { class: "btn primary" }, icon("lab"), "交给规则引擎终审");
  runBtn.addEventListener("click", async () => {
    let staging, facts, plan;
    try { staging = JSON.parse(Lab.staging); facts = JSON.parse(Lab.facts); plan = JSON.parse(Lab.plan); } catch (err) { toast(`JSON 无效：${err.message}`, true); return; }
    const done = busy(runBtn, "审计中…");
    try {
      const r = await Bridge.call("audit_plan", { staging, facts, plan });
      Lab.result = r; drawOut();
    } catch (err) { toast(err.message, true); } finally { done(); }
  });
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
  const probeList = h("div", { class: "stack", style: { maxHeight: "560px", overflowY: "auto" } });
  const drawProbes = () => {
    clear(probeList);
    for (const p of Lab.probes || []) {
      probeList.appendChild(h("button", { class: "example", style: Lab.sel && Lab.sel.id === p.id ? { borderColor: "var(--accent)" } : null, onclick: () => {
        Lab.sel = p;
        Lab.staging = JSON.stringify(p.audit_staging || {}, null, 2);
        Lab.facts = JSON.stringify(p.audit_facts || {}, null, 2);
        Lab.plan = JSON.stringify(p.audit_plan || {}, null, 2);
        Lab.result = null; drawEditors(); drawOut(); drawProbes();
      } }, h("div", { class: "t mono", style: { fontSize: "12px" } }, p.id), h("div", { class: "s" }, p.comment)));
    }
  };
  Bridge.call("golden_cases").then((cases) => { Lab.probes = cases.filter((c) => c.kind === "audit"); drawProbes(); }).catch((e) => toast(e.message, true));
  root.appendChild(h("div", { class: "stack" },
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "构造方案"), h("span", { class: "sub" }, "与审计型金标准探针同一调用：rules.check_plan(staging, facts, plan)")),
      editors, h("div", { class: "row", style: { marginTop: "12px" } }, runBtn,
        h("span", { class: "muted small" }, "试试：把 EGFR 改成 negative，或在 options 里写一个没有 regimen_ids 的药名。"))),
    out));
  root.appendChild(h("div", { class: "card", style: { position: "sticky", top: "84px" } },
    h("div", { class: "card-head" }, h("h3", null, "安全网探针"), h("span", { class: "sub" }, "点击载入")),
    probeList,
    h("div", { class: "hr" }),
    h("div", { class: "muted small", style: { marginBottom: "6px" } }, "规则清单"),
    h("div", { class: "chips" }, ((Store.info && Store.info.rule_ids) || []).map((r) => h("span", { class: "chip mono", style: { fontSize: "11px" } }, r)))));
  if (Lab.result) drawOut();
  return root;
};

/* ---------------------------------------------------------------- eval */

const Evalv = { report: null, ms: 0, filter: "all" };
const TAXO = { major_harmful: "方向性伤害", unsafe_release: "该拦未拦", overblocking: "过度拦截", false_alarm: "误报", omission: "遗漏", missing_workup: "漏补检", incorrect_release: "放行状态错误", staging_error: "分期错误", routing_error: "路由错误" };

VIEWS.eval = () => {
  const root = h("div", { class: "stack" });
  const host = h("div", { class: "stack" });
  const btn = h("button", { class: "btn primary" }, icon("play"), "在浏览器内运行全部金标准");
  btn.addEventListener("click", async () => {
    const done = busy(btn, "评测中（全部病例完整运行）…");
    try { const { result, ms } = await Bridge.timed("run_eval"); Evalv.report = result; Evalv.ms = ms; draw(); }
    catch (err) { toast(err.message, true); } finally { done(); }
  });
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
        h("div", { class: "kv" },
          h("div", { class: "k" }, "病例数"), h("div", null, (s.adjudication || {}).cases),
          h("div", { class: "k" }, "双人裁定"), h("div", null, (s.adjudication || {}).dual_adjudicated),
          h("div", { class: "k" }, "未裁定"), h("div", null, (s.adjudication || {}).unadjudicated)),
        h("div", { class: "muted small", style: { marginTop: "10px" } }, "裁定本身是人的工作；台账追加式、分歧并存、内容寻址作废（命令行 adjudicate）。"),
        s.unsafe_release_breakdown ? h("div", { style: { marginTop: "10px" } }, jsonBlock(s.unsafe_release_breakdown)) : null)));
    const filterSeg = h("div", { class: "seg" }, [["all", "全部"], ["pipeline", "流水线病例"], ["audit", "安全网探针"], ["failed", "失败"]].map(([k, l]) =>
      h("button", { class: Evalv.filter === k ? "on" : "", onclick: () => { Evalv.filter = k; draw(); } }, l)));
    const rows = r.results.filter((x) => Evalv.filter === "all" || (Evalv.filter === "failed" ? !x.passed : x.kind === Evalv.filter));
    host.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, "逐例结果"), h("div", { class: "right" }, filterSeg)),
      h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, ["病例", "类型", "结果", "分期 / 放行", "方案 / 触发规则"].map((x) => h("th", null, x)))),
        h("tbody", null, rows.map((x) => h("tr", null,
          h("td", { class: "mono" }, x.id),
          h("td", null, h("span", { class: `chip ${x.kind === "audit" ? "trial" : ""}` }, x.kind === "audit" ? "探针" : "流水线")),
          h("td", null, h("span", { class: `chip ${x.passed ? "ok" : "block"}` }, x.passed ? "通过" : "失败"), x.failures.length ? h("div", { class: "small", style: { marginTop: "4px" } }, x.failures.map((f) => `${TAXO[f.taxonomy] || f.taxonomy}: ${f.detail}`).join("；")) : null),
          h("td", null, x.kind === "audit" ? "—" : `${x.stage_group || "—"} · ${x.release_status}`),
          h("td", { class: "small" }, x.kind === "audit" ? (x.violations || []).map((v) => v.join(":")).join(", ") : (x.regimen_ids || []).join(", ")))))))));
  };
  root.appendChild(h("div", { class: "card" }, h("div", { class: "row" }, btn, h("span", { class: "muted small" }, "约 70 例 — 桌面浏览器通常 5–20 秒。结果与命令行 `python -m nsclc_agent eval` 完全一致（同一份代码）。"))));
  root.appendChild(host);
  draw();
  return root;
};

/* ---------------------------------------------------------------- settings */

const PROVIDERS = [
  { id: "none", name: "确定性模式", desc: "无需密钥 · 规则模式方案 · 完整安全治理" },
  { id: "poe", name: "Poe", desc: "一个密钥接入 Claude / Gemini / GPT 等；支持读片" },
  { id: "minimax", name: "MiniMax", desc: "MiniMax-M3 · 中国区 / 国际区" },
  { id: "azure", name: "Azure OpenAI", desc: "企业部署（需开启浏览器 CORS）" },
  { id: "mock", name: "离线 Mock", desc: "驱动完整工具循环的离线模型桩（演示用）" },
];
const Settings = {
  cfg: { provider: "none", api_key: "", model: "", base_url: "", region: "china", group_id: "", endpoint: "", api_version: "", vision: true, vision_model: "", remember: false },
  async apply(silent) {
    const r = await Bridge.call("configure_llm", Object.fromEntries(Object.entries(this.cfg).filter(([k]) => k !== "remember")));
    Store.llm = r; updateModelPill(r);
    try {
      if (this.cfg.remember) sessionStorage.setItem("nsclc-llm", JSON.stringify(this.cfg));
      else sessionStorage.removeItem("nsclc-llm");
    } catch (_) { /* storage unavailable */ }
    Chat.started = false;
    if (!silent) toast(r.llm.available ? `已接入 ${r.llm.provider} · ${r.llm.model}` : "已切换为确定性模式");
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
  const cards = h("div", { class: "grid cols-4", style: { gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" } });
  const drawCards = () => { clear(cards); for (const p of PROVIDERS) cards.appendChild(h("button", { class: `provider${c.provider === p.id ? " on" : ""}`, onclick: () => { c.provider = p.id; drawCards(); drawForm(); } }, h("div", { class: "t" }, p.name), h("div", { class: "s" }, p.desc))); };
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
        h("label", { class: "field span-2" }, "视觉模型（可选，留空不启用读片）", inputEl("text", c.vision_model, (v) => (c.vision_model = v)))]);
    } else if (c.provider === "azure") {
      append(grid, [key,
        h("label", { class: "field span-2" }, "Endpoint", inputEl("text", c.endpoint, (v) => (c.endpoint = v), "https://<resource>.openai.azure.com")),
        h("label", { class: "field" }, "Deployment", inputEl("text", c.model, (v) => (c.model = v))),
        h("label", { class: "field" }, "API Version", inputEl("text", c.api_version, (v) => (c.api_version = v), "2024-10-21"))]);
    } else {
      grid.appendChild(h("div", { class: "span-4 muted" }, c.provider === "mock" ? "离线模型桩：驱动完整的工具循环与会诊面板，不产生真实临床推理，用于演示治理机制。" : "无需任何配置。规则模式产出真实临床形状的方案，并经过与模型模式完全相同的安全治理。"));
    }
    if (["poe", "minimax", "azure"].includes(c.provider)) {
      grid.appendChild(h("label", { class: "check span-4" }, h("input", { type: "checkbox", checked: c.remember || null, onchange: (e) => (c.remember = e.target.checked) }), "在本标签页记住（sessionStorage，关闭标签页即清除）"));
    }
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
  const drawOut = () => {
    const l = Store.llm || {};
    out.appendChild(h("div", { class: "card flat" }, h("div", { class: "card-head" }, h("h3", null, "当前状态")),
      h("div", { class: "kv" }, h("div", { class: "k" }, "模式"), h("div", null, l.mode || "deterministic"),
        h("div", { class: "k" }, "文本模型"), h("div", null, l.llm && l.llm.available ? `${l.llm.provider} · ${l.llm.model}` : "无"),
        h("div", { class: "k" }, "视觉模型"), h("div", null, l.vision && l.vision.provider !== "none" ? `${l.vision.provider} · ${l.vision.model}` : "无"))));
  };
  drawCards(); drawForm(); drawOut();
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "选择模型服务")), cards, h("div", { class: "hr" }), form));
  root.appendChild(h("div", { class: "callout" }, icon("key"), h("div", null,
    "密钥只保存在本页的 Web Worker 内存中，请求从您的浏览器直接发往所选服务商（Poe 与 MiniMax 均允许浏览器跨域调用，已实测）。IMPF-AI 与 GitHub Pages 不经手您的密钥或病例。模型只能提议：分期、适应证、器官闸门、证据蕴含与终审规则仍由确定性内核裁决，剂量永远只来自确定性方案库。")));
  root.appendChild(out);
  return root;
};

/* ---------------------------------------------------------------- about */

VIEWS.about = () => {
  const i = Store.info || {};
  const b = Store.build || {};
  return h("div", { class: "stack" },
    h("section", { class: "hero" },
      h("span", { class: "hero-badge" }, h("span", { class: "impf-dot" }), "IMPF-AI 研发"),
      h("h2", null, "NSCLC-Agent"),
      h("p", null, "由 IMPF-AI 研发的非小细胞肺癌多学科决策支持智能体：证据治理、分期确定、安全终审。本网页端在浏览器内以 WebAssembly 运行与命令行完全相同的 Python 代码。")),
    h("div", { class: "grid cols-3" },
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "版本"),
        h("div", { class: "kv" }, h("div", { class: "k" }, "智能体"), h("div", null, `v${i.version || ""}`),
          h("div", { class: "k" }, "运行时"), h("div", null, i.runtime || ""),
          h("div", { class: "k" }, "构建"), h("div", { class: "mono" }, `${b.hash || ""} · ${b.built || ""}`))),
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "设计原则"),
        fmtList(["确定性分期是唯一分期权威", "模型只能提议，确定性内核裁决", "每个事实只有一种读法", "未放行的方案不展示给患者", "剂量只来自确定性方案库", "文件与日志不授予任何权限"])),
      h("div", { class: "card" }, h("h3", { style: { fontSize: "15px", marginBottom: "8px" } }, "重要声明"),
        h("div", { class: "muted" }, "本系统仅供教学与研究，不是医疗器械，不构成医疗建议；内置试验注册表、方案库与指南知识图谱为教学规模语料，须经本机构医师与药师复核。任何治疗决定请与主治团队确认。"))),
    h("div", { class: "card" }, h("div", { class: "row" },
      h("div", null, h("div", { style: { fontWeight: 700 } }, "© 2026 IMPF-AI 研发"), h("div", { class: "muted small" }, "NSCLC-Agent · MIT License")),
      h("span", { style: { flex: 1 } }),
      h("a", { class: "btn", href: "https://github.com/psknlr/NSCLC-Agent", target: "_blank", rel: "noopener" }, "GitHub 仓库"))));
};

/* ================================================================ theme */

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("nsclc-theme"); } catch (_) { /* ignore */ }
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = $("#theme-btn");
  const paint = () => {
    const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    clear(btn).appendChild(icon(dark ? "sun" : "moon"));
  };
  btn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("nsclc-theme", document.documentElement.dataset.theme); } catch (_) { /* ignore */ }
    paint();
  });
  paint();
}

/* ================================================================ start */

Router.buildNav();
initTheme();
window.addEventListener("hashchange", () => Router.render());
Router.render();
Bridge.start();
