import { config } from "../../package.json";
import packageInfo from "../../package.json";
import { bridge, Job, pref, setTranslationPref } from "./bridge";
import { SplitViewFactory } from "./splitView";
import { runtime } from "./runtime";
import { ReaderToolbarRegistry, styleReaderToolbar, toolbarButton, preserveToolbarClick } from "./readerToolbar";

const RELATION = "dc:relation";
const URN = "urn:zotero-codex:translation:";
const menuIDs: string[] = [];
const dialogs = new Set<Window>();
const imports = new Map<string, Promise<Zotero.Item>>();
const activeJobs = new Set<string>();
const panels = new Set<TranslationPanel>();
const statusButtons = new Set<HTMLButtonElement>();
const compareButtons = new Map<HTMLButtonElement, any>();
type ToolbarEntry = { reader: any; group: HTMLDivElement; compare: HTMLButtonElement; status: HTMLButtonElement; releaseClick: () => void };
const toolbars = new ReaderToolbarRegistry<ToolbarEntry>();
const taskJobs = new Map<string, Job>();
const notifications = new Set<any>();
let submitting: { key: string; promise: Promise<void> } | undefined;
let stopping = false;
type SettingsOperation = { kind: string; cancelled: boolean; cancellation?: Promise<void> };
let settingsOperation: SettingsOperation | undefined;
const renderPanels = () => { for (const panel of panels) panel.render(); updateTaskButtons(); };
function updateTaskButtons() {
  updateCompareButtons();
  const running = [...taskJobs.values()].find(j => ["starting", "running", "cancelling"].includes(j.state));
  const failed = [...taskJobs.values()].some(j => ["failed", "interrupted"].includes(j.state));
  for (const button of statusButtons) {
    // Reader toolbar append() is deferred until its render event returns.
    // A newly registered button is intentionally not connected yet.
    try {
      if (button.ownerDocument?.defaultView?.closed) { statusButtons.delete(button); continue; }
      button.textContent = running || submitting ? "Codex 翻译中 · 查看进度" : failed ? "Codex 翻译任务 · 需处理" : "Codex 翻译任务";
    } catch { statusButtons.delete(button); } // A closed reader may be a Gecko dead object.
  }
}

export function comparisonPair(item: Zotero.Item): { original: Zotero.Item; translated: Zotero.Item } | undefined {
  const marker = association(item);
  const original = marker ? Zotero.Items.getByLibraryAndKey(item.libraryID, marker.split(":")[0]) : item;
  if (!original || original.deleted || !original.parentItemID || original.attachmentContentType !== "application/pdf") return;
  if (marker) return { original, translated: item };
  const parent = Zotero.Items.get(original.parentItemID as number);
  const translated = parent?.getAttachments().map(id => Zotero.Items.get(id)).filter(a => !a.deleted && association(a)?.split(":")[0] === original.key)
    .sort((a, b) => String(b.dateAdded || "").localeCompare(String(a.dateAdded || "")) || b.id-a.id)[0];
  return translated ? { original, translated } : undefined;
}
function updateCompareButtons() {
  for (const [button, reader] of compareButtons) {
    try {
      if (button.ownerDocument?.defaultView?.closed) { compareButtons.delete(button); continue; }
      const item = Zotero.Items.get(reader.itemID);
      const pair = item && comparisonPair(item);
      const busy = SplitViewFactory.comparisonOperations.status(reader.tabID);
      const active = SplitViewFactory.isComparison(reader.tabID);
      button.disabled = !!busy || (!active && !pair);
      button.setAttribute("aria-pressed", String(active));
      button.setAttribute("aria-busy", String(!!busy));
      const label = busy === "opening" ? "正在打开…" : busy === "closing" ? "正在关闭…" : "中英对照";
      if (button.textContent !== label) button.textContent = label;
      button.title = busy ? "正在切换中英对照" : active ? "退出中英对照，保留当前阅读位置" : pair ? "打开中英对照；双向连续同步，可切换独立浏览" : "尚无中文译文";
    } catch { compareButtons.delete(button); }
  }
}
function notice(text: string, jobId?: string) {
  if (stopping) return;
  try {
    const popup = new (Zotero as any).ProgressWindow({ closeOnClick: false });
    popup.changeHeadline("Codex 文献翻译");
    const line = new popup.ItemProgress(null, text);
    line.setProgress(100);
    popup.show();
    notifications.add(popup);
    const attach = (tries = 0) => {
      if (stopping) return;
      if (line._hbox) {
        line._hbox.style.cursor = "pointer";
        line._hbox.addEventListener("click", () => { popup.close(); notifications.delete(popup); void TranslationFactory.openTasks(jobId); });
      } else if (tries < 50) setTimeout(() => attach(tries + 1), 50);
    };
    attach();
    popup.startCloseTimer(5000);
    setTimeout(() => { popup.close(); notifications.delete(popup); }, 6000);
  } catch { (Zotero as any).debug?.(`Codex 翻译：${text}`); }
}
const completedListener = (event: any) => {
  if (event.event === "job.progress") {
    const job = taskJobs.get(event.jobId);
    if (job) job.progress = event;
    updateTaskButtons();
  }
  if (event.event === "job.changed") {
    taskJobs.set(event.job.id, event.job);
    if (["starting", "running", "cancelling"].includes(event.job?.state)) activeJobs.add(event.jobId || event.job.id);
    else activeJobs.delete(event.jobId || event.job?.id);
    updateTaskButtons();
    if (event.job?.state === "failed") notice(`翻译失败：${event.job.error?.message || "请查看任务详情"}`, event.job.id);
  }
  if (event.event !== "job.changed" || event.job?.state !== "completed") return;
  const job: Job = event.job;
  const source = Zotero.Items.getByLibraryAndKey(job.libraryID, job.sourceItemKey);
  if (source && !source.deleted) void finishJob(source, job).catch((error) => { Zotero.logError(error); notice(`译文已生成，导入失败：${error.message}`, job.id); });
};

