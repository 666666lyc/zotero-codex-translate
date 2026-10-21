import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {build} from 'esbuild';
const bundle=await build({entryPoints:['src/modules/pairing.ts'],bundle:true,write:false,format:'cjs'});
function setup({readonly=false,cancel=false,fail=false}={}){
 const map=new Map(),backups=[],errors=[];let prompts=0,transactions=0;
 const parent={id:1,key:'PARENT01',deleted:false,loadDataType:async()=>{},getAttachments:()=>[...map.values()].filter(a=>a.parentItemID===1).map(a=>a.id)};map.set(1,parent);
 function item(id,key,extra={}){
  const a={id,key,libraryID:1,parentItemID:1,attachmentContentType:'application/pdf',deleted:false,note:'<p>User note &amp; annotations stay.</p>',relations:{},
   getNote(){return this.note;},setNote(v){this.note=v;},getRelations(){return this.relations;},getField(){return this.key;},
   removeRelation(p,v){this.relations[p]=this.relations[p].filter(x=>x!==v);},
   async loadDataType(){},async saveTx(){if(fail)throw Error('disk failure');},async save(){if(fail)throw Error('disk failure');},async reload(){},...extra};map.set(id,a);return a;
 }
 const context={module:{exports:{}},exports:{},Services:{prompt:{select(_w,_t,_m,_labels,out){prompts++;out.value=1;return !cancel;}}},
 Zotero:{Items:{get:id=>map.get(id),getByLibraryAndKey:(lib,key)=>[...map.values()].find(a=>a.libraryID===lib&&a.key===key),getAsync:async ids=>ids.map(id=>map.get(id)).filter(Boolean)},
 Libraries:{get:()=>({editable:!readonly})},getMainWindow:()=>({}),DataDirectory:{dir:'/test'},logError:e=>errors.push(String(e)),
 DB:{columnQueryAsync:async()=>[...map.values()].filter(a=>a.attachmentContentType).map(a=>a.id),executeTransaction:async fn=>{transactions++;const old=[...map.values()].map(a=>[a,a.note,JSON.stringify(a.relations)]);try{await fn();}catch(e){for(const [a,n,r]of old){a.note=n;a.relations=r?JSON.parse(r):undefined;}throw e;}}},
 },PathUtils:{join:(...p)=>p.join('/')},IOUtils:{makeDirectory:async()=>{},writeJSON:async(p,r)=>backups.push([p,JSON.parse(JSON.stringify(r))])}};
 context.exports=context.module.exports;vm.runInNewContext(bundle.outputFiles[0].text,context);
 const api=context.module.exports;return {api,item,map,backups,errors,get prompts(){return prompts;},get transactions(){return transactions;}};
}
test('one candidate auto-pairs, excludes snapshot, and survives note JSON transport with new local IDs',async()=>{
 const f=setup(),a=f.item(2,'SOURCE01'),b=f.item(3,'TARGET01');f.item(4,'SNAP0001',{attachmentContentType:'text/html'});
 const pair=await f.api.choosePair(a);assert.equal(pair.translated.id,b.id);assert.equal(f.prompts,0);
 const before=b.note;await f.api.saveMetadata(b,{version:1,originalKey:a.key});assert.ok(b.note.startsWith(before));assert.deepEqual(b.relations,{});
 const g=setup(),newA=g.item(20,'SOURCE01'),newB=g.item(30,'TARGET01',{note:JSON.parse(JSON.stringify({note:b.note})).note});
 assert.equal(g.api.savedPair(newA).translated.id,30);assert.equal(g.api.savedPair(newB).original.id,20);
});
test('multiple candidates require selection; cancel does not save metadata',async()=>{
 const f=setup(),a=f.item(2,'SOURCE01');f.item(3,'TARGET01');f.item(4,'TARGET02');assert.equal((await f.api.choosePair(a)).translated.key,'TARGET02');assert.equal(f.prompts,1);
 const g=setup({cancel:true}),x=g.item(2,'SOURCE01');g.item(3,'TARGET01');g.item(4,'TARGET02');assert.equal(await g.api.choosePair(x),undefined);assert.ok(!x.note.includes('ZOTERO_CODEX'));
});
test('deleted and moved PDFs are excluded; no candidate has an actionable error',async()=>{
 const f=setup(),a=f.item(2,'SOURCE01');f.item(3,'TARGET01',{deleted:true});f.item(4,'TARGET02',{parentItemID:9});
 assert.equal(f.api.candidates(a).length,0);await assert.rejects(f.api.choosePair(a),/没有可对照/);
});
test('valid metadata takes precedence and a missing synced source is preserved',async()=>{
 const f=setup(),a=f.item(2,'SOURCE01'),b=f.item(3,'TARGET01');f.item(4,'TARGET02');await f.api.saveMetadata(b,{version:1,originalKey:a.key});
 assert.equal((await f.api.choosePair(a)).translated.id,3);assert.equal(f.prompts,0);
 a.deleted=true;const old=b.note;await assert.rejects(f.api.choosePair(b),/尚未同步/);assert.equal(b.note,old);
});
test('malformed, duplicate, future-version, and conflicting metadata are never overwritten',async()=>{
 for(const text of ['ZOTERO_CODEX_TRANSLATION_V1:%broken','ZOTERO_CODEX_TRANSLATION_V1:%7B%7D','ZOTERO_CODEX_TRANSLATION_V1:x ZOTERO_CODEX_TRANSLATION_V1:y']){
  const f=setup(),a=f.item(2,'SOURCE01',{note:text});await assert.rejects(f.api.saveMetadata(a,{version:1,originalKey:'TARGET01'}));assert.equal(a.note,text);
 }
 const f=setup(),a=f.item(2,'SOURCE01');await f.api.saveMetadata(a,{version:1,originalKey:'TARGET01'});await assert.rejects(f.api.saveMetadata(a,{version:1,originalKey:'TARGET02'}),/其他原文/);
});
const legacy={ 'dc:relation':['urn:zotero-codex:translation:SOURCE01:hash:job','urn:zotero-codex:page-counts:2:2','http://zotero.org/users/1/items/OTHER001'] };
test('migration backs up before atomic save, preserves other relations, and is idempotent',async()=>{
 const f=setup(),a=f.item(2,'TARGET01',{relations:structuredClone(legacy)});await f.api.initializePairing();
 assert.equal(f.backups[0][1][0].relations['dc:relation'].length,3);assert.equal(f.api.metadata(a).jobId,'job');assert.equal(f.api.metadata(a).pageCount,2);
 assert.equal(a.relations['dc:relation'].length,1);assert.equal(f.transactions,1);await f.api.initializePairing();assert.equal(f.transactions,1);
});
test('failed migration rolls back note and relations and records the failure',async()=>{
 const f=setup({fail:true}),a=f.item(2,'TARGET01',{relations:structuredClone(legacy)}),note=a.note;await f.api.initializePairing();
 assert.equal(a.note,note);assert.deepEqual(a.relations,legacy);assert.equal(f.errors.length,1);assert.ok(f.backups[1][1][0].error);
});
test('read-only library reads legacy pairs without migration writes',async()=>{
 const f=setup({readonly:true}),a=f.item(2,'SOURCE01'),b=f.item(3,'TARGET01',{relations:structuredClone(legacy)});
 await f.api.initializePairing();assert.equal(f.backups.length,0);assert.equal(f.api.savedPair(a).translated.id,b.id);
});
