/* Runs in the privileged Zotero dialog. All document-derived strings use textContent. */
/* global window, document */
let panel;
const states = { running: "处理中", starting: "准备中", completed: "待导入", imported: "已导入", failed: "失败", cancelled: "已取消", interrupted: "已中断" };
const stages = { assets: "检查字体和版面模型", parsing: "解析 PDF", cache: "读取段落缓存", translating: "翻译段落", typesetting: "生成中文排版", validating: "检查 PDF 完整性" };
const environmentStages = { idle: "等待准备环境", uv: "下载并校验 uv", python: "准备独立 Python", venv: "创建专用环境", dependencies: "安装固定版本依赖", bridge: "安装桥接模块", resources: "准备完整字体和模型", checking: "检查环境与资源", ready: "环境就绪", failed: "环境准备失败，可重试", cancelled: "环境准备已取消，可继续" };
const connectionStages = { closed: "未连接", connecting: "正在连接", connected: "已连接", closing: "正在关闭旧连接", failed: "连接失败" };
const operations = { diagnose: "正在检测登录与模型……", reconnect: "正在重新连接……", prepare: "正在准备环境……", repair: "正在检查与修复……" };
// A chrome dialog has no browser SelectParent popup host. Keep choices in the
// document flow instead of using an HTML select's out-of-process native popup.
function renderChoices(id, choices, value, disabled = false) {
  const field = document.getElementById(id);
  const picker = document.getElementById(`${id}-picker`);
  const options = document.getElementById(`${id}-options`);
  const signature = JSON.stringify(choices);
  if (options.dataset.choices !== signature) {
    const rows = choices.map(choice => {
      const label = document.createElementNS("http://www.w3.org/1999/xhtml", "label");
      label.className = "choice-row";
      const radio = document.createElementNS("http://www.w3.org/1999/xhtml", "input");
      radio.type = "radio";
      radio.name = `${id}-choice`;
      radio.value = choice.value;
      const text = document.createElementNS("http://www.w3.org/1999/xhtml", "span");
      text.textContent = choice.label;
      label.append(radio, text);
      radio.addEventListener("change", () => {
        if (!radio.checked || field.disabled) return;
        field.value = radio.value;
        picker.open = false;
        field.dispatchEvent(new window.Event("change", { bubbles: true }));
        document.getElementById(`${id}-current`).focus();
      });
      return label;
    });
    options.replaceChildren(...rows);
    options.dataset.choices = signature;
  }
  field.value = value;
  field.disabled = options.disabled = disabled;
  for (const radio of options.querySelectorAll("input")) radio.checked = radio.value === value;
  const current = document.getElementById(`${id}-current`);
  current.textContent = choices.find(choice => choice.value === value)?.label || choices[0]?.label || "暂无选项";
  current.setAttribute("aria-disabled", String(disabled));
  if (disabled) picker.open = false;
}
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
  const connection = state.connection || { phase: "closed" };
  document.getElementById("connection-state").textContent = `桥接：${connectionStages[connection.phase] || connection.phase}${connection.error ? ` · ${connection.error}` : ""}`;
  document.getElementById("connection-diagnostics").textContent = connection.details || "暂无连接诊断；点击检测或重新连接后可查看。";
  document.getElementById("operation-state").textContent = operations[state.operation] || "";
  document.getElementById("environment-state").textContent = state.bridgePath ? "使用高级设置中的外部桥接程序" : `${environmentStages[environment.stage] || environment.stage}${environment.file ? ` · ${environment.file}` : ""}${environment.total ? ` · ${(environment.completed / 1000000).toFixed(1)} / ${(environment.total / 1000000).toFixed(1)} MB` : ""}${environment.error ? ` · ${environment.error}` : environment.detail ? ` · ${environment.detail}` : ""}`;
  const environmentBar = document.getElementById("environment-progress");
  if (environment.total) environmentBar.value = 100 * environment.completed / environment.total;
  else if (environment.stage === "ready") environmentBar.value = 100;
  else environmentBar.removeAttribute("value");
  document.getElementById("prepare-environment").disabled = preparing || state.busy || !!state.operation || !!state.bridgePath;
  document.getElementById("repair-environment").disabled = preparing || state.busy || !!state.operation || !!state.bridgePath;
  document.getElementById("cancel-environment").disabled = !preparing && !["prepare", "repair"].includes(state.operation);
  document.getElementById("cancel-connection").disabled = !["diagnose", "reconnect"].includes(state.operation) || preparing;
  for (const action of ["diagnose", "reconnect", "refresh"]) document.querySelector(`[data-action="${action}"]`).disabled = !!state.operation || (action !== "refresh" && state.busy);
  for (const input of document.querySelectorAll('input:not([type="hidden"]):not([type="radio"]), [data-choose]')) input.disabled = !!state.operation;
  const models = [{ label: "使用 Codex 默认模型", value: "" }, ...(state.models || []).map(model => ({ label: model.displayName || model.model || model.id, value: model.model || model.id }))];
  if (state.model && !models.some(model => model.value === state.model)) models.push({ label: `${state.model}（上次选择，待检测）`, value: state.model });
  renderChoices("model", models, state.model || "", !!state.operation);
  const jobs = state.jobs.map(job => ({ label: `${states[job.state] || job.state} · ${job.model} · ${job.id.slice(0, 8)}`, value: job.id }));
  if (!jobs.length) jobs.push({ label: "暂无任务", value: "" });
  renderChoices("jobs", jobs, state.selectedJob?.id || "");
  const job = state.selectedJob;
  const progress = job?.progress || {};
  const metrics = progress.metrics || job?.metrics;
  document.getElementById("job-metrics").textContent = metrics ? `已处理 ${metrics.paragraphs || 0} 段 · 缓存命中 ${metrics.cacheHits || 0} · 模型请求 ${metrics.requests || 0} 次 · 重试 ${metrics.retries || 0} · 已用时 ${Math.round(metrics.elapsedSeconds || 0)} 秒${metrics.waitingSeconds ? ` · 等待模型 ${Math.round(metrics.waitingSeconds)} 秒` : ""}` : "";
  const pages = job?.result?.simplifiedPages || [];
  document.getElementById("layout-recovery").textContent = job?.result?.layoutMode === "recovered-existing" ? "恢复原任务已生成的 PDF（未重新排版、未调用模型）；原任务来自旧版桥接。" : ["native", "upstream-api"].includes(job?.result?.layoutMode) ? `BabelDOC ${job.result.engineVersion} 原生排版${job.sourceJobId ? " · 使用已有译文缓存" : ""}` : pages.length ? `旧版结果：第 ${pages.join("、")} 页采用简化排版；可使用缓存重新排版。` : "";
  document.getElementById("job-diagnostics").textContent = JSON.stringify({ metrics, error: job?.error, simplifiedPages: pages }, null, 2);
  document.getElementById("stage").textContent = job ? `${states[job.state] || job.state} · ${stages[progress.stage] || progress.detail || ""}${progress.total ? ` (${progress.completed || 0}/${progress.total})` : ""}` : "就绪";
  const bar = document.getElementById("progress");
  if (progress.percent !== undefined) bar.value = progress.percent;
  else if (["completed", "imported"].includes(job?.state)) bar.value = 100;
  else bar.removeAttribute("value");
  for (const id of ["start", "retranslate"]) document.getElementById(id).disabled = !!state.operation || state.busy || !state.sourceReady || (!state.bridgePath && environment.stage !== "ready");
  document.getElementById("resume").disabled = state.busy || !["failed", "cancelled", "interrupted"].includes(job?.state);
  document.getElementById("relayout").disabled = !!state.operation || state.busy || !state.relayoutSupported || !job || !state.sourceReady;
  document.getElementById("recover-output").disabled = !!state.operation || state.busy || !state.recoverySupported || !state.sourceReady || job?.state !== "failed" || !["UNREADABLE_TEXT","TEXT_OVERLAP","TEXT_CLIPPED","PAGE_COUNT","PAGE_SIZE","MISSING_PARAGRAPH_TEXT","MISSING_PAGE_TEXT"].includes(job?.error?.code);
  document.getElementById("cancel").disabled = !["running", "starting"].includes(job?.state);
  for (const id of ["translated", "compare"]) document.getElementById(id).disabled = !["completed", "imported"].includes(job?.state);
  document.getElementById("job-error").textContent = job?.error ? `${job.error.code}: ${job.error.message}` : "";
};
window.addEventListener("DOMContentLoaded", async () => {
  panel = window.arguments[0];
  const run = async (callback) => {
    try { await callback(); } catch (e) { panel.message(e.message || String(e)); }
  };
  for (const input of document.querySelectorAll('input:not([type="hidden"]):not([type="radio"]), #model')) input.addEventListener("change", () => panel.save(input.id, input.value));
  for (const id of ["model", "jobs"]) document.getElementById(`${id}-current`).addEventListener("click", event => {
    if (document.getElementById(id).disabled) event.preventDefault();
  });
  for (const button of document.querySelectorAll("[data-choose]")) button.addEventListener("click", () => run(() => panel.choose(button.dataset.choose)));
  const actions = { licenses: () => panel.licenses(), prepare: () => panel.prepareEnvironment(), repair: () => panel.prepareEnvironment(true), cancelEnvironment: () => panel.cancelEnvironment(), cancelConnection: () => panel.cancelConnection(), diagnose: () => panel.diagnose(), reconnect: () => panel.reconnect(), refresh: () => panel.refresh(), start: () => panel.start(), retranslate: () => panel.start(true), resume: () => panel.resume(), relayout: () => panel.relayout(), recoverOutput: () => panel.recoverOutput(), cancel: () => panel.cancel(), translated: () => panel.open("translated"), compare: () => panel.open("compare") };
  for (const button of document.querySelectorAll("[data-action]")) button.addEventListener("click", () => run(actions[button.dataset.action]));
  document.getElementById("jobs").addEventListener("change", (event) => panel.selectJob(event.target.value));
  await run(() => panel.bind(window));
});