const finishing = new Map<string, Promise<void>>();
async function finishJob(source: Zotero.Item, job: Job): Promise<void> {
  if (finishing.has(job.id)) return finishing.get(job.id);
  const promise = (async () => {
    await importResult(source, job);
    job.state = "imported";
    taskJobs.set(job.id, job);
    renderPanels();
    notice("中文 PDF 已添加到原文献下，点击查看任务。", job.id);
  })();
  finishing.set(job.id, promise);
  try { await promise; } finally { finishing.delete(job.id); }
}

async function listTasks(): Promise<Job[]> {
  const jobs = await bridge.request<Job[]>("jobs.list");
  taskJobs.clear(); activeJobs.clear();
  for (const job of jobs) {
    taskJobs.set(job.id, job);
    if (["starting", "running", "cancelling"].includes(job.state)) activeJobs.add(job.id);
    if (job.state === "completed") {
      const source = Zotero.Items.getByLibraryAndKey(job.libraryID, job.sourceItemKey);
      if (source && !source.deleted) await finishJob(source, job);
    }
  }
  updateTaskButtons();
  return jobs;
}

async function submitSource(source: Zotero.Item, force = false): Promise<void> {
  if (settingsOperation) throw new Error("环境或连接操作正在进行，请完成后再开始翻译。");
  const key = `${source.libraryID}:${source.key}`;
  if (submitting) {
    if (submitting.key === key && !force) { notice("此文献正在准备翻译，可从 Codex 翻译任务查看进度。"); return submitting.promise; }
    throw new Error("已有文献正在准备翻译，请等待或取消后再开始。");
  }
  const promise = (async () => {
    // Yield once so the shared submission guard is established before IO.
    await Promise.resolve();
    notice("正在后台准备全文翻译，可从 Codex 翻译任务查看进度。");
    const all = await listTasks();
    const running = all.find(j => ["starting", "running", "cancelling"].includes(j.state));
    if (running) {
      if (running.libraryID === source.libraryID && running.sourceItemKey === source.key && !force) { notice("此文献正在后台翻译。", running.id); return; }
      throw new Error("已有文献正在处理，请完成或取消后再开始。");
    }
    const info = await bridge.request<{ fingerprint: string }>("document.inspect", { sourcePath: await source.getFilePathAsync() });
    const jobs = all.filter(j => j.libraryID === source.libraryID && j.sourceItemKey === source.key && j.fingerprint === info.fingerprint);
    let job: Job;
    const existing = !force && jobs.find(j => ["completed", "imported"].includes(j.state) && findImported(source, j));
    if (existing) { const translated = await importResult(source, existing); await Zotero.Reader.open(translated.id); return; }
    const resume = !force && jobs.find(j => ["failed", "cancelled", "interrupted"].includes(j.state));
    if (resume) job = await bridge.request<Job>("jobs.resume", { jobId: resume.id });
    else job = await bridge.request<Job>("jobs.start", { sourcePath: await source.getFilePathAsync(), sourceItemKey: source.key,
      parentItemKey: Zotero.Items.get(source.parentItemID as number).key, libraryID: source.libraryID,
      model: pref("model"), codexPath: pref("codexPath"), offlineAssets: pref("offlineAssets") });
    taskJobs.set(job.id, job); activeJobs.add(job.id);
    notice("全文翻译已在后台运行，点击查看进度。", job.id);
  })();
  submitting = { key, promise }; renderPanels();
  try { await promise; } finally { submitting = undefined; renderPanels(); }
}

