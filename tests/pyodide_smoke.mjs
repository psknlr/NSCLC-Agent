// Browser-runtime smoke test: runs the packaged agent under the SAME
// Pyodide release the web app loads, in Node — no browser needed.
//
//   npm i --prefix /tmp/py pyodide@0.27.7
//   python web/build.py --out _site
//   PYODIDE_DIR=/tmp/py/node_modules/pyodide node tests/pyodide_smoke.mjs _site
//
// Catches what native tests cannot: stdlib modules Pyodide does not ship
// (ssl, sqlite3…), thread starts, and anything else that only fails under
// WebAssembly. Exits non-zero on the first broken contract.

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const site = resolve(process.argv[2] || "_site");
const dir = resolve(process.env.PYODIDE_DIR || "node_modules/pyodide");
const { loadPyodide } = await import(pathToFileURL(join(dir, "pyodide.mjs")).href);

const t0 = Date.now();
const pyodide = await loadPyodide({ indexURL: dir + "/" });
const archive = readFileSync(join(site, "nsclc_agent.zip"));
pyodide.unpackArchive(new Uint8Array(archive.buffer, archive.byteOffset, archive.byteLength), "zip", { extractDir: "/app" });
pyodide.runPython("import sys; sys.path.insert(0, '/app')");
const api = pyodide.pyimport("nsclc_agent.webapi");
console.log(`runtime + package ready in ${Date.now() - t0} ms`);

let failures = 0;
function call(name, payload = {}) {
  const out = JSON.parse(api.call(name, JSON.stringify(payload)));
  if (!out.ok) throw new Error(`${name}: ${out.error}`);
  return out.result;
}
function check(label, cond, detail = "") {
  console.log(`${cond ? "PASS" : "FAIL"} ${label} ${detail}`);
  if (!cond) failures += 1;
}

const info = call("info");
check("runtime is the browser build", info.runtime.startsWith("browser"), info.runtime);
check("rules loaded", info.counts.rules >= 20, `${info.counts.rules}`);

const staged = call("stage", { t: "T2b", n: "N2b", m: "M0" });
check("staging T2bN2bM0 = IIIB", staged.stage_group === "IIIB");
check("ambiguous N2 refused", call("stage", { t: "T2b", n: "N2", m: "M0" }).refused === true);

for (const example of call("examples")) {
  const r = call("run_case", Object.assign({}, example.case, { role: "oncologist" }));
  check(`example ${example.id}`, Boolean(r.release_status), r.release_status);
}
const patient = call("run_case", Object.assign({}, call("examples")[0].case, { role: "patient" }));
check("patient role returns the patient view only", Object.keys(patient.views).join() === "patient");

call("configure_llm", { provider: "mock" });
const panel = call("run_case", Object.assign({}, call("examples")[1].case, { role: "oncologist", enable_panel: true }));
check("mock model + MDT panel (serial scheduling)", Boolean(panel.release_status), panel.release_status);
call("chat_new", { role: "oncologist" });
const turn = call("chat_turn", { message: "cT2aN0M1b 肺腺癌，脑MRI阴性，EGFR阴性，ALK阴性。" });
check("chat turn under mock", turn.turns === 1 && Boolean(turn.reply));
call("configure_llm", { provider: "none" });

check("guideline KG loads", call("kg_info").recommendations > 1000);
check("KG search", call("kg_search", { query: "osimertinib", limit: 5 }).hits.length > 0);

const report = call("run_eval");
const s = report.summary;
check("golden eval all green", s.all_passed, `${s.passed}/${s.total} · unsafe ${s.unsafe_release_rate}`);

console.log(`\n${failures ? `${failures} FAILED` : "all checks passed"} in ${Date.now() - t0} ms`);
process.exit(failures ? 1 : 0);
