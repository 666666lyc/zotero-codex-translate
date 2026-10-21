import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { build } from "esbuild";

const bundle = await build({ stdin: { contents: 'export {TranslationFactory} from "./src/modules/translation"; export {bridge} from "./src/modules/bridge"; export {runtime} from "./src/modules/runtime"; export {SplitViewFactory} from "./src/modules/splitView";', resolveDir: process.cwd(), loader: "ts" }, bundle: true, write: false, format: "cjs", target: "es2022" });

function fixture({ failSave = false, failAck = false } = {}) {
  let panel, imports = 0, acknowledgements = 0;
  const items = new Map();
  const attachmentIDs = [];
  const parent = { id: 1, libraryID: 1, key: "PARENT01", getAttachments: () => attachmentIDs, deleted: false };
  const source = { id: 2, parentItemID: 1, libraryID: 1, key: "SOURCE01", deleted: false };
  items.set(1, parent); items.set(2, source);
  const Zotero = {
    Prefs: { get: () => "" },
    Items: { get: (id) => items.get(id) },
    Libraries: { get: () => ({ editable: true }) },
    getMainWindow: () => ({ openDialog: (...args) => { panel = args.at(-1); } }),
    Reader: { open: async () => ({}) },
    Attachments: { importFromFile: async (options) => {
      imports++;
      const relations = {};
      const attachment = { id: 10, key: "OUTPUT01", attachmentFilename: options.fileBaseName + ".pdf", getRelations: () => relations,
        addRelation: (predicate, value) => { relations[predicate] = [value]; },
        saveTx: async () => { if (failSave) { failSave = false; throw new Error("simulated crash before relation commit"); } },
        getFilePathAsync: async () => "translated.pdf" };
      attachmentIDs.push(10); items.set(10, attachment);
      return attachment;
    } },
    DB: { executeTransaction: () => { throw new Error("Nested transaction is forbidden by real Zotero"); } },
  };
  const context = { module: { exports: {} }, exports: {}, Zotero, Components: {utils: {cloneInto: value => value}}, rootURI: "chrome://test/", setTimeout, clearTimeout, console };
  context.exports = context.module.exports;
  vm.runInNewContext(bundle.outputFiles[0].text, context);
  const api = context.module.exports;
  api.bridge.request = async () => {
    acknowledgements++;
    if (failAck) { failAck = false; throw new Error("simulated lost acknowledgement"); }
    return {};
  };
  const job = { id: "abcd", createdAt: 1, state: "completed", fingerprint: "fingerprint", result: { outputPath: "translated.pdf" } };
  return { api, job, Zotero, get dialogData() { return panel; }, get imports() { return imports; }, get acknowledgements() { return acknowledgements; }, async panel() {
    await api.TranslationFactory.open(false);
    panel.source = source; panel.selectedJob = job;
    return panel;
  } };
}

test("concurrent result opens share one import and avoid nested Zotero transactions", async () => {
  const f = fixture();
  const panel = await f.panel();
  await Promise.all([panel.open("translated"), panel.open("translated")]);
  assert.equal(f.imports, 1);
  assert.equal(f.acknowledgements, 1);
});

test("different page counts allow standalone import but block comparison", async () => {
  const f = fixture();
  f.job.result = {outputPath:"translated.pdf",sourcePageCount:2,pageCount:1,comparisonAvailable:false};
  const panel = await f.panel();
  await assert.rejects(panel.open("compare"), /页数不同/);
  assert.equal(f.imports, 0);
  await panel.open("translated");
  assert.equal(f.imports, 1);
  await assert.rejects(f.api.SplitViewFactory.openTranslationPair(2,10), /页数不同/);
});

test("background submission coalesces duplicate starts and creates no dialog", async () => {
  const f = fixture();
  const source = {id:2,key:"SOURCE01",libraryID:1,parentItemID:1,attachmentContentType:"application/pdf",isRegularItem:()=>false,isAttachment:()=>true,getRelations:()=>({}),getFilePathAsync:async()=>"source.pdf"};
  f.Zotero.getActiveZoteroPane = () => ({getSelectedItems:()=>[source]});
  f.Zotero.Reader.getByTabID = () => undefined;
  f.Zotero.getMainWindow = () => ({Zotero_Tabs:{selectedID:"library"},openDialog:()=>assert.fail("Background start must not open a dialog")});
  let starts = 0;
  f.api.bridge.request = async method => {
    if(method==="jobs.list")return [];
    if(method==="document.inspect")return {fingerprint:"same"};
    if(method==="jobs.start"){starts++;await new Promise(resolve=>setTimeout(resolve,10));return {id:"new-job",state:"running",createdAt:1};}
    throw Error(method);
  };
  await Promise.all([f.api.TranslationFactory.startBackground(),f.api.TranslationFactory.startBackground()]);
  assert.equal(starts,1);
});