async function sourceFromSelection(readerItemID?: number): Promise<Zotero.Item> {
  const pane = Zotero.getActiveZoteroPane();
  const selection = pane.getSelectedItems();
  const reader = Zotero.Reader.getByTabID((Zotero.getMainWindow() as any).Zotero_Tabs.selectedID);
  let item: Zotero.Item | undefined = readerItemID ? Zotero.Items.get(readerItemID) : reader?.itemID ? Zotero.Items.get(reader.itemID) : selection.length === 1 ? selection[0] : undefined;
  if (!item) throw new Error("请选择一篇文献或一个 PDF 附件。");
  if (item.isRegularItem()) {
    const attachments = item.getAttachments().map((id) => Zotero.Items.get(id)).filter((a) => a.attachmentContentType === "application/pdf" && !association(a));
    if (!attachments.length) throw new Error("此文献没有原文 PDF 附件。");
    if (attachments.length === 1) item = attachments[0];
    else {
      const selected = { value: 0 };
      if (!Services.prompt.select(Zotero.getMainWindow() as any, "选择原文 PDF", "此文献有多个 PDF，请选择要翻译的附件。", attachments.map((a) => String(a.getField("title"))), selected)) throw new Error("已取消选择。");
      item = attachments[selected.value];
    }
  }
  if (association(item)) {
    const key = association(item)!.split(":")[0];
    const original = Zotero.Items.getByLibraryAndKey(item.libraryID, key);
    if (!original) throw new Error("找不到这份译文对应的原文附件。");
    item = original;
  }
  if (!item.isAttachment() || item.attachmentContentType !== "application/pdf") throw new Error("请选择 PDF 附件。");
  if (!item.parentItemID) throw new Error("请先为此 PDF 创建父文献条目，再进行翻译。");
  if (!(Zotero.Libraries.get(item.libraryID) as any).editable) throw new Error("此文献库没有写入权限。");
  if (!(await item.getFilePathAsync())) throw new Error("PDF 文件不在本机，请先下载附件。");
  return item;
}

export function association(item: Zotero.Item): string | undefined {
  const values = item.getRelations?.()[RELATION] || [];
  return (Array.isArray(values) ? values : [values]).find((value: string) => value.startsWith(URN))?.slice(URN.length);
}

function findImported(source: Zotero.Item, job: Job): Zotero.Item | undefined {
  const parent = Zotero.Items.get(source.parentItemID as number);
  const marker = `${source.key}:${job.fingerprint}:${job.id}`;
  return parent.getAttachments().map((id) => Zotero.Items.get(id)).find((item) => association(item) === marker || item.attachmentFilename === `codex-${job.id}.zh-CN.pdf`);
}

async function importResult(source: Zotero.Item, job: Job): Promise<Zotero.Item> {
  const active = imports.get(job.id);
  if (active) return active;
  const promise = performImport(source, job);
  imports.set(job.id, promise);
  try { return await promise; } finally { imports.delete(job.id); }
}

