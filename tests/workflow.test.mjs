import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { build } from "esbuild";

const bundle = await build({ stdin: { contents: 'export {TranslationFactory} from "./src/modules/translation"; export {bridge} from "./src/modules/bridge"; export {SplitViewFactory} from "./src/modules/splitView";', resolveDir: process.cwd(), loader: "ts" }, bundle: true, write: false, format: "cjs", target: "es2022" });

function fixture({ failSave = false, failAck = false } = {}) {
  let panel, imports = 0, acknowledgements = 0;
  const items = new Map();
  const attachmentIDs = [];
  const parent = { id: 1, libraryID: 1, key: "PARENT01", getAttachments: () => attachmentIDs, deleted: false };
  const source = { id: 2, parentItemID: 1, libraryID: 1, key: "SOURCE01", deleted: false };
  items.set(1, parent); items.set(2, source);
  const Zotero = {
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
  return { api, job, get imports() { return imports; }, get acknowledgements() { return acknowledgements; }, async panel() {
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

test("different documents follow page numbers and allow independent browsing", () => {
  const { api } = fixture();
  const reader = api.SplitViewFactory;
  const moves = [];
  const left = {contentWindow: {}}, right = {contentWindow: {}};
  const state = { primarySide: "left", leftBrowser: left, rightBrowser: right, lastPrimaryScroll: { top: 0, left: 0 }, isSamePDF: false };
  reader.stateMap = new Map([["tab", state]]);
  reader.getImmediateViewStateFromBrowser = (side) => ({ pageIndex: side === left ? 3 : 1 });
  reader.getInternalReaderFromBrowser = () => ({ navigate: (location) => moves.push(location.pageIndex) });
  reader.syncViews("tab");
  assert.deepEqual(moves, [3]);
  state.lastPrimaryScroll = null;
  reader.handleViewStateChange(left, {pageIndex: 2});
  assert.deepEqual(moves, [3, 2]);
  state.primarySide = null;
  reader.syncViews("tab");
  reader.handleViewStateChange(left, {pageIndex: 0});
  assert.deepEqual(moves, [3, 2]);
});