test("connection settings do not offer orphan result actions without a source", async () => {
  const f = fixture();
  f.api.bridge.request = async () => [{id:"existing",state:"failed",createdAt:1}];
  await f.api.TranslationFactory.open(false);
  const panel=f.dialogData;
  await panel.refresh();
  assert.equal(panel.jobs.length,0);
  assert.equal(panel.selectedJob,undefined);
});

test("lost acknowledgement recovers the existing attachment", async () => {
  const f = fixture({ failAck: true });
  const panel = await f.panel();
  await assert.rejects(panel.open("translated"), /lost acknowledgement/);
  await panel.open("translated");
  assert.equal(f.imports, 1);
});

test("deterministic filename recovers an import before relation save", async () => {
  const f = fixture({ failSave: true });
  const panel = await f.panel();
  await assert.rejects(panel.open("translated"), /relation commit/);
  await panel.open("translated");
  assert.equal(f.imports, 1);
});

test("split view delegates changes from both PDF readers and respects independent mode", () => {
  const {api}=fixture(),factory=api.SplitViewFactory,moves=[];
  const left={},right={};
  const state={primarySide:"left",activeSide:"left",leftBrowser:left,rightBrowser:right,isSamePDF:false,comparison:{changed:side=>moves.push(side)}};
  factory.stateMap=new Map([["tab",state]]);
  factory.handleViewStateChange(left,{pageIndex:2});
  factory.handleViewStateChange(right,{pageIndex:4});
  assert.deepEqual(moves,["left","right"]);
  state.primarySide=null;
  factory.handleViewStateChange(right,{pageIndex:1});
  assert.deepEqual(moves,["left","right"]);
});

test("repair bypasses a failed bridge and a failed repair can be retried", async () => {
  const f = fixture(), panel = await f.panel(); panel.source = undefined;
  panel.busy = true; // A lost connection can leave stale task state in a window.
  let repaired = 0, closed = 0;
  const messages=[];panel.win={renderCodexPanel:s=>messages.push(s.message)};
  f.api.bridge.status=()=>({phase:'failed'});
  f.api.bridge.close=async()=>{closed++;};
  f.api.runtime.repair=async()=>{if(++repaired===1)throw Error('simulated repair failure');};
  f.api.bridge.request=async()=>{assert.ok(repaired>=2,'Must not query broken bridge before repair');return [];};
  await assert.rejects(panel.prepareEnvironment(true),/simulated repair failure/);
  await panel.prepareEnvironment(true);
  assert.equal(repaired,2);assert.equal(closed,2);assert.match(messages.at(-1),/修复完成/);
});

test("active jobs block repair without closing their bridge", async () => {
  const f=fixture(),panel=await f.panel();panel.source=undefined;
  f.api.bridge.status=()=>({phase:'connected'});
  f.api.bridge.request=async()=>[{id:'active',state:'running'}];
  f.api.bridge.close=async()=>{assert.fail('Must not close active bridge');};
  f.api.runtime.repair=async()=>{assert.fail('Must not repair active environment');};
  await assert.rejects(panel.prepareEnvironment(true),/完成或取消/);
});

test("connection actions reject duplicate clicks and unlock after completion", async () => {
  const f=fixture(),panel=await f.panel();panel.source=undefined;
  let finish;const pending=new Promise(resolve=>{finish=resolve;});
  f.api.bridge.status=()=>({phase:'failed'});f.api.bridge.close=async()=>{};
  f.api.runtime.repair=()=>pending;f.api.bridge.request=async()=>[];
  const first=panel.prepareEnvironment(true);
  await assert.rejects(panel.diagnose(),/操作正在进行/);
  finish();await first;
  await panel.reconnect();
});

test("cancel keeps actions locked until the old connection is fully closed", async () => {
  const f=fixture(),panel=await f.panel();panel.source=undefined;
  let rejectDiagnosis,finishClose;
  f.api.bridge.request=()=>new Promise((_resolve,reject)=>{rejectDiagnosis=reject;});
  f.api.bridge.close=()=>{rejectDiagnosis(Error('closed'));return new Promise(resolve=>{finishClose=resolve;});};
  const diagnosis=panel.diagnose();const rejected=assert.rejects(diagnosis,/已取消/);
  const cancellation=panel.cancelConnection();await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(panel.reconnect(),/操作正在进行/);
  finishClose();await Promise.all([rejected,cancellation]);
});

test("license URI responses are passed as text rather than XMLHttpRequest objects", async () => {
  const f=fixture(),panel=await f.panel();
  f.Zotero.HTTP={request:async(_method,url)=>({responseText:url.endsWith('/LICENSE')?'GNU AFFERO GENERAL PUBLIC LICENSE':'Split-View Reader original notices'})};
  await panel.licenses();
  assert.equal(f.dialogData.license,'GNU AFFERO GENERAL PUBLIC LICENSE');
  assert.equal(f.dialogData.notices,'Split-View Reader original notices');
});
