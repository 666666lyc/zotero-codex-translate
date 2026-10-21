import { config } from "../../package.json";
import packageInfo from "../../package.json";
import { bridge, Job, pref, setTranslationPref } from "./bridge";
import { SplitViewFactory } from "./splitView";
import { runtime } from "./runtime";

const RELATION = "dc:relation";
const URN = "urn:zotero-codex:translation:";
const menuIDs: string[] = [];
const dialogs = new Set<Window>();
const imports = new Map<string, Promise<Zotero.Item>>();
const completedListener = (event: any) => {
  if (event.event !== "job.changed" || event.job?.state !== "completed") return;
  const job: Job = event.job;
  const source = Zotero.Items.getByLibraryAndKey(job.libraryID, job.sourceItemKey);
  if (source && !source.deleted) void importResult(source, job).catch((error) => Zotero.logError(error));
};

async function sourceFromSelection(): Promise<Zotero.Item> {
  const pane = Zotero.getActiveZoteroPane();
  const selection = pane.getSelectedItems();
  const reader = Zotero.Reader.getByTabID((Zotero.getMainWindow() as any).Zotero_Tabs.selectedID);
  let item: Zotero.Item | undefined = reader?.itemID ? Zotero.Items.get(reader.itemID) : selection.length === 1 ? selection[0] : undefined;
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
  const values = item.getRelations()[RELATION] || [];
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
  listener = (event: any) => { void this.onEvent(event).catch((e) => this.message(e.message)); };
  runtimeListener = () => this.render();

  constructor(source?: Zotero.Item) { this.source = source; }

  async bind(win: Window) {
    this.win = win;
    dialogs.add(win);
    bridge.listeners.add(this.listener);
    runtime.listeners.add(this.runtimeListener);
    win.addEventListener("unload", () => { dialogs.delete(win); bridge.listeners.delete(this.listener); runtime.listeners.delete(this.runtimeListener); });
    this.render();
    if (pref("bridgePath") || runtime.status().stage === "ready") await this.refresh();
  }

  message(text: string) { (this.win as any)?.renderCodexPanel({ message: text }); }

  render(extra: Record<string, any> = {}) {
    (this.win as any)?.renderCodexPanel({
      title: this.source ? String(this.source.getField("title")) : "设置与环境检测",
      bridgePath: pref("bridgePath"), codexPath: pref("codexPath"), model: pref("model"), offlineAssets: pref("offlineAssets"),
      sourceReady: !!this.source, busy: this.busy, jobs: this.jobs, selectedJob: this.selectedJob, ...extra,
      runtime: runtime.status(), models: TranslationFactory.lastDiagnosis?.models,
    });
  }

  async choose(name: string) {
    const picker = new ztoolkit.FilePicker(name === "offlineAssets" ? "选择离线资源 ZIP" : "选择可执行文件", "open", [["All files", "*"]]);
    const path = await picker.open();
    if (path) { setTranslationPref(name, path); this.render(); }
  }

  save(name: string, value: string) { setTranslationPref(name, value); }

  async reconnect() { await bridge.close(); await this.refresh(); }

  async prepareEnvironment(repair = false) {
    if (pref("bridgePath")) throw new Error("当前使用高级设置的外部桥接程序；清空桥接路径后可使用自动环境。");
    // Query the already running bridge without switching its environment.
    if (runtime.status().stage === "ready") {
      const jobs = await bridge.request<Job[]>("jobs.list");
      if (jobs.some((job) => ["starting", "running", "cancelling"].includes(job.state))) throw new Error("请先完成或取消翻译任务，再检查与修复环境。");
    }
    await bridge.close();
    await (repair ? runtime.repair() : runtime.ensureReady());
    await this.refresh();
  }

  async cancelEnvironment() { await runtime.cancel(); }

  async licenses() {
    const data = {
      notices: await Zotero.File.getContentsAsync(rootURI + "THIRD_PARTY_NOTICES.md"),
      license: await Zotero.File.getContentsAsync(rootURI + "LICENSE"),
      sourceURL: (packageInfo as { homepage?: string }).homepage || "",
    };
    Zotero.getMainWindow().openDialog(`chrome://${config.addonRef}/content/licenses.xhtml`, "", "chrome,centerscreen,resizable,dialog=no,width=850,height=740", data);
  }

  async diagnose() {
    this.message("正在检测 Codex 登录、模型和 PDF 引擎……");
    const result = await bridge.request("diagnose", { codexPath: pref("codexPath") });
    TranslationFactory.lastDiagnosis = result;
    this.render({ models: result.models, message: result.engineReady ? "Codex 已登录，PDF 引擎就绪。模型列表不代表账号一定有对应额度。" : "PDF 引擎版本不匹配，请安装配套桥接包。" });
  }

  async refresh() {
    if (this.source) {
      const info = await bridge.request("document.inspect", { sourcePath: await this.source.getFilePathAsync() });
      this.sourceFingerprint = info.fingerprint;
    }
    const all = await bridge.request<Job[]>("jobs.list");
    this.jobs = this.source ? all.filter((j) => j.sourceItemKey === this.source!.key && j.libraryID === this.source!.libraryID && j.fingerprint === this.sourceFingerprint) : [];
    this.selectedJob = this.jobs.find((j) => j.id === this.selectedJob?.id) || this.jobs[0];
    this.busy = all.some((j) => ["starting", "running", "cancelling"].includes(j.state));
    this.render();
    // Recover a completed translation after Zotero restarted before import.
    const completed = this.jobs.find((j) => j.state === "completed");
    if (completed) await this.complete(completed);
  }

  selectJob(id: string) { this.selectedJob = this.jobs.find((j) => j.id === id); this.render(); }

  async start(force = false) {
    if (!this.source) throw new Error("请从文献菜单打开翻译窗口。");
    if (this.busy) throw new Error("已有任务运行中。");
    if (!force) {
      const existing = this.jobs.find((j) => ["completed", "imported"].includes(j.state) && findImported(this.source!, j));
      if (existing) { this.selectedJob = existing; await this.open("translated"); return; }
      const resumable = this.jobs.find((j) => ["failed", "cancelled", "interrupted"].includes(j.state));
      if (resumable) { this.selectedJob = resumable; await this.resume(); return; }
    }
    this.busy = true;
    this.render({ message: "正在准备全文翻译……" });
    try {
      const job = await bridge.request<Job>("jobs.start", { sourcePath: await this.source.getFilePathAsync(), sourceItemKey: this.source.key,
        parentItemKey: Zotero.Items.get(this.source.parentItemID as number).key, libraryID: this.source.libraryID,
        model: pref("model"), codexPath: pref("codexPath"), offlineAssets: pref("offlineAssets") });
      this.jobs.unshift(job); this.selectedJob = job; this.render();
      await this.refresh();
    } catch (e) { this.busy = false; this.render(); throw e; }
  }

  async resume() {
    if (!this.selectedJob) throw new Error("没有可续跑的任务。");
    await bridge.request("jobs.resume", { jobId: this.selectedJob.id });
    await this.refresh();
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
      await importResult(this.source, job);
      job.state = "imported";
      this.message("中文 PDF 已添加到原文献下，可单独打开或中英对照阅读。");
      this.render();
    } finally { this.importing.delete(job.id); }
  }

  async onEvent(event: any) {
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
    const translated = await importResult(this.source, this.selectedJob);
    if (!(await translated.getFilePathAsync())) throw new Error("中文附件不在本机，请先下载附件。");
    if (mode === "compare") await SplitViewFactory.openTranslationPair(this.source.id, translated.id);
    else await Zotero.Reader.open(translated.id);
  }
}