async function performImport(source: Zotero.Item, job: Job): Promise<Zotero.Item> {
  let translated = findImported(source, job);
  const marker = `${URN}${source.key}:${job.fingerprint}:${job.id}`;
  if (!translated) {
    if (job.state === "imported" && job.outputItemKey) throw new Error("此前导入的译文已被移除，请使用“重新翻译为新版本”。");
    if (!job.result?.outputPath) throw new Error("任务没有可导入的 PDF。");
    const parent = Zotero.Items.get(source.parentItemID as number);
    if (!parent || parent.deleted || !(Zotero.Libraries.get(parent.libraryID) as any).editable) throw new Error("父文献已删除或不可写；结果保留在任务中。");
    if (job.parentItemKey && parent.key !== job.parentItemKey) throw new Error("原文附件的父文献已改变，请从当前文献重新开始任务。");
    // importFromFile owns its DB transaction. Its deterministic filename is
    // committed with the attachment, closing the crash window before relations
    // and bridge acknowledgement are saved. Never nest Zotero transactions.
    translated = await Zotero.Attachments.importFromFile({ file: job.result!.outputPath, libraryID: parent.libraryID, parentItemID: parent.id,
      fileBaseName: `codex-${job.id}.zh-CN`, title: `中文译文 · ${new Date(job.createdAt * 1000).toLocaleString()}` });
  }
  // Repair the association when recovering from a crash after the attachment
  // transaction but before its relation was saved.
  if (association(translated!) !== marker.slice(URN.length)) {
    translated!.addRelation(RELATION, marker);
    await translated!.saveTx();
  }
  if (job.result?.sourcePageCount !== undefined) {
    const pageRelation = `urn:zotero-codex:page-counts:${job.result.sourcePageCount}:${job.result.pageCount}`;
    const relations = translated!.getRelations()[RELATION] || [];
    if (!(Array.isArray(relations) ? relations : [relations]).includes(pageRelation)) {
      translated!.addRelation(RELATION, pageRelation); await translated!.saveTx();
    }
  }
  await bridge.request("jobs.acknowledge", { jobId: job.id, outputItemKey: translated!.key });
  return translated!;
}

class TranslationPanel {
  source?: Zotero.Item;
  win?: Window;
  jobs: Job[] = [];
  selectedJob?: Job;
  busy = false;
  importing = new Set<string>();
  sourceFingerprint = "";
  allTasks = false;
  listener = (event: any) => { void this.onEvent(event).catch((e) => this.message(e.message)); };
  runtimeListener = () => this.render();

  constructor(source?: Zotero.Item) { this.source = source; }

  async bind(win: Window) {
    this.win = win;
    dialogs.add(win);
    panels.add(this);
    bridge.listeners.add(this.listener);
    runtime.listeners.add(this.runtimeListener);
    win.addEventListener("unload", () => { dialogs.delete(win); panels.delete(this); bridge.listeners.delete(this.listener); runtime.listeners.delete(this.runtimeListener); });
    this.render();
    if (pref("bridgePath") || runtime.status().stage === "ready") await this.refresh();
  }

  message(text: string) { (this.win as any)?.renderCodexPanel({ message: text }); }

  render(extra: Record<string, any> = {}) {
    if (this.allTasks) {
      this.jobs = [...taskJobs.values()].sort((a, b) => b.createdAt - a.createdAt);
      this.selectedJob = this.jobs.find(j => j.id === this.selectedJob?.id) || this.jobs[0];
      this.source = this.selectedJob ? Zotero.Items.getByLibraryAndKey(this.selectedJob.libraryID, this.selectedJob.sourceItemKey) || undefined : undefined;
      this.busy = activeJobs.size > 0;
    }
    (this.win as any)?.renderCodexPanel({
      title: this.allTasks ? "后台翻译任务" : this.source ? String(this.source.getField("title")) : "设置与环境检测",
      bridgePath: pref("bridgePath"), codexPath: pref("codexPath"), model: pref("model"), offlineAssets: pref("offlineAssets"),
      sourceReady: !!this.source, busy: this.busy || !!submitting, jobs: this.jobs, selectedJob: this.selectedJob, ...extra,
      runtime: runtime.status(), models: TranslationFactory.lastDiagnosis?.models || [],
      connection: bridge.status(), operation: settingsOperation?.kind,
      relayoutSupported: bridge.supports("jobs.relayout"),
      recoverySupported: bridge.supports("jobs.recoverOutput"),
    });
  }

