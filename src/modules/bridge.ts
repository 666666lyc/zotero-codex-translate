import { config } from "../../package.json";
import { runtime } from "./runtime";

export const pref = (name: string): string =>
  String(Zotero.Prefs.get(`extensions.zotero.${config.addonRef}.${name}`, true) || "");
export const setTranslationPref = (name: string, value: string) =>
  Zotero.Prefs.set(`extensions.zotero.${config.addonRef}.${name}`, value, true);

export interface Job {
  id: string;
  createdAt: number;
  state: string;
  sourceItemKey: string;
  parentItemKey: string;
  libraryID: number;
  fingerprint: string;
  model: string;
  outputItemKey?: string;
  error?: { code: string; message: string };
  progress?: Record<string, any>;
  result?: { outputPath: string; pageCount: number; pageMap: number[] };
}

export class Bridge {
  private process: any = null;
  private starting: Promise<void> | null = null;
  private sequence = 0;
  private pending = new Map<number, { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  private writes: Promise<any> = Promise.resolve();
  listeners = new Set<(event: any) => void>();

  private async ensureStarted() {
    if (this.starting) return this.starting;
    if (this.process) return;
    this.starting = (async () => {
      const command = pref("bridgePath");
      const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs") as any;
      const pathUtils = (ztoolkit as any).getGlobal("PathUtils");
      const dataDir = pathUtils.join(Zotero.DataDirectory.dir, "codex-translate");
      // stderr is deliberately separate from the protocol; it can contain text
      // from third-party PDF libraries and is not exposed in normal diagnostics.
      const launch = command ? { command, arguments: ["--data-dir", dataDir] } : await runtime.ensureReady();
      this.process = await Subprocess.call({ ...launch, environmentAppend: true, stderr: "ignore" });
      void this.read(this.process);
      await this.send("hello", {});
    })();
    try { await this.starting; }
    catch (e) { await this.close(); throw e; }
    finally { this.starting = null; }
  }

  async request<T = any>(method: string, params: Record<string, any> = {}): Promise<T> {
    await this.ensureStarted();
    return this.send(method, params);
  }

  private send(method: string, params: Record<string, any>): Promise<any> {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("桥接程序响应超时，请查看任务状态或重新连接。"));
      }, method === "jobs.start" || method === "diagnose" ? 180000 : 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.writes = this.writes.then(() => this.process.stdin.write(JSON.stringify({ protocolVersion: 1, id, method, params }) + "\n")).catch((error) => {
        this.fail(error);
      });
    });
  }

  private async read(process: any) {
    let buffer = "";
    try {
      while (true) {
        const chunk = await process.stdout.readString();
        if (!chunk) break;
        buffer += chunk;
        if (buffer.length > 4 * 1024 * 1024) throw new Error("桥接程序消息过大。");
        let index: number;
        while ((index = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, index);
          buffer = buffer.slice(index + 1);
          if (!line.trim()) continue;
          const message = JSON.parse(line);
          if (message.protocolVersion !== 1) throw new Error("桥接协议版本不兼容。");
          if (message.id !== undefined && message.id !== null) {
            const waiting = this.pending.get(message.id);
            if (!waiting) continue;
            clearTimeout(waiting.timer);
            this.pending.delete(message.id);
            if (message.error) waiting.reject(new Error(`${message.error.code}: ${message.error.message}`));
            else waiting.resolve(message.result);
          } else if (message.event) {
            for (const listener of this.listeners) {
              try { listener(message); } catch (error) { Zotero.logError(error as Error); }
            }
          }
        }
      }
      this.fail(new Error("桥接程序已退出；未完成的任务可以续跑。"));
    } catch (e) {
      this.fail(e as Error);
    } finally {
      if (this.process === process) this.process = null;
    }
  }

  private fail(error: Error) {
    for (const waiting of this.pending.values()) {
      clearTimeout(waiting.timer);
      waiting.reject(error);
    }
    this.pending.clear();
    for (const listener of this.listeners) listener({ event: "bridge.error", error: error.message });
  }

  async close() {
    const process = this.process;
    this.process = null;
    if (!process) return;
    // Closing stdin makes the bridge terminate its worker and all Codex children.
    try { await process.stdin.close(); } catch { /* Already exited. */ }
    const timer = setTimeout(() => { void process.kill(); }, 10000);
    try { await process.wait(); } finally { clearTimeout(timer); }
    this.fail(new Error("桥接程序已关闭。"));
  }
}

export const bridge = new Bridge();
