/** Private upstream runtime bootstrap. No shell, global Python or PATH edits. */
import { config } from "../../package.json";

export interface LaunchConfig { command: string; arguments: string[]; environment?: Record<string, string | null>; }
export interface RuntimeState { stage: string; detail?: string; error?: string; completed?: number; total?: number; file?: string; }

class RuntimeManager {
  private preparing: Promise<LaunchConfig> | null = null;
  private child: any = null;
  private abortDownload?: () => void;
  private cancelled = false;
  private snapshot: RuntimeState = { stage: "idle" };
  private readyConfig?: { root: string; config: LaunchConfig };
  listeners = new Set<(state: RuntimeState) => void>();
  private get paths(): any { return (ztoolkit as any).getGlobal("PathUtils"); }
  private get io(): any { return (ztoolkit as any).getGlobal("IOUtils"); }
  private get root() { return this.paths.join(Zotero.DataDirectory.dir, "codex-translate", "runtime"); }
  private get bundleURL() { return `chrome://${config.addonRef}/content/runtime/`; }
  status() { return { ...this.snapshot }; }
  note(detail: string) { this.update({ ...this.snapshot, detail }); }
  private update(state: RuntimeState) { this.snapshot = state; for (const listener of this.listeners) listener(this.status()); }
  private checkCancelled() { if (this.cancelled) throw new Error("环境准备已取消，可稍后继续。"); }
  private async manifest() { return JSON.parse(String((await Zotero.HTTP.request("GET", this.bundleURL + "manifest.json")).responseText)); }
  private launch(record: any): LaunchConfig {
    return { command: record.python, arguments: ["-I", "-m", "zotero_codex_bridge", "--data-dir", this.paths.parent(this.root)],
      environment: { ZOTERO_CODEX_MANAGED: "1", ZOTERO_CODEX_ASSET_DIR: record.resourceDir, PYTHONUTF8: "1" } };
  }

  async ensureReady(): Promise<LaunchConfig> {
    if (this.preparing) return this.preparing;
    const ready = this.readyConfig;
    if (this.snapshot.stage === "ready" && ready && ready.root === this.root) return ready.config;
    this.cancelled = false;
    this.preparing = this.prepare(false);
    try { const result = await this.preparing; this.readyConfig = { root: this.root, config: result }; return result; }
    catch (error) { this.update({ stage: this.cancelled ? "cancelled" : "failed", error: String(error) }); throw error; }
    finally { this.preparing = null; }
  }

  async repair(): Promise<LaunchConfig> {
    if (this.preparing) return this.preparing;
    this.cancelled = false;
    this.readyConfig = undefined;
    this.preparing = this.prepare(true);
    try { const result = await this.preparing; this.readyConfig = { root: this.root, config: result }; return result; }
    catch (error) { this.update({ stage: this.cancelled ? "cancelled" : "failed", error: String(error) }); throw error; }
    finally { this.preparing = null; }
  }

  async cancel() {
    this.cancelled = true;
    this.abortDownload?.();
    const child = this.child;
    if (child) {
      try { await child.stdin.close(); } catch { /* exited */ }
      // The installer observes EOF and terminates its entire uv child tree.
      const timer = setTimeout(() => { void child.kill(); }, 45000);
      try { await child.wait(); } finally { clearTimeout(timer); }
    }
  }

  private async digest(path: string) {
    const hash = (Components as any).classes["@mozilla.org/security/hash;1"].createInstance((Components as any).interfaces.nsICryptoHash);
    hash.init(hash.SHA256);
    const stream = (Components as any).classes["@mozilla.org/network/file-input-stream;1"].createInstance((Components as any).interfaces.nsIFileInputStream);
    stream.init(Zotero.File.pathToFile(path), 1, 0, 0);
    try { hash.updateFromStream(stream, 0xffffffff); } finally { stream.close(); }
    return Array.from(hash.finish(false) as string, (c) => c.charCodeAt(0).toString(16).padStart(2, "0")).join("");
  }