  async choose(name: string) {
    const picker = new ztoolkit.FilePicker(name === "offlineAssets" ? "选择离线资源 ZIP" : "选择可执行文件", "open", [["All files", "*"]]);
    const path = await picker.open();
    if (path) { setTranslationPref(name, path); this.render(); }
  }

  save(name: string, value: string) {
    setTranslationPref(name, value);
    if (name === "codexPath") TranslationFactory.lastDiagnosis = undefined;
    this.render();
  }

  private async action(kind: string, message: string, callback: () => Promise<void>) {
    if (settingsOperation) throw new Error("连接操作正在进行，请等待完成或先取消。");
    const operation: SettingsOperation = settingsOperation = { kind, cancelled: false };
    this.message(message);
    renderPanels();
    try { await callback(); }
    catch (error) {
      await operation.cancellation;
      const message = operation.cancelled ? "连接操作已取消，可以重新尝试。" : `操作失败：${(error as Error).message || String(error)}`;
      this.message(message); throw new Error(message);
    }
    finally { if (settingsOperation === operation) settingsOperation = undefined; renderPanels(); }
  }

  async cancelConnection() {
    this.requireIdle();
    const operation = settingsOperation;
    if (operation) operation.cancelled = true;
    this.message("正在取消连接操作……");
    const cancellation = bridge.close();
    if (operation) operation.cancellation = cancellation;
    await cancellation;
    this.message("连接操作已取消，可以重新尝试。");
  }

  private requireIdle() {
    if (submitting) throw new Error("全文翻译正在准备，请稍后再检查与修复或重新连接。");
    // Job notifications from a dead connection are stale. Closing its process
    // and starting a new server recovers interrupted job records; stale UI
    // state must not permanently lock the repair/reconnect controls.
    const phase = bridge.status().phase;
    if (phase !== "failed" && phase !== "closed" && (activeJobs.size || [...panels, this].some(panel => panel.busy))) throw new Error("请先完成或取消翻译任务，再重新连接或检查与修复环境。");
  }

  async reconnect() {
    return this.action("reconnect", "正在关闭旧连接并重新连接桥接程序……", async () => {
      this.requireIdle();
      await bridge.close();
      await this.refresh();
      this.message("桥接已重新连接，可以检测登录与模型。");
    });
  }

  async prepareEnvironment(repair = false) {
    return this.action(repair ? "repair" : "prepare", repair ? "正在检查并修复环境……" : "正在准备运行环境……", async () => {
      if (pref("bridgePath")) throw new Error("当前使用高级设置的外部桥接程序；清空桥接路径后可使用自动环境。");
      this.requireIdle();
      // A failed bridge must not be a prerequisite for repairing that bridge.
      if (bridge.status().phase === "connected") {
        let jobs: Job[] = [];
        try { jobs = await bridge.request<Job[]>("jobs.list"); }
        catch (error) { if (bridge.status().phase !== "failed") throw error; }
        if (jobs.some(job => ["starting", "running", "cancelling"].includes(job.state))) throw new Error("请先完成或取消翻译任务，再检查与修复环境。");
      }
      await bridge.close();
      if (settingsOperation?.cancelled) throw new Error("环境操作已取消。");
      await (repair ? runtime.repair() : runtime.ensureReady());
      if (settingsOperation?.cancelled) throw new Error("环境操作已取消。");
      await this.refresh();
      this.message(repair ? "环境检查与修复完成，桥接已连接。" : "运行环境就绪，桥接已连接。");
    });
  }

  async cancelEnvironment() {
    const operation = settingsOperation;
    if (operation) operation.cancelled = true;
    this.message("正在取消环境准备……");
    const cancellation = (async () => { await bridge.close(); await runtime.cancel(); })();
    if (operation) operation.cancellation = cancellation;
    await cancellation;
    this.message("环境准备已取消，可稍后继续。");
  }

