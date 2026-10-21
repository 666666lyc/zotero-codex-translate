/** Syncable attachment-note metadata. Never put plugin URNs in dc:relation. */
export interface PairMetadata {
  version: 1;
  originalKey: string;
  fingerprint?: string;
  jobId?: string;
  sourcePageCount?: number;
  pageCount?: number;
}
const TOKEN = "ZOTERO_CODEX_TRANSLATION_V1:";
const PATTERN = /ZOTERO_CODEX_TRANSLATION_V1:([A-Za-z0-9%_.!~*'()-]+)/g;
const LEGACY = "urn:zotero-codex:translation:";
const COUNTS = "urn:zotero-codex:page-counts:";
export function readMetadata(item: Zotero.Item): PairMetadata | undefined {
  const note = item.getNote?.() || "";
  if (!note.includes(TOKEN)) return;
  const matches = [...note.matchAll(PATTERN)];
  if (matches.length !== 1 || note.split(TOKEN).length !== 2) throw new Error("附件中的 Codex 对照关联损坏或重复，请先检查附件笔记。");
  let data: PairMetadata;
  try { data = JSON.parse(decodeURIComponent(matches[0][1])); }
  catch { throw new Error("附件中的 Codex 对照关联无法解析，请先检查附件笔记。"); }
  if (data.version !== 1 || !/^[A-Z0-9]{8}$/.test(data.originalKey) ||
      [data.fingerprint, data.jobId].some(v => v !== undefined && (typeof v !== "string" || v.length > 256)) ||
      [data.sourcePageCount, data.pageCount].some(v => v !== undefined && (!Number.isSafeInteger(v) || v < 1))) {
    throw new Error("附件中的 Codex 对照关联格式无效。");
  }
  return data;
}
function legacyValues(item: Zotero.Item): string[] {
  const values = item.getRelations?.()["dc:relation"] || [];
  return Array.isArray(values) ? values : [values];
}
export function metadata(item: Zotero.Item): PairMetadata | undefined {
  const current = readMetadata(item);
  if (current) return current;
  const values = legacyValues(item);
  const records = values.filter(v => v.startsWith(LEGACY));
  if (records.length > 1) throw new Error("存在多个旧版译文关联，请先检查附件。");
  if (!records.length) return;
  const [originalKey, fingerprint, jobId] = records[0].slice(LEGACY.length).split(":");
  if (!/^[A-Z0-9]{8}$/.test(originalKey)) throw new Error("旧版译文关联无效。");
  const counts = values.find(v => v.startsWith(COUNTS))?.slice(COUNTS.length).split(":").map(Number);
  return {version: 1, originalKey, fingerprint, jobId,
    ...(counts?.length === 2 && counts.every(n => Number.isSafeInteger(n) && n > 0) ? {sourcePageCount:counts[0],pageCount:counts[1]} : {})};
}
export async function saveMetadata(item: Zotero.Item, data: PairMetadata, inTransaction = false) {
  const previous = readMetadata(item); // Refuse to overwrite malformed user-edited metadata.
  if (previous && previous.originalKey !== data.originalKey) throw new Error("此附件已有其他原文关联，未覆盖。");
  const token = TOKEN + encodeURIComponent(JSON.stringify(data));
  const note = item.getNote?.() || "";
  item.setNote(previous ? note.replace(PATTERN, () => token) : note + '\n<p>Codex 对照关联（插件管理）：' + token + '</p>');
  if (inTransaction) await item.save(); else await item.saveTx();
}
export function candidates(item: Zotero.Item): Zotero.Item[] {
  if (!item || item.deleted || !item.parentItemID || item.attachmentContentType !== "application/pdf") return [];
  const parent = Zotero.Items.get(item.parentItemID);
  if (!parent || parent.deleted) return [];
  return parent.getAttachments().map(id => Zotero.Items.get(id)).filter(a => a && !a.deleted && a.id !== item.id &&
    a.libraryID === item.libraryID && a.parentItemID === item.parentItemID && a.attachmentContentType === "application/pdf");
}
export function savedPair(item: Zotero.Item): {original: Zotero.Item; translated: Zotero.Item} | undefined {
  if (!item || item.deleted || !item.parentItemID || item.attachmentContentType !== "application/pdf") return;
  const own = metadata(item);
  if (own) {
    const original = Zotero.Items.getByLibraryAndKey(item.libraryID, own.originalKey);
    if (original && candidates(item).some(a => a.id === original.id)) return {original,translated:item};
    return; // Source may not have arrived via sync yet. Keep the note intact.
  }
  const translated = candidates(item).filter(a => metadata(a)?.originalKey === item.key)
    .sort((a,b) => String(b.dateAdded || "").localeCompare(String(a.dateAdded || "")) || b.id-a.id)[0];
  if (translated) return {original:item,translated};
}
export async function choosePair(item: Zotero.Item) {
  const existing = savedPair(item);
  if (existing) return existing;
  if (metadata(item)) throw new Error("关联原文尚未同步到达、已被删除或已移到其他文献下。");
  const choices = candidates(item).filter(a => !metadata(a));
  if (!choices.length) throw new Error("同一文献下没有可对照的 PDF。");
  let index = 0;
  if (choices.length > 1) {
    const selected = {value:0};
    if (!Services.prompt.select(Zotero.getMainWindow() as any, "选择对照 PDF", "请选择与当前 PDF 对照的附件（当前 PDF 放左侧）。",
      choices.map(a => String(a.getField("title")) + " · " + a.key), selected)) return;
    index = selected.value;
  }
  const translated = choices[index];
  if (!translated) return;
  return {original:item,translated};
}

/** Notes and relations are lazy-loaded by Zotero, including after sync/restart. */
export async function loadPairingData(): Promise<Zotero.Item[]> {
  const ids: number[] = await Zotero.DB.columnQueryAsync("SELECT itemID FROM itemAttachments WHERE contentType='application/pdf'");
  const items = await Zotero.Items.getAsync(ids);
  for (const item of items) {
    await (item as any).loadDataType("note");
    await (item as any).loadDataType("relations");
    await (item as any).loadDataType("itemData");
  }
  const parentIDs = [...new Set(items.map(item=>item.parentItemID).filter(Boolean))] as number[];
  const parents = await Zotero.Items.getAsync(parentIDs);
  for (const parent of parents) await (parent as any).loadDataType("childItems");
  return items;
}

/** Called before registering UI. A note/relations change is one atomic save. */
export async function initializePairing(): Promise<void> {
  const items = await loadPairingData();
  const pending = items.filter(item => legacyValues(item).some(v => v.startsWith(LEGACY) || v.startsWith(COUNTS)) && (Zotero.Libraries.get(item.libraryID) as any).editable);
  if (!pending.length) return;
  const paths = (globalThis as any).PathUtils || (ztoolkit as any).getGlobal("PathUtils");
  const io = (globalThis as any).IOUtils || (ztoolkit as any).getGlobal("IOUtils");
  const directory = paths.join(Zotero.DataDirectory.dir,"codex-translate","migration-backups");
  await io.makeDirectory(directory,{createAncestors:true,ignoreExisting:true});
  const stamp = String(Date.now());
  await io.writeJSON(paths.join(directory,stamp+"-before.json"),pending.map(item=>({libraryID:item.libraryID,key:item.key,note:item.getNote(),relations:item.getRelations()})));
  const results: any[] = [];
  for (const item of pending) {
    try {
      const old = metadata(item);
      const legacy = legacyValues(item).filter(v=>v.startsWith(LEGACY));
      if (readMetadata(item) && legacy.some(v=>v.slice(LEGACY.length).split(":")[0] !== old?.originalKey)) throw new Error("新旧原文关联冲突，未迁移");
      await Zotero.DB.executeTransaction(async()=>{
        if (old) await saveMetadata(item,old,true);
        for (const value of legacyValues(item)) if (value.startsWith(LEGACY)||value.startsWith(COUNTS)) item.removeRelation("dc:relation",value);
        await item.save();
      });
      results.push({key:item.key,migrated:true});
    } catch(error) {
      await (item as any).reload(["note","relations"],true);
      Zotero.logError(error as Error);results.push({key:item.key,error:String(error)});
    }
  }
  await io.writeJSON(paths.join(directory,stamp+"-result.json"),results);
}
