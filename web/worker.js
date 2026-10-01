/* NSCLC-Agent · browser runtime (IMPF-AI)
 *
 * Runs the unchanged Python package inside Pyodide in this Web Worker.
 * The page never touches Python objects: every call is one JSON string in
 * (nsclc_agent.webapi.call) and one JSON string out. Model calls the agent
 * makes use synchronous XHR from THIS worker (allowed in workers), straight
 * to the provider the visitor configured — keys never leave this worker
 * except toward that provider.
 */
"use strict";

const PYODIDE_VERSION = "0.27.7";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let api = null;
let pyodide = null;

/* Live agent progress: Python calls this mid-turn (js.agentEvent); the page
   receives each event while the worker is still busy with the turn. */
self.agentEvent = (json) => {
  try { self.postMessage({ type: "event", data: JSON.parse(json) }); } catch (_) { /* ignore */ }
};

function progress(stage, detail) {
  self.postMessage({ type: "progress", stage, detail });
}

async function boot() {
  try {
    progress("runtime", "加载 WebAssembly Python 运行时");
    importScripts(`${PYODIDE_BASE}pyodide.js`);
    pyodide = await self.loadPyodide({ indexURL: PYODIDE_BASE });

    progress("package", "加载智能体代码包");
    const build = await (await fetch("build.json", { cache: "no-store" })).json();
    const response = await fetch(`nsclc_agent.zip?v=${encodeURIComponent(build.hash)}`);
    if (!response.ok) throw new Error(`package download failed (${response.status})`);
    pyodide.unpackArchive(await response.arrayBuffer(), "zip", { extractDir: "/app" });

    progress("init", "初始化分期引擎、规则引擎与指南知识图谱");
    pyodide.runPython("import sys; sys.path.insert(0, '/app')");
    api = pyodide.pyimport("nsclc_agent.webapi");
    const envelope = JSON.parse(api.call("info", "{}"));
    if (!envelope.ok) throw new Error(envelope.error || "agent init failed");
    self.postMessage({ type: "ready", info: envelope.result, build });
  } catch (err) {
    self.postMessage({ type: "fatal", error: String((err && err.message) || err) });
  }
}

function writeUploads(files) {
  const paths = [];
  if (!files || !files.length) return paths;
  try { pyodide.FS.mkdirTree("/uploads"); } catch (_) { /* exists */ }
  for (const file of files) {
    const safe = String(file.name || "upload").replace(/[^\w.\-]+/g, "_");
    const path = `/uploads/${Date.now()}_${safe}`;
    pyodide.FS.writeFile(path, new Uint8Array(file.bytes));
    paths.push(path);
  }
  return paths;
}

self.onmessage = (event) => {
  const { id, name, payload, uploads } = event.data || {};
  if (!api) {
    self.postMessage({ type: "result", id, data: { ok: false, error: "runtime not ready" } });
    return;
  }
  try {
    const body = Object.assign({}, payload || {});
    if (uploads) {
      if (uploads.images && uploads.images.length) body.images = writeUploads(uploads.images);
      if (uploads.reports && uploads.reports.length) body.reports = writeUploads(uploads.reports);
    }
    const out = api.call(name, JSON.stringify(body));
    self.postMessage({ type: "result", id, data: JSON.parse(out) });
  } catch (err) {
    self.postMessage({ type: "result", id, data: { ok: false, error: String((err && err.message) || err) } });
  }
};

boot();