  async licenses() {
    // Use Zotero's URI API explicitly: getContentsAsync returns an XHR rather
    // than text for jar: resources in an installed XPI.
    const readText = async (name: string) => {
      const response = await Zotero.HTTP.request("GET", rootURI + name, { responseType: "text" });
      const text = response.responseText;
      if (typeof text !== "string") throw new Error(`无法读取插件许可文件：${name}`);
      return text;
    };
    const data = {
      notices: await readText("THIRD_PARTY_NOTICES.md"),
      license: await readText("LICENSE"),
      sourceURL: (packageInfo as { homepage?: string }).homepage || "",
    };
    Zotero.getMainWindow().openDialog(`chrome://${config.addonRef}/content/licenses.xhtml`, "", "chrome,centerscreen,resizable,dialog=no,width=850,height=740", data);
  }

  async diagnose() {
    return this.action("diagnose", "正在检测 Codex 登录、模型和 PDF 引擎……", async () => {
      this.requireIdle();
      const result = await TranslationFactory.diagnoseConnection();
      this.render({ message: result.engineReady ? "Codex 已登录，PDF 引擎就绪。模型列表不代表账号一定有对应额度。" : "PDF 引擎版本不匹配，请检查与修复运行环境。" });
    });
  }

  async refresh() {
    if (this.source && !this.allTasks) {
      const info = await bridge.request("document.inspect", { sourcePath: await this.source.getFilePathAsync() });
      this.sourceFingerprint = info.fingerprint;
    }
    const all = await listTasks();
    activeJobs.clear();
    for (const job of all) if (["starting", "running", "cancelling"].includes(job.state)) activeJobs.add(job.id);
    this.jobs = this.allTasks ? all : this.source ? all.filter((j) => j.sourceItemKey === this.source!.key && j.libraryID === this.source!.libraryID && j.fingerprint === this.sourceFingerprint) : [];
    this.selectedJob = this.jobs.find((j) => j.id === this.selectedJob?.id) || this.jobs[0];
    if (this.allTasks && this.selectedJob) this.source = Zotero.Items.getByLibraryAndKey(this.selectedJob.libraryID, this.selectedJob.sourceItemKey) || undefined;
    this.busy = all.some((j) => ["starting", "running", "cancelling"].includes(j.state));
    this.render();
    // Recover a completed translation after Zotero restarted before import.
    const completed = this.jobs.find((j) => j.state === "completed");
    if (completed) await this.complete(completed);
  }

  selectJob(id: string) { this.selectedJob = this.jobs.find((j) => j.id === id); if (this.allTasks) this.source = this.selectedJob ? Zotero.Items.getByLibraryAndKey(this.selectedJob.libraryID, this.selectedJob.sourceItemKey) || undefined : undefined; this.render(); }

  async start(force = false) {
    if (!this.source) throw new Error("请从文献菜单打开翻译窗口。");
    this.render({ message: "正在准备全文翻译……" });
    await submitSource(this.source, force);
    await this.refresh();
  }

  async resume() {
    if (!this.selectedJob) throw new Error("没有可续跑的任务。");
    await bridge.request("jobs.resume", { jobId: this.selectedJob.id });
    await this.refresh();
  }

  async relayout() {
    if (!this.selectedJob || !this.source) throw new Error("请先选择要重新排版的原任务。");
    if (this.busy || submitting) throw new Error("请先完成或取消当前任务。");
    this.busy = true; this.render({ message: "正在校验原文并使用缓存重新排版……" });
    try {
      const hello = await bridge.request("hello");
      if (!hello.capabilities?.includes("jobs.relayout")) throw new Error("当前外部桥接不支持缓存重排，请升级桥接程序。");
      const job = await bridge.request<Job>("jobs.relayout", { jobId: this.selectedJob.id, sourcePath: await this.source.getFilePathAsync() });
      taskJobs.set(job.id, job); activeJobs.add(job.id); this.selectedJob = job;
      notice("正在使用缓存重新排版，不调用模型；点击查看进度。", job.id);
      await this.refresh();
    } finally { this.busy = activeJobs.size > 0; this.render(); }
  }