  private async run(command: string, args: string[], environment: Record<string, string | null>, stage: string, json = false): Promise<any> {
    this.checkCancelled();
    this.update({ stage });
    const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs") as any;
    const isolatedEnvironment = Subprocess.getEnvironment();
    for (const key of Object.keys(isolatedEnvironment)) if (/^(UV_|PIP_|PYTHON|VIRTUAL_ENV|CONDA_)/i.test(key)) delete isolatedEnvironment[key];
    Object.assign(isolatedEnvironment, environment);
    const child = this.child = await Subprocess.call({ command, arguments: args, environment: isolatedEnvironment, stderr: "stdout" });
    // One-shot checks never consume stdin. In particular, don't leave a Gecko
    // named pipe open across the Windows venv launcher. The installer keeps
    // its own pipe open so EOF remains its explicit cancellation signal.
    if (!json) await child.stdin.close();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; void child.stdin.close(); void child.kill(); }, json ? 60 * 60 * 1000 : 10 * 60 * 1000);
    let buffer = "", output = "", result: any;
    try {
      while (true) {
        const chunk = await child.stdout.readString();
        if (!chunk) break;
        output = (output + chunk).slice(-6000);
        buffer += chunk;
        if (buffer.length > 65536) buffer = buffer.slice(-65536);
        let index: number;
        while ((index = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1);
          if (json && line.startsWith("{")) {
            const event = JSON.parse(line); this.update(event); if (event.result) result = event.result;
          } else if (line) this.update({ stage, detail: line });
        }
      }
      const { exitCode } = await child.wait();
      this.checkCancelled();
      if (timedOut) throw new Error(`${stage}：环境检查或下载超时，请检查网络后继续。`);
      if (exitCode !== 0) throw new Error(`${stage}: ${output}`);
      return result || output.trim();
    } finally { clearTimeout(deadline); if (this.child === child) this.child = null; }
  }

  private async prepareLinux(): Promise<LaunchConfig> {
    const manifest = await this.manifest();
    const pointer = this.paths.join(this.root, "linux-current.json");
    if (!(await this.io.exists(pointer))) throw new Error("Linux 需先使用 Python 3.12 运行源码中的 scripts/install_linux.py --data-dir <Zotero数据目录>/codex-translate；详见 docs/LINUX.md。");
    const record = await this.io.readJSON(pointer);
    const expectedPython = this.paths.join(this.root, "linux-env", "bin", "python");
    const expectedAssets = this.paths.join(this.paths.parent(this.root), "engine-assets");
    if (record.runtimeRoot !== this.root || record.python !== expectedPython || record.resourceDir !== expectedAssets || record.bridgeVersion !== manifest.bridgeVersion) {
      throw new Error("Linux 环境路径或桥接版本不匹配，请重新运行 scripts/install_linux.py 后重启 Zotero。");
    }
    const launch = this.launch(record);
    await this.run(launch.command, ["-I", "-c", "from zotero_codex_bridge.pdf_engine import check_runtime; from zotero_codex_bridge.resources import prepare; import sys; check_runtime(sys.argv[1]); prepare(sys.argv[1],verify_only=True)", record.resourceDir], launch.environment || {}, "checking");
    this.update({ stage: "ready", detail: "Linux x64 · 专用 Python 环境已验证" });
    return launch;
  }

  private async prepare(repair: boolean): Promise<LaunchConfig> {
    if (Services.appinfo.OS === "Linux" && Services.appinfo.XPCOMABI.startsWith("x86_64")) return this.prepareLinux();
    const cpu = Services.appinfo.XPCOMABI;
    if (Services.appinfo.OS !== "WINNT" || !cpu.startsWith("x86_64")) throw new Error("自动安装目前仅开放 Windows x64；其他平台可在高级设置使用外部桥接程序。");
    const manifest = await this.manifest();
    await this.io.makeDirectory(this.root, { ignoreExisting: true, createAncestors: true });
    const pointer = this.paths.join(this.root, "current.json");
    let current: any;
    if (await this.io.exists(pointer)) {
      try { current = await this.io.readJSON(pointer); } catch (error) { Zotero.logError(error as Error); }
    }
    const migrated = current && current.runtimeRoot !== this.root;
    if (!repair && current) {
      // Absolute paths encode the data-directory location. Migrated venvs must
      // be rebuilt; never execute a pointer outside the current managed root.
      if (current.runtimeRoot === this.root && current.environmentVersion === manifest.environmentVersion && current.python.startsWith(this.root + "\\") && await this.io.exists(current.python)) {
        const config = this.launch(current);
        try {
          await this.run(config.command, ["-I", "-c", "from zotero_codex_bridge.pdf_engine import check_runtime; from zotero_codex_bridge.resources import prepare; import sys; check_runtime(sys.argv[1]); prepare(sys.argv[1],verify_only=True)", current.resourceDir], config.environment || {}, "checking");
          this.update({ stage: "ready" }); return config;
        } catch (error) { this.checkCancelled(); Zotero.logError(error as Error); repair = true; }
      }
    }
    const bundle = this.paths.join(this.root, "bundles", manifest.environmentVersion);
    await this.io.makeDirectory(bundle, { ignoreExisting: true, createAncestors: true });
    // Bundle files are immutable and individually checked before execution.
    for (const [name, digest] of Object.entries(manifest.bundleHashes)) {
      if (!/^[\w.-]+$/.test(name)) throw new Error("安装清单路径无效。");
      const path = this.paths.join(bundle, name);
      await Zotero.File.download(this.bundleURL + name, path);
      if (await this.digest(path) !== digest) throw new Error(`插件安装文件摘要不符：${name}`);
    }
    await this.io.writeJSON(this.paths.join(bundle, "manifest.json"), manifest);
    const archive = this.paths.join(this.root, "uv-0.13.4.zip");
    if (!(await this.io.exists(archive)) || await this.digest(archive) !== manifest.uv.sha256) {
      for (let attempt = 0; attempt < 3; attempt++) {
        this.checkCancelled();
        this.update({ stage: "uv", detail: "github.com · uv 0.13.4" });
        try {
          const response = await Zotero.HTTP.request("GET", manifest.uv.url, { responseType: "arraybuffer", timeout: 180000, errorDelayMax: 0, anon: true,
            cancellerReceiver: (cancel: () => void) => { this.abortDownload = cancel; },
            requestObserver: (xhr: any) => { xhr.onprogress = (event: any) => this.update({ stage: "uv", completed: event.loaded, total: event.lengthComputable ? event.total : undefined }); } } as any);
          await this.io.write(archive, new Uint8Array(response.response));
          if (await this.digest(archive) !== manifest.uv.sha256) throw new Error("uv 下载摘要不符。");
          break;
        } catch (error) { if (attempt === 2 || this.cancelled) throw new Error(`github.com · uv 下载失败：${error}`); }
        finally { this.abortDownload = undefined; }
      }
    }
    this.checkCancelled();
    const uvDir = this.paths.join(this.root, "tools", manifest.uv.sha256);
    const uv = this.paths.join(uvDir, "uv.exe");
    await this.io.makeDirectory(uvDir, { ignoreExisting: true, createAncestors: true });
    if (!(await this.io.exists(uv)) || await this.digest(uv) !== "b9d9656370ee1812e53e985d8bbb81870038106df98ef34cb45bcf4e0fe0df17") {
    if (await this.io.exists(uv)) { await this.io.setPermissions(uv, 0o600); await this.io.remove(uv); }
    const zip = (Components as any).classes["@mozilla.org/libjar/zip-reader;1"].createInstance((Components as any).interfaces.nsIZipReader);
    zip.open(Zotero.File.pathToFile(archive));
    try {
      const entries = zip.findEntries(null);
      while (entries.hasMore()) {
        const name = entries.getNext();
        if (name.startsWith("/") || name.includes("\\") || name.split("/").includes("..") || name.includes(":")) throw new Error("uv ZIP 包含不安全路径。");
        const entry = zip.getEntry(name);
        if ((entry.permissions & 0xf000) === 0xa000) throw new Error("uv ZIP 不允许符号链接。");
      }
      // Extract only the expected executable, never arbitrary archive entries.
      zip.test("uv.exe"); zip.extract("uv.exe", Zotero.File.pathToFile(uv));
    } finally { zip.close(); }
    if (await this.digest(uv) !== "b9d9656370ee1812e53e985d8bbb81870038106df98ef34cb45bcf4e0fe0df17") throw new Error("uv 可执行文件摘要不符。");
    }
    const environment: Record<string, string | null> = { UV_NO_CONFIG: "1", UV_CACHE_DIR: this.paths.join(this.root, "cache"), UV_PYTHON_INSTALL_DIR: this.paths.join(this.root, "python"), UV_PYTHON_INSTALL_BIN: "0", UV_PYTHON_INSTALL_REGISTRY: "0", UV_PYTHON_DOWNLOADS_JSON_URL: this.paths.join(bundle, "python-downloads.json"), UV_INDEX_URL: "https://pypi.org/simple", UV_SYSTEM_CERTS: "true", UV_HTTP_RETRIES: "2", PYTHONHOME: null, PYTHONPATH: null, VIRTUAL_ENV: null };
    for (let attempt = 0; attempt < 3; attempt++) {
      try { await this.run(uv, ["--no-config", "python", "install", "3.12.15", "--no-bin", "--no-registry", ...(migrated ? ["--reinstall"] : [])], environment, "python"); break; }
      catch (error) { if (attempt === 2 || this.cancelled) throw error; }
    }
    const python = this.paths.join(this.root, "python", "cpython-3.12.15-windows-x86_64-none", "python.exe");
    const result = await this.run(python, ["-I", this.paths.join(bundle, "install.py"), "--root", this.root, "--bundle", bundle, "--uv", uv, ...(repair ? ["--repair"] : [])], environment, "dependencies", true);
    if (!result?.python) throw new Error("环境检查未返回有效启动配置。");
    return this.launch(result);
  }
}

export const runtime = new RuntimeManager();
