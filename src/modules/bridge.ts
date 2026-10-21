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
  sourceJobId?: string;
  cacheOnly?: boolean;
  error?: { code: string; message: string };
  progress?: Record<string, any>;
  metrics?: Record<string, any>;
  result?: { outputPath: string; pageCount: number; sourcePageCount?: number; comparisonAvailable?: boolean; pageMap: number[]; simplifiedPages?: number[]; engine?: string; engineVersion?: string; layoutMode?: string; sourceJobId?: string; generatedByVersion?: string };
}

type Pending = { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };
type Connection = {
  id: number; process?: any; starting: Promise<void>; cancelled: boolean;
  abort: (error: Error) => void; pending: Map<number, Pending>; writes: Promise<any>;
  spawning?: Promise<any>; stopped?: Promise<void>; exited?: Promise<any>; stderrDone?: Promise<void>;
  command?: string; stderr: string; exitCode?: number; ready: boolean;
  capabilities?: string[];
};
export interface BridgeState {
  phase: "closed" | "connecting" | "connected" | "closing" | "failed";
  generation: number; error?: string; exitCode?: number; details?: string;
}

// Keep diagnostics local and bounded. Never include protocol payloads, document
// text, environment dumps or stderr emitted during translation.
function diagnostic(text: string) {
  return text.replace(/(Bearer\s+)\S+/gi, "$1[REDACTED]")
    .replace(/(["']?(?:[A-Z_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)|authorization)["']?\s*[=:]\s*["']?)[^\s,;"'}]+/gi, "$1[REDACTED]")
    .replace(/(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})/g, "[REDACTED]")
    .slice(-4096);
}

export class Bridge {
  private current?: Connection;
  private closing: Promise<void> = Promise.resolve();
  private generation = 0;
  private sequence = 0;
  private snapshot: BridgeState = { phase: "closed", generation: 0 };
  listeners = new Set<(event: any) => void>();

  status(): BridgeState { return { ...this.snapshot }; }
  supports(capability: string) { return !!this.current?.capabilities?.includes(capability); }
  private emit(event: any) {
    for (const listener of this.listeners) {
      try { listener(event); } catch (error) { Zotero.logError(error as Error); }
    }
  }
  private state(connection: Connection, phase: BridgeState["phase"], error?: string) {
    this.snapshot = { phase, generation: connection.id, error,
      exitCode: connection.exitCode,
      details: diagnostic([`连接 ${connection.id} · ${phase}`, connection.command ? `程序：${connection.command}` : "",
        connection.exitCode === undefined ? "" : `退出码：${connection.exitCode}`, error || "", connection.stderr].filter(Boolean).join("\n")) };
    this.emit({ event: "bridge.state", state: this.status() });
  }
  private assertCurrent(connection: Connection) {
    if (connection.cancelled || this.current !== connection) throw new Error("桥接连接已关闭或取消，请重新连接。");
  }
  private rejectPending(connection: Connection, error: Error) {
    for (const waiting of connection.pending.values()) { clearTimeout(waiting.timer); waiting.reject(error); }
    connection.pending.clear();
  }
  private stop(connection: Connection): Promise<void> {
    if (!connection.process && !connection.spawning) return Promise.resolve();
    if (connection.stopped) return connection.stopped;
    connection.stopped = (async () => {
      const process = connection.process || await connection.spawning?.catch(() => undefined);
      if (!process) return;
      connection.process = process;
      // EOF permits the server to stop its worker and Codex descendants.
      try { await process.stdin.close(); } catch { /* Already exited. */ }
      const timer = setTimeout(() => { try { void Promise.resolve(process.kill()).catch(() => {}); } catch { /* Already exited. */ } }, 10000);
      try { await (connection.exited || process.wait()); } catch { /* Already stopped. */ }
      finally { clearTimeout(timer); }
    })();
    return connection.stopped;
  }
  private fail(connection: Connection, error: Error) {
    if (connection.cancelled || this.current !== connection) return;
    connection.cancelled = true;
    this.current = undefined;
    connection.abort(error);
    this.rejectPending(connection, error);
    this.state(connection, "failed", diagnostic(error.message || String(error)));
    this.emit({ event: "bridge.error", error: this.snapshot.error });
    this.closing = Promise.all([this.closing, this.stop(connection)]).then(() => {});
  }

  private async ensureStarted(): Promise<Connection> {
    if (this.current) { const connection = this.current; await connection.starting; this.assertCurrent(connection); return connection; }
    let abort!: (error: Error) => void;
    const cancelled = new Promise<never>((_resolve, reject) => { abort = reject; });
    const connection: Connection = { id: ++this.generation, cancelled: false, abort, starting: Promise.resolve(),
      pending: new Map(), writes: Promise.resolve(), stderr: "", ready: false };
    this.current = connection;
    const previousClose = this.closing;
    const start = (async () => {
      await previousClose;
      this.assertCurrent(connection);
      const command = pref("bridgePath");
      const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs") as any;
      const pathUtils = (ztoolkit as any).getGlobal("PathUtils");
      const dataDir = pathUtils.join(Zotero.DataDirectory.dir, "codex-translate");
      const launch = command ? { command, arguments: ["--data-dir", dataDir] } : await runtime.ensureReady();
      this.assertCurrent(connection);
      connection.command = launch.command;
      connection.spawning = Subprocess.call({ ...launch, environmentAppend: true, stderr: "pipe" });
      const process = connection.process = await connection.spawning;
      if (connection.cancelled || this.current !== connection) { await this.stop(connection); this.assertCurrent(connection); }
      connection.stderrDone = this.readStderr(connection);
      const exited: Promise<any> = connection.exited = process.wait();
      void exited.then(async ({ exitCode }: any) => {
        connection.exitCode = exitCode;
        let timer: ReturnType<typeof setTimeout> | undefined;
        try { await Promise.race([connection.stderrDone, new Promise(resolve => { timer = setTimeout(resolve, 250); })]); }
        finally { if (timer !== undefined) clearTimeout(timer); }
        if (connection.cancelled && this.snapshot.phase === "failed" && this.snapshot.generation === connection.id) this.state(connection, "failed", this.snapshot.error);
        else this.fail(connection, new Error(`桥接程序已退出（退出码 ${exitCode}）；未完成的任务可以续跑。`));
      }).catch(error => this.fail(connection, error));
      void this.read(connection);
      const hello = await this.send(connection, "hello", {});
      connection.capabilities = hello.capabilities || [];
      this.assertCurrent(connection);
      connection.ready = true;
      // Startup diagnostics have served their purpose. Discard them before any
      // document can be submitted to the connection.
      connection.stderr = "";
      this.state(connection, "connected");
    })();
    connection.starting = Promise.race([start, cancelled]).catch(error => { this.fail(connection, error); throw error; });
    this.state(connection, "connecting");
    await connection.starting;
    this.assertCurrent(connection);
    return connection;
  }

  async request<T = any>(method: string, params: Record<string, any> = {}): Promise<T> {
    const connection = await this.ensureStarted();
    return this.send(connection, method, params);
  }
  private send(connection: Connection, method: string, params: Record<string, any>): Promise<any> {
    this.assertCurrent(connection);
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!connection.pending.has(id)) return;
        this.fail(connection, new Error(`${method}：桥接程序响应超时，请重新连接后重试。`));
      }, method === "jobs.start" || method === "diagnose" ? 180000 : 30000);
      connection.pending.set(id, { resolve, reject, timer });
      connection.writes = connection.writes.then(() => {
        this.assertCurrent(connection);
        return connection.process.stdin.write(JSON.stringify({ protocolVersion: 1, id, method, params }) + "\n");
      }).catch(error => this.fail(connection, error));
    });
  }
  private async readStderr(connection: Connection) {
    try {
      while (true) {
        const chunk = await connection.process.stderr.readString();
        if (!chunk) break;
        if (!connection.ready) connection.stderr = diagnostic(connection.stderr + chunk);
      }
    } catch { /* Process termination also closes its pipes. */ }
  }
  private async read(connection: Connection) {
    let buffer = "";
    try {
      while (true) {
        const chunk = await connection.process.stdout.readString();
        if (connection.cancelled || this.current !== connection) return;
        // EOF is reported by process.wait(), after stderr and the exit code
        // have been collected. It must never fail a different connection.
        if (!chunk) return;
        buffer += chunk;
        if (buffer.length > 4 * 1024 * 1024) throw new Error("桥接程序消息过大。");
        let index: number;
        while ((index = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
          if (!line.trim()) continue;
          let message: any;
          try { message = JSON.parse(line); } catch { throw new Error("桥接程序返回了无效 JSON，详情中不记录消息正文。"); }
          if (message.protocolVersion !== 1) throw new Error("桥接协议版本不兼容。");
          if (message.id !== undefined && message.id !== null) {
            const waiting = connection.pending.get(message.id);
            if (!waiting) continue;
            clearTimeout(waiting.timer); connection.pending.delete(message.id);
            if (message.error) waiting.reject(new Error(diagnostic(`${message.error.code}: ${message.error.message}`)));
            else waiting.resolve(message.result);
          } else if (message.event) this.emit(message);
        }
      }
    } catch (error) { this.fail(connection, error as Error); }
  }
  async close() {
    const connection = this.current;
    if (!connection) { await this.closing; return; }
    this.current = undefined;
    connection.cancelled = true;
    const error = new Error("桥接连接已关闭或取消。");
    connection.abort(error);
    this.rejectPending(connection, error);
    this.state(connection, "closing");
    this.closing = Promise.all([this.closing, this.stop(connection)]).then(() => {});
    await this.closing;
    if (!this.current && this.snapshot.generation === connection.id) this.state(connection, "closed");
  }
}

export const bridge = new Bridge();