  async recoverOutput() {
    if (!this.selectedJob || !this.source) throw new Error("请先选择被旧版检查拦截的任务。");
    if (this.busy || submitting) throw new Error("请先完成或取消当前任务。");
    this.busy = true; this.render({ message: "正在恢复原任务已生成的 PDF，不重新翻译……" });
    try {
      const hello = await bridge.request("hello");
      if (!hello.capabilities?.includes("jobs.recoverOutput")) throw new Error("当前桥接不支持恢复已有结果。");
      const job = await bridge.request<Job>("jobs.recoverOutput", { jobId: this.selectedJob.id, sourcePath: await this.source.getFilePathAsync() });
      taskJobs.set(job.id, job); this.selectedJob = job;
      if (job.state === "completed") await finishJob(this.source, job);
      await this.refresh();
      this.message("已恢复并导入原任务生成的 PDF；没有调用模型，原失败记录保留。");
    } finally { this.busy = activeJobs.size > 0; this.render(); }
  }

  async cancel() {
    if (!this.selectedJob) return;
    await bridge.request("jobs.cancel", { jobId: this.selectedJob.id });
    await this.refresh();
  }

  async complete(job: Job) {
    if (!this.source || this.importing.has(job.id)) return;
    this.importing.add(job.id);
    try {
      await finishJob(this.source, job);
      job.state = "imported";
      this.message("中文 PDF 已添加到原文献下，可单独打开或中英对照阅读。");
      this.render();
    } finally { this.importing.delete(job.id); }
  }

  async onEvent(event: any) {
    if (event.event === "bridge.state") { this.render(); return; }
    if (event.event === "bridge.error") { this.busy = false; this.message(event.error); return; }
    const job = this.jobs.find((j) => j.id === event.jobId);
    if (!job) return;
    if (event.event === "job.progress") {
      job.progress = event;
      if (job.id === this.selectedJob?.id) this.render();
    } else if (event.event === "job.changed") {
      Object.assign(job, event.job);
      this.busy = false;
      this.render({ message: job.error ? `${job.error.code}: ${job.error.message}` : undefined });
      if (job.state === "completed") await this.complete(job);
    }
  }

  async open(mode: "translated" | "compare") {
    if (!this.source || !this.selectedJob) throw new Error("请先选择已完成的任务。");
    if (mode === "compare" && this.selectedJob.result?.comparisonAvailable === false) throw new Error("原文与译文页数不同，无法按页对应；请单独打开中文 PDF。");
    const translated = await importResult(this.source, this.selectedJob);
    if (!(await translated.getFilePathAsync())) throw new Error("中文附件不在本机，请先下载附件。");
    if (mode === "compare") await SplitViewFactory.openTranslationPair(this.source.id, translated.id);
    else await Zotero.Reader.open(translated.id);
  }
}

