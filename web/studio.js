const form = document.querySelector("#run-form");
const benchmarkList = document.querySelector("#benchmark-list");
const preflightResult = document.querySelector("#preflight-result");
const launchButton = document.querySelector("#launch-button");
const statusLabel = document.querySelector("#run-status");
const runSummary = document.querySelector("#run-summary");
const runLog = document.querySelector("#run-log");
const savedSetupStatus = document.querySelector("#saved-setup-status");
const restoreSetupButton = document.querySelector("#restore-setup");
const forgetSetupButton = document.querySelector("#forget-setup");
const LOCAL_SETUP_KEY = "regression-lab.studio.local-setup.v1";
let preflightValid = false;

function values() {
  const data = new FormData(form);
  const launchMode = data.get("setup_mode") === "quick" ? "quick" : "spec_paths";
  return {
    launch_mode: launchMode, baseline: data.get("baseline")?.trim(), candidate: data.get("candidate")?.trim(),
    project_id: data.get("project_id")?.trim(), agent_id: data.get("agent_id")?.trim(),
    baseline_version: data.get("baseline_version")?.trim(), candidate_version: data.get("candidate_version")?.trim(),
    baseline_python_executable: data.get("baseline_python_executable")?.trim(), candidate_python_executable: data.get("candidate_python_executable")?.trim(), launch_target_kind: data.get("launch_target_kind"), baseline_entrypoint: data.get("baseline_entrypoint")?.trim(), candidate_entrypoint: data.get("candidate_entrypoint")?.trim(), observation_mode: data.get("observation_mode"),
    benchmarks: [...data.getAll("benchmarks")], trials: Number(data.get("trials")), execution_mode: data.get("execution_mode"), trusted_host_confirmed: data.get("trusted_host_confirmed") === "on",
  };
}
function escapeHtml(value) { const node = document.createElement("span"); node.textContent = String(value); return node.innerHTML; }
function readSavedSetup() {
  try { return JSON.parse(localStorage.getItem(LOCAL_SETUP_KEY)); }
  catch { return null; }
}
function updateSavedSetupStatus(message) {
  const available = Boolean(readSavedSetup());
  restoreSetupButton.disabled = !available; forgetSetupButton.disabled = !available;
  savedSetupStatus.textContent = message || (available ? "Saved in this browser" : "Not saved in this browser");
}
function applySavedSetup(saved) {
  if (!saved || typeof saved !== "object") return;
  const setupMode = document.querySelector(`input[name=setup_mode][value="${CSS.escape(saved.launch_mode || "quick")}"]`);
  if (setupMode) setupMode.checked = true;
  const simpleFields = ["baseline", "candidate", "project_id", "agent_id", "baseline_version", "candidate_version", "baseline_python_executable", "candidate_python_executable", "launch_target_kind", "baseline_entrypoint", "candidate_entrypoint", "observation_mode", "trials"];
  simpleFields.forEach(name => { const control = form.elements.namedItem(name); if (control && saved[name] !== undefined) control.value = saved[name]; });
  const executionMode = document.querySelector(`input[name=execution_mode][value="${CSS.escape(saved.execution_mode || "docker")}"]`);
  if (executionMode) executionMode.checked = true;
  const selectedBenchmarks = new Set(Array.isArray(saved.benchmarks) ? saved.benchmarks : []);
  document.querySelectorAll("input[name=benchmarks]").forEach(input => { input.checked = selectedBenchmarks.has(input.value); });
  form.elements.namedItem("trusted_host_confirmed").checked = false;
  setSetupMode(); syncExecutionMode(); preflightValid = false; launchButton.disabled = true;
}
function saveLocalSetup() {
  const setup = values();
  delete setup.trusted_host_confirmed;
  try { localStorage.setItem(LOCAL_SETUP_KEY, JSON.stringify(setup)); updateSavedSetupStatus("Saved locally · no secrets stored"); }
  catch { updateSavedSetupStatus("Browser storage is unavailable"); }
}
function restoreLocalSetup(message = true) {
  const saved = readSavedSetup();
  if (saved) { applySavedSetup(saved); updateSavedSetupStatus(message ? "Restored from this browser" : undefined); }
}
function forgetLocalSetup() {
  try { localStorage.removeItem(LOCAL_SETUP_KEY); } catch {}
  updateSavedSetupStatus("Local setup removed");
}
function renderPreflight(result) {
  preflightValid = result.valid === true; launchButton.disabled = !preflightValid;
  const errors = (result.errors || []).map(item => `<li>${escapeHtml(item)}</li>`).join("");
  const warnings = (result.warnings || []).map(item => `<li>${escapeHtml(item)}</li>`).join("");
  const configuration = result.configuration;
  preflightResult.className = `preflight-result ${preflightValid ? "valid" : "invalid"}`;
  preflightResult.innerHTML = configuration ? `<strong>Ready · ${escapeHtml(configuration.agent_id)} ${escapeHtml(configuration.baseline_version)} → ${escapeHtml(configuration.candidate_version)}</strong><p>${configuration.benchmark_count} Cases × ${configuration.trial_count / configuration.benchmark_count / 2} repeats × 2 versions = ${configuration.trial_count} Trials</p>${warnings ? `<ul>${warnings}</ul>` : ""}` : `<strong>Not ready</strong>${errors ? `<ul>${errors}</ul>` : ""}${warnings ? `<ul>${warnings}</ul>` : ""}`;
}
async function request(url, options) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok && !data.errors) data.errors = [data.error || "请求失败"]; return data; }
async function validate() { const result = await request("/api/preflight", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values())}); renderPreflight(result); return result; }
function renderRun(run) {
  const status = run.status || "idle"; statusLabel.textContent = status.toUpperCase(); statusLabel.className = `status ${status === "completed" ? "ok" : status === "failed" ? "bad" : "muted"}`;
  runLog.hidden = !(run.logs || []).length; runLog.textContent = (run.logs || []).join("\n");
  if (run.console_url) runSummary.innerHTML = `Artifacts are ready. <a href="${escapeHtml(run.console_url)}" target="_blank" rel="noopener">Open Observability Console →</a>`;
  else if (status === "running") runSummary.textContent = "Experiment is running. This page keeps the latest process output; artifacts will open in the Console when complete.";
  else if (status === "failed") runSummary.textContent = "Experiment setup failed. Review the final output, revise the configuration, then validate again.";
}
async function refreshRun() { const run = await request("/api/run"); renderRun(run); if (run.status === "running") setTimeout(refreshRun, 1000); }
document.querySelector("#preflight-button").addEventListener("click", event => { event.preventDefault(); validate(); });
form.addEventListener("change", () => { preflightValid = false; launchButton.disabled = true; });
form.addEventListener("submit", event => { event.preventDefault(); validate(); });
function syncExecutionMode() { document.querySelector("#trusted-confirmation").hidden = document.querySelector("input[name=execution_mode]:checked").value !== "trusted_host"; }
document.querySelectorAll("input[name=execution_mode]").forEach(input => input.addEventListener("change", syncExecutionMode));
function setSetupMode() {
  const quick = document.querySelector("input[name=setup_mode]:checked").value === "quick";
  document.querySelector("#quick-setup").hidden = !quick;
  document.querySelector("#spec-setup").hidden = quick;
  document.querySelectorAll("#quick-setup input, #quick-setup select").forEach(input => { input.disabled = !quick; });
  document.querySelectorAll("#spec-setup input").forEach(input => { input.disabled = quick; });
}
document.querySelectorAll("input[name=setup_mode]").forEach(input => input.addEventListener("change", setSetupMode));
document.querySelector("select[name=launch_target_kind]").addEventListener("change", event => {
  const module = event.target.value === "module";
  document.querySelectorAll(".target-label").forEach(label => { label.textContent = module ? "module name" : "entry file"; });
  document.querySelectorAll("input[name$=entrypoint]").forEach(input => { input.placeholder = module ? "standalone_langgraph_agent" : "/absolute/path/to/agent.py"; });
});
launchButton.addEventListener("click", async () => { const result = await request("/api/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values())}); if (result.errors) return renderPreflight(result); renderRun(result); refreshRun(); });
document.querySelector("#save-setup").addEventListener("click", saveLocalSetup);
restoreSetupButton.addEventListener("click", () => restoreLocalSetup());
forgetSetupButton.addEventListener("click", forgetLocalSetup);
request("/api/catalog").then(catalog => { document.querySelector("#case-count").textContent = `${catalog.benchmarks.length} available`; document.querySelectorAll("input[name$=python_executable]").forEach(input => { input.value = catalog.python_executable; }); benchmarkList.innerHTML = catalog.benchmarks.map((item, index) => `<label class="benchmark-choice"><input type="checkbox" name="benchmarks" value="${escapeHtml(item.id)}" ${index === 0 ? "checked" : ""}><span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.case_id)}</small></span></label>`).join(""); restoreLocalSetup(false); }).catch(() => { benchmarkList.textContent = "Could not load local Benchmark catalog."; });
setSetupMode();
syncExecutionMode();
updateSavedSetupStatus();
refreshRun();
