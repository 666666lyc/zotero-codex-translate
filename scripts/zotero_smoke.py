"""Isolated Zotero integration smoke test. Never touches the user's profile/library.

Requires a built XPI and bridge. Produces tmp/zotero-smoke-*/report.json.
The injected harness exists only in a disposable test extension, never the XPI.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time
import uuid
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("--zotero", type=pathlib.Path, required=True)
parser.add_argument("--bridge", type=pathlib.Path)
parser.add_argument("--automatic", action="store_true")
parser.add_argument("--reuse-root", type=pathlib.Path)
parser.add_argument("--data-dir", type=pathlib.Path, help="Reuse only an isolated workspace tmp test library")
parser.add_argument("--packed", action="store_true", help="Load the diagnostic test addon as a compressed XPI")
parser.add_argument("--source-pdf", type=pathlib.Path, help="Use an existing synthetic source instead of generating the fixture")
parser.add_argument("--translated-pdf", type=pathlib.Path, help="Import an actual bridge result instead of a copied source PDF")
parser.add_argument("--timeout", type=int, default=600)
args = parser.parse_args()
if not args.bridge and not args.automatic:
    parser.error("--bridge or --automatic required")
root = pathlib.Path(__file__).resolve().parent.parent
test_root = args.reuse_root.resolve() if args.reuse_root else root / "tmp" / ("zotero-smoke-" + uuid.uuid4().hex[:8])
test_root.resolve().relative_to((root / "tmp").resolve())
for old_report in (test_root / "report.json", test_root / "progress.json"):
    old_report.unlink(missing_ok=True)
profile = test_root / ("profile-" + uuid.uuid4().hex[:8])
profile.mkdir(parents=True, exist_ok=True)
data = args.data_dir.resolve() if args.data_dir else test_root / "data"
data.resolve().relative_to((root / "tmp").resolve())
extension = test_root / "addon"
shutil.copytree(root / ".scaffold" / "build", extension, dirs_exist_ok=True)
# Expose internals only in the disposable extension for failure diagnostics.
test_bundle = extension / "content" / "scripts" / "zotero-codex-translate.js"
test_bundle.write_text(test_bundle.read_text(encoding="utf-8").replace(
    "openSettings: () => TranslationFactory.open(false)",
    "runtimeTest: runtime, splitTest: SplitViewFactory, openSettings: () => TranslationFactory.open(false)"), encoding="utf-8")
data.mkdir(parents=True, exist_ok=True)
# The debugger actor installs exactly once. A profile extension pointer plus
# installTemporaryAddon can start two harnesses after a profile is reused.

import pymupdf
fixture = test_root / "中文路径" / "英文文献.pdf"
fixture.parent.mkdir(parents=True, exist_ok=True)
if args.source_pdf:
    shutil.copyfile(args.source_pdf, fixture)
else:
    pdf = pymupdf.open()
    for i in range(2):
        page = pdf.new_page()
        page.insert_textbox((50, 50, 500, 300), f"Page {i+1}. This scientific paper contains a sufficiently long English paragraph to exercise PDF attachment handling and reading.")
    pdf.save(fixture)
    pdf.close()
options = {"separator": os.sep, "root": str(test_root), "data": str(data), "bridge": str(args.bridge.resolve()) if args.bridge else "", "automatic": args.automatic, "fixture": str(fixture), "fingerprint": hashlib.sha256(fixture.read_bytes()).hexdigest(), "translated": str(args.translated_pdf.resolve()) if args.translated_pdf else str(fixture), "realTranslation": bool(args.translated_pdf)}
harness = r'''
(async () => {
  if (Zotero.__codexSmokeStarted) return;
  Zotero.__codexSmokeStarted = true;
  const report = { checks: [], passed: false };
  const join = (...parts) => parts.join(OPTS.separator);
  const delay = (ms) => new Promise(resolve => Zotero.getMainWindow().setTimeout(resolve, ms));
  const record = async (name) => { report.checks.push(name); await Zotero.File.putContentsAsync(join(OPTS.root, 'progress.json'), JSON.stringify(report)); };
  try {
    await record('plugin-started');
    if (!addon.data.initialized) throw new Error('Plugin did not initialize');
    Zotero.Prefs.set('extensions.zotero.zotero-codex-translate.bridgePath', OPTS.bridge, true);
    let dialog = await addon.api.openSettings();
    for (let attempt=0; attempt<100 && (!dialog || dialog.document.readyState !== 'complete'); attempt++) {
      for (const win of Services.wm.getEnumerator(null)) {
        if (win.location?.href?.endsWith('/content/translation.xhtml') && win.document.readyState === 'complete') dialog = win;
      }
      if (!dialog) await delay(100);
    }
    if (!dialog) throw new Error('Translation dialog did not open');
    await delay(500);
    if (!dialog.document.getElementById('start')) throw new Error('Dialog controls missing');
    await record('settings-dialog-rendered');
    const panel = dialog.arguments[0];
    await panel.licenses();
    let licenseWindow;
    for (let attempt=0; attempt<100; attempt++) {
      licenseWindow = [...Services.wm.getEnumerator(null)].find(win => win.location?.href?.endsWith('/content/licenses.xhtml'));
      if (licenseWindow?.document.getElementById('license')?.textContent?.includes('GNU AFFERO GENERAL PUBLIC LICENSE')) break;
      await delay(100);
    }
    if (!licenseWindow?.document.getElementById('notices')?.textContent?.includes('Split-View Reader')) {
      report.licenseDebug = {url:licenseWindow?.location?.href, body:licenseWindow?.document.documentElement?.textContent?.slice(0,1200), windows:[...Services.wm.getEnumerator(null)].map(win=>win.location?.href)};
      throw new Error('License dialog did not load original notices');
    }
    licenseWindow.close();
    await record('licenses-and-corresponding-source-dialog');
    if (OPTS.automatic) {
      await record('automatic-bootstrap-started');
      const unsubscribe = state => { void Zotero.File.putContentsAsync(join(OPTS.root, 'environment-progress.json'), JSON.stringify(state)); };
      addon.api.runtimeTest.listeners.add(unsubscribe);
      try { await addon.api.runtimeTest.ensureReady(); }
      finally { addon.api.runtimeTest.listeners.delete(unsubscribe); }
      await record('automatic-upstream-environment-ready');
    }
    const parent = new Zotero.Item('journalArticle');
    parent.setField('title', 'Codex integration smoke fixture');
    await parent.saveTx();
    const source = await Zotero.Attachments.importFromFile({file:OPTS.fixture,parentItemID:parent.id,title:'English original'});
    await record('source-attachment-created');
    panel.source = source;
    const id = '12345678-1234-1234-1234-123456789abc';
    const dir = join(OPTS.data, 'codex-translate', 'jobs', id);
    const output = join(dir, 'translated.zh-CN.pdf');
    await IOUtils.makeDirectory(dir, {createAncestors:true});
    await IOUtils.copy(OPTS.translated, output);
    await Zotero.File.putContentsAsync(join(dir, 'job.json'), JSON.stringify({id,createdAt:1,state:'completed',sourceItemKey:source.key,parentItemKey:parent.key,libraryID:source.libraryID,fingerprint:OPTS.fingerprint,model:'test-fixture',result:{outputPath:output,pageCount:2,pageMap:[1,2]}}));
    await panel.refresh();
    await record('bridge-protocol-and-result-imported');
    if (OPTS.realTranslation) await record('real-codex-output-imported');
    if (parent.getAttachments().length !== 2) throw new Error('Expected exactly one translated attachment');
    await panel.refresh();
    await panel.open('translated');
    if (parent.getAttachments().length !== 2) throw new Error('Duplicate attachment after retry');
    await record('duplicate-import-prevented');
    await panel.open('compare');
    await delay(2000);
    const tabs = Zotero.getMainWindow().Zotero_Tabs;
    const tab = tabs._tabs.find(t => t.data?.isCodexSplitView && t.data.leftItemID === source.id);
    if (!tab || tab.data.leftItemID !== source.id || !tab.data.rightItemID) throw new Error('Paired split tab missing');
    await record('paired-pdf-readers-opened');
    const browsers = Zotero.getMainWindow().document.getElementById(tab.id).querySelectorAll('browser');
    const readerFor = browser => (browser.contentWindow.wrappedJSObject || browser.contentWindow)._reader;
    const left = readerFor(browsers[0]);
    const right = readerFor(browsers[1]);
    await left.navigate(Components.utils.cloneInto({pageIndex:1}, browsers[0].contentWindow));
    await delay(1500);
    const rightPage = right._primaryView._iframeWindow.PDFViewerApplication.pdfViewer.currentPageNumber;
    const split = addon.api.splitTest;
    const state = split.stateMap.get(tab.id);
    report.syncDebug = {leftPage:left._primaryView._iframeWindow.PDFViewerApplication.pdfViewer.currentPageNumber, rightPage,
      primary:state?.primarySide, paused:state?.syncPaused, samePDF:state?.isSamePDF,
      leftState:state?.leftViewState,rightState:state?.rightViewState,
      immediateLeft:split.getImmediateViewStateFromBrowser(state.leftBrowser), immediateRight:split.getImmediateViewStateFromBrowser(state.rightBrowser)};
    if (rightPage !== 2) throw new Error('Page synchronization did not follow original: ' + rightPage);
    await record('page-synchronization-followed');
    split.setPrimarySide(tab.id, null);
    await right.navigate(Components.utils.cloneInto({pageIndex:0}, browsers[1].contentWindow));
    await delay(1000);
    if (left._primaryView._iframeWindow.PDFViewerApplication.pdfViewer.currentPageNumber !== 2 ||
        right._primaryView._iframeWindow.PDFViewerApplication.pdfViewer.currentPageNumber !== 1) throw new Error('Independent page browsing failed');
    await record('independent-browsing');
    for (const reader of [left, right]) {
      const browser = reader === left ? browsers[0] : browsers[1];
      reader._annotationManager.addAnnotation(Components.utils.cloneInto({type:'highlight',color:'#ffd400',text:'Smoke annotation',comment:'',pageLabel:'1',sortIndex:'00000|000000|00000',position:{pageIndex:0,rects:[[50,50,100,65]]}}, browser.contentWindow));
    }
    await delay(2000);
    const target = Zotero.Items.get(tab.data.rightItemID);
    if (source.getAnnotations().length !== 1 || target.getAnnotations().length !== 1) throw new Error('Annotations were not saved to separate attachments');
    await record('annotations-saved-per-attachment');
    await split.revertToSingleReader(tab.id, 'right');
    await delay(1000);
    if (split.stateMap.has(tab.id) || Zotero.Reader.getByTabID(tabs.selectedID)?.itemID !== source.id) throw new Error('Closing split view did not restore a normal reader');
    await record('normal-reader-restored-after-split-close');
    dialog.close();
    await addon.hooks.onShutdown();
    await record('plugin-shutdown-cleanup');
    report.passed = true;
  } catch (error) { report.error = String(error); report.stack = error.stack; }
  await Zotero.File.putContentsAsync(join(OPTS.root, 'report.json'), JSON.stringify(report,null,2));
  Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);
})();
'''
(extension / "content" / "smoke.js").write_text("const OPTS = " + json.dumps(options) + ";\n" + harness, encoding="utf-8")
bootstrap = extension / "bootstrap.js"
code = bootstrap.read_text(encoding="utf-8")
needle = "await Zotero.ZoteroCodexTranslate.hooks.onStartup();"
assert needle in code
code = code.replace(needle, needle + '\n  Services.scriptloader.loadSubScript(rootURI + "content/smoke.js", {Zotero, Services, ChromeUtils, IOUtils, Components, addon: Zotero.ZoteroCodexTranslate});')
bootstrap.write_text(code, encoding="utf-8")
addon_path = extension
if args.packed:
    addon_path = test_root / "diagnostic.xpi"
    with zipfile.ZipFile(addon_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in extension.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(extension).as_posix())
prefs = {"extensions.autoDisableScopes": 0, "extensions.experiments.enabled": True, "extensions.zoteroWinWordIntegration.skipInstallation": True, "extensions.zoteroMacWordIntegration.skipInstallation": True, "extensions.zoteroOpenOfficeIntegration.skipInstallation": True, "layers.acceleration.disabled": True, "gfx.webrender.force-disabled": True, "extensions.enabledScopes": 15, "extensions.zotero.dataDir": str(data), "extensions.zotero.useDataDir": True,
         "extensions.zotero.firstRun": False, "extensions.zotero.firstRun2": False, "extensions.zotero.sync.autoSync": False,
         "extensions.zotero.automaticScraperUpdates": False, "app.update.auto": False, "extensions.update.enabled": False}
(profile / "user.js").write_text("\n".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k,v in prefs.items()), encoding="utf-8")
with socket.socket() as reservation:
    reservation.bind(("127.0.0.1", 0))
    debug_port = reservation.getsockname()[1]
debug_prefs = {"devtools.debugger.remote-enabled": True, "devtools.debugger.remote-websocket": False,
               "devtools.debugger.prompt-connection": False, "extensions.logging.enabled": True}
with (profile / "user.js").open("a", encoding="utf-8") as f:
    f.write("\n" + "\n".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k,v in debug_prefs.items()))


def install_temporary_addon(port, addon_path, process):
    connection = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and process.poll() is None:
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            time.sleep(0.5)
    if connection is None:
        raise RuntimeError("Isolated Zotero debugger did not start")
    connection.settimeout(180)
    def receive():
        size = b""
        while not size.endswith(b":"):
            chunk = connection.recv(1)
            if not chunk: raise RuntimeError("Debugger closed the connection")
            size += chunk
        length = int(size[:-1])
        body = b""
        while len(body) < length:
            body += connection.recv(length-len(body))
        return json.loads(body)
    def request(to, kind, **params):
        body = json.dumps({"to":to,"type":kind,**params}).encode()
        connection.sendall(str(len(body)).encode() + b":" + body)
        while True:
            reply = receive()
            if reply.get("from") == to:
                if reply.get("error"): raise RuntimeError(str(reply))
                return reply
    try:
        receive()  # Root handshake
        actors = request("root", "getRoot")
        result = request(actors["addonsActor"], "installTemporaryAddon", addonPath=str(addon_path.resolve()))
        print("Temporary addon installed:", result.get("addon", {}).get("id"), flush=True)
    finally:
        connection.close()
startup = None
if os.name == "nt":
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
with (test_root / "zotero.log").open("w", encoding="utf-8") as log:
    env = os.environ.copy()
    env["MOZ_HEADLESS"] = "1"
    process = subprocess.Popen([str(args.zotero), "-no-remote", "-profile", str(profile), "--purgecaches", "--start-debugger-server", str(debug_port), "-ZoteroDebugText"], stdout=log, stderr=log, startupinfo=startup, env=env)
    try:
        install_temporary_addon(debug_port, addon_path, process)
        deadline = time.monotonic() + args.timeout
        report_path = test_root / "report.json"
        while time.monotonic() < deadline and not report_path.exists() and process.poll() is None:
            time.sleep(0.5)
        if report_path.exists():
            result = json.loads(report_path.read_text(encoding="utf-8"))
            print(json.dumps(result, ensure_ascii=True, indent=2))
            print(report_path)
            raise SystemExit(0 if result["passed"] else 1)
        print("Smoke test did not finish. See", test_root / "zotero.log")
        raise SystemExit(1)
    finally:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Only this isolated test process and its descendants are stopped.
            import psutil
            parent_process = psutil.Process(process.pid)
            for child in parent_process.children(recursive=True):
                child.kill()
            parent_process.kill()