export class TranslationFactory {
  static lastDiagnosis: any;
  private static diagnosis?: { generation: number; path: string; promise: Promise<any> };
  static diagnoseConnection(): Promise<any> {
    const path = pref("codexPath");
    const previous = this.diagnosis;
    if (previous && previous.generation === bridge.status().generation && previous.path === path) return previous.promise;
    const request = bridge.request("diagnose", { codexPath: path });
    const entry = { generation: bridge.status().generation, path, promise: Promise.resolve() as Promise<any> };
    entry.promise = request.then(result => {
      if (bridge.status().generation === entry.generation && pref("codexPath") === path) {
        this.lastDiagnosis = result; renderPanels();
      }
      return result;
    }).finally(() => { if (this.diagnosis === entry) this.diagnosis = undefined; });
    this.diagnosis = entry;
    return entry.promise;
  }
  static register() {
    stopping = false;
    SplitViewFactory.comparisonOperations.listeners.add(updateCompareButtons);
    bridge.listeners.add(completedListener);
    const manager = (Zotero as any).MenuManager;
    menuIDs.push(manager.registerMenu({ menuID: "codex-translate-item", pluginID: config.addonID, target: "main/library/item", menus: [
      { menuType: "menuitem", l10nID: `${config.addonRef}-translate-menu`, onShowing: (_event: any, context: any) => context.setVisible(context.items?.length === 1), onCommand: () => void this.startBackground().catch(this.error) },
    ] }));
    menuIDs.push(manager.registerMenu({ menuID: "codex-translate-settings", pluginID: config.addonID, target: "main/menubar/tools", menus: [
      { menuType: "menuitem", l10nID: `${config.addonRef}-translate-settings`, onCommand: () => void this.open(false).catch(this.error) },
      { menuType: "menuitem", l10nID: `${config.addonRef}-translate-tasks`, onCommand: () => void this.openTasks().catch(this.error) },
    ] }));
    Zotero.Reader.registerEventListener("renderToolbar", ({ doc, append, reader }: any) => {
      styleReaderToolbar(doc);
      const entry = toolbars.get(doc, () => {
        const group = doc.createElementNS("http://www.w3.org/1999/xhtml", "div") as HTMLDivElement;
        group.className = "codex-toolbar-group";
        const button = toolbarButton(doc, "Codex 全文翻译");
        button.title = "后台翻译当前文献；已有译文时打开已有附件";
        const compare = toolbarButton(doc, "中英对照", "codex-comparison-toggle");
        const status = toolbarButton(doc, "Codex 翻译任务", "codex-translation-task-status");
        status.style.cssText = "max-width:190px;overflow:hidden;text-overflow:ellipsis";
        status.title = "查看全部翻译任务及历史失败记录";
        const created = { reader, group, compare, status, releaseClick: preserveToolbarClick(doc, group) };
        button.addEventListener("click", () => void this.startBackground(false, created.reader?.itemID).catch(this.error));
        compare.addEventListener("click", async () => {
          const current = created.reader;
          if (compare.disabled || SplitViewFactory.comparisonOperations.status(current.tabID)) return;
          try {
            if (SplitViewFactory.isComparison(current.tabID)) await SplitViewFactory.closeComparison(current.tabID);
            else {
              const pair = comparisonPair(Zotero.Items.get(current.itemID));
              if (!pair) throw new Error("尚无中文译文，或关联原文已被移除。");
              // openTranslationPair establishes the tab guard before any asynchronous IO.
              await SplitViewFactory.openTranslationPair(pair.original.id, pair.translated.id, current);
            }
          } catch (error) { this.error(error as Error); }
        });
        status.addEventListener("click", () => void this.openTasks().catch(this.error));
        group.append(button, compare, status);
        statusButtons.add(status);
        return created;
      }, old => { try { old.releaseClick(); } catch { /* unloaded document */ } compareButtons.delete(old.compare); statusButtons.delete(old.status); });
      entry.reader = reader;
      compareButtons.set(entry.compare, reader);
      append(entry.group);
      updateTaskButtons();
    }, config.addonID);
  }

  static prepareEnvironment() {
    if (!pref("bridgePath")) void runtime.ensureReady().then(async () => {
      if (settingsOperation) return;
      try {
        await this.diagnoseConnection();
        await listTasks();
        renderPanels();
      } catch { /* Connection state is reported separately from environment readiness. */ }
    }).catch((error) => Zotero.logError(error));
  }

  static error(error: Error) { notice(error.message || String(error)); }

  static async startBackground(force = false, readerItemID?: number) { await submitSource(await sourceFromSelection(readerItemID), force); }

  static async openTasks(jobId?: string) {
    const panel = new TranslationPanel(); panel.allTasks = true;
    if (jobId) panel.selectedJob = taskJobs.get(jobId);
    return Zotero.getMainWindow().openDialog(`chrome://${config.addonRef}/content/translation.xhtml`, "", "chrome,centerscreen,resizable,dialog=no,width=800,height=740", panel);
  }

  static async open(withSource = true) {
    const source = withSource ? await sourceFromSelection() : undefined;
    const panel = new TranslationPanel(source);
    return Zotero.getMainWindow().openDialog(`chrome://${config.addonRef}/content/translation.xhtml`, "", "chrome,centerscreen,resizable,dialog=no,width=800,height=740", panel);
  }

  static async shutdown() {
    stopping = true;
    for (const popup of notifications) popup.close(); notifications.clear();
    await runtime.cancel();
    bridge.listeners.delete(completedListener);
    for (const win of dialogs) win.close();
    for (const id of menuIDs.splice(0)) (Zotero as any).MenuManager.unregisterMenu(id);
    await bridge.close();
    SplitViewFactory.comparisonOperations.listeners.delete(updateCompareButtons);
    toolbars.clear();
    taskJobs.clear(); statusButtons.clear(); compareButtons.clear();
  }
}