export class TranslationFactory {
  static lastDiagnosis: any;
  static register() {
    bridge.listeners.add(completedListener);
    const manager = (Zotero as any).MenuManager;
    menuIDs.push(manager.registerMenu({ menuID: "codex-translate-item", pluginID: config.addonID, target: "main/library/item", menus: [
      { menuType: "menuitem", l10nID: `${config.addonRef}-translate-menu`, onShowing: (_event: any, context: any) => context.setVisible(context.items?.length === 1), onCommand: () => void this.open().catch(this.error) },
    ] }));
    menuIDs.push(manager.registerMenu({ menuID: "codex-translate-settings", pluginID: config.addonID, target: "main/menubar/tools", menus: [
      { menuType: "menuitem", l10nID: `${config.addonRef}-translate-settings`, onCommand: () => void this.open(false).catch(this.error) },
    ] }));
    Zotero.Reader.registerEventListener("renderToolbar", ({ doc, append }: any) => {
      const button = doc.createElement("button");
      button.textContent = "Codex 全文翻译";
      button.addEventListener("click", () => void this.open().catch(this.error));
      append(button);
    }, config.addonID);
  }

  static prepareEnvironment() {
    if (!pref("bridgePath")) void runtime.ensureReady().then(async () => {
      try {
        this.lastDiagnosis = await bridge.request("diagnose", { codexPath: pref("codexPath") });
        runtime.note("Codex 登录检测通过，模型列表已读取。");
      } catch (error) { runtime.note(`环境已保留；Codex 检测：${error}`); }
    }).catch((error) => Zotero.logError(error));
  }

  static error(error: Error) { Services.prompt.alert(Zotero.getMainWindow() as any, "Codex 文献翻译", error.message || String(error)); }

  static async open(withSource = true) {
    const source = withSource ? await sourceFromSelection() : undefined;
    const panel = new TranslationPanel(source);
    return Zotero.getMainWindow().openDialog(`chrome://${config.addonRef}/content/translation.xhtml`, "", "chrome,centerscreen,resizable,dialog=no,width=800,height=740", panel);
  }

  static async shutdown() {
    await runtime.cancel();
    bridge.listeners.delete(completedListener);
    for (const win of dialogs) win.close();
    for (const id of menuIDs.splice(0)) (Zotero as any).MenuManager.unregisterMenu(id);
    await bridge.close();
  }
}
