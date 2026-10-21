/* Runs in the privileged Zotero dialog. All document-derived strings use textContent. */
/* global window, document */
let panel;
const states = { running: "处理中", starting: "准备中", completed: "待导入", imported: "已导入", failed: "失败", cancelled: "已取消", interrupted: "已中断" };
const stages = { assets: "准备字体和版面模型（首次运行可能需要下载）", parsing: "解析 PDF", translating: "翻译段落", typesetting: "生成中文排版", validating: "检查 PDF 完整性" };
const environmentStages = { idle: "等待准备环境", uv: "下载并校验 uv", python: "准备独立 Python", venv: "创建专用环境", dependencies: "安装固定版本依赖", bridge: "安装桥接模块", resources: "准备完整字体和模型", checking: "检查环境与资源", ready: "环境就绪", failed: "环境准备失败，可重试", cancelled: "环境准备已取消，可继续" };
window.renderCodexPanel = function (state) {
  if (state.message !== undefined) document.getElementById("message").textContent = state.message;
  if (!state.title) return;
  document.getElementById("document-title").textContent = state.title;
  for (const name of ["bridgePath", "codexPath", "offlineAssets"]) {
    const field = document.getElementById(name);
    if (document.activeElement !== field) field.value = state[name];
  }
  const environment = state.runtime || { stage: "idle" };
  const preparing = !["idle", "ready", "failed", "cancelled"].includes(environment.stage);
  document.getElementById("environment-state").textContent = state.bridgePath ? "使用高级设置中的外部桥接程序" : `${environmentStages[environment.stage] || environment.stage}${environment.file ? ` · ${environment.file}` : ""}${environment.total ? ` · ${(environment.completed / 1000000).toFixed(1)} / ${(environment.total / 1000000).toFixed(1)} MB` : ""}${environment.error ? ` · ${environment.error}` : environment.detail ? ` · ${environment.detail}` : ""}`;
  const environmentBar = document.getElementById("environment-progress");
  if (environment.total) environmentBar.value = 100 * environment.completed / environment.total;
  else if (environment.stage === "ready") environmentBar.value = 100;
  else environmentBar.removeAttribute("value");
  document.getElementById("prepare-environment").disabled = preparing || state.busy || !!state.bridgePath;
  document.getElementById("repair-environment").disabled = preparing || state.busy || !!state.bridgePath;
  document.getElementById("cancel-environment").disabled = !preparing;
  if (state.models) {
    const select = document.getElementById("model");
    select.replaceChildren(new Option("使用 Codex 默认模型", ""));
    for (const model of state.models) select.add(new Option(model.displayName || model.model || model.id, model.model || model.id));
    select.value = state.model;
  }
  const jobs = document.getElementById("jobs");
  jobs.replaceChildren();
  if (!state.jobs.length) jobs.add(new Option("暂无任务", ""));
  for (const job of state.jobs) jobs.add(new Option(`${states[job.state] || job.state} · ${job.model} · ${job.id.slice(0, 8)}`, job.id));
  jobs.value = state.selectedJob?.id || "";
  const job = state.selectedJob;
  const progress = job?.progress || {};
  document.getElementById("stage").textContent = job ? `${states[job.state] || job.state} · ${stages[progress.stage] || progress.detail || ""}${progress.total ? ` (${progress.completed || 0}/${progress.total})` : ""}` : "就绪";
  const bar = document.getElementById("progress");
  if (progress.percent !== undefined) bar.value = progress.percent;
  else if (["completed", "imported"].includes(job?.state)) bar.value = 100;
  else bar.removeAttribute("value");
  for (const id of ["start", "retranslate"]) document.getElementById(id).disabled = state.busy || !state.sourceReady || (!state.bridgePath && environment.stage !== "ready");
  document.getElementById("resume").disabled = state.busy || !["failed", "cancelled", "interrupted"].includes(job?.state);
  document.getElementById("cancel").disabled = !["running", "starting"].includes(job?.state);
  for (const id of ["translated", "compare"]) document.getElementById(id).disabled = !["completed", "imported"].includes(job?.state);
  if (job?.error) document.getElementById("message").textContent = `${job.error.code}: ${job.error.message}`;
};
window.addEventListener("DOMContentLoaded", async () => {
  panel = window.arguments[0];
  const run = async (callback) => {
    try { await callback(); } catch (e) { panel.message(e.message || String(e)); }
  };
  for (const input of document.querySelectorAll("input, #model")) input.addEventListener("change", () => panel.save(input.id, input.value));
  for (const button of document.querySelectorAll("[data-choose]")) button.addEventListener("click", () => run(() => panel.choose(button.dataset.choose)));
  const actions = { licenses: () => panel.licenses(), prepare: () => panel.prepareEnvironment(), repair: () => panel.prepareEnvironment(true), cancelEnvironment: () => panel.cancelEnvironment(), diagnose: () => panel.diagnose(), reconnect: () => panel.reconnect(), refresh: () => panel.refresh(), start: () => panel.start(), retranslate: () => panel.start(true), resume: () => panel.resume(), cancel: () => panel.cancel(), translated: () => panel.open("translated"), compare: () => panel.open("compare") };
  for (const button of document.querySelectorAll("[data-action]")) button.addEventListener("click", () => run(actions[button.dataset.action]));
  document.getElementById("jobs").addEventListener("change", (event) => panel.selectJob(event.target.value));
  await run(() => panel.bind(window));
});
