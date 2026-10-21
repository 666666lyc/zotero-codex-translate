"""Local two-profile JSON transport and restart regression. No cloud sync or model calls."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'tmp' / ('pairing-smoke-' + uuid.uuid4().hex[:8])
WORK.mkdir(parents=True)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--zotero',type=Path,required=True)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--translated',type=Path,required=True)
args=parser.parse_args()
ZOTERO = str(args.zotero.resolve())
VERSION = json.loads((ROOT/"package.json").read_text())["version"]
FIXTURE = args.source.resolve()
TRANSLATED = args.translated.resolve()
import pymupdf
with pymupdf.open(FIXTURE) as pdf: source_pages=len(pdf)
with pymupdf.open(TRANSLATED) as pdf: translated_pages=len(pdf)
# Test-only API is injected in this disposable XPI, never the shipped package.
addon = WORK / 'test.xpi'
with zipfile.ZipFile(ROOT / f'dist/zotero-codex-translate-{VERSION}.xpi') as source, zipfile.ZipFile(addon,'w',zipfile.ZIP_DEFLATED) as target:
    for name in source.namelist():
        data=source.read(name)
        if name.endswith('content/scripts/zotero-codex-translate.js'):
            text=data.decode().replace('openSettings: () => TranslationFactory.open(false)', 'pairingTest: {metadata,saveMetadata,initializePairing,savedPair}, splitTest: SplitViewFactory, openSettings: () => TranslationFactory.open(false)')
            data=text.encode()
        if name=='bootstrap.js':
            text=data.decode().replace('await Zotero.ZoteroCodexTranslate.hooks.onStartup();','await Zotero.ZoteroCodexTranslate.hooks.onStartup();\n Services.scriptloader.loadSubScript(rootURI+"content/pairing-smoke.js",{Zotero,Services,ChromeUtils,Components,IOUtils,PathUtils,addon:Zotero.ZoteroCodexTranslate});')
            data=text.encode()
        target.writestr(name,data)
    target.writestr('content/pairing-smoke.js',r'''
(async()=>{
 const root=ROOT_PATH;
 const {phase}=await IOUtils.readJSON(root+'/phase.json');
 const report={phase,passed:false,checks:[]};
 const check=(ok,name)=>{if(!ok)throw Error(name);report.checks.push(name);};
 const delay=ms=>new Promise(r=>Zotero.getMainWindow().setTimeout(r,ms));
 const api=addon.api.pairingTest,split=addon.api.splitTest;
 await delay(1500);
 try {
 let parent,original,translated;
 if(phase==='A') {
   parent=new Zotero.Item('journalArticle');parent.setField('title','Pair transport fixture');await parent.saveTx();
   original=await Zotero.Attachments.importFromFile({file:SOURCE_PATH,parentItemID:parent.id,title:'English source'});
   translated=await Zotero.Attachments.importFromFile({file:TRANSLATED_PATH,parentItemID:parent.id,title:'已有中文译文'});
   translated.setNote('<p>用户已有笔记：保留 &amp; 内容</p>');await translated.saveTx();
   check(!api.metadata(translated),'unassociated-existing-translation');
   const reader=await Zotero.Reader.open(original.id);await delay(1500);
   const button=reader._iframeWindow.document.querySelector('.codex-comparison-toggle');
   check(button&&!button.disabled,'unique-candidate-toolbar-enabled');
   button.click();
   for(let n=0;n<100&&!api.metadata(translated);n++)await delay(100);
   check(api.metadata(translated)?.originalKey===original.key,'toolbar-auto-pair-saved-to-note');
   check(translated.getNote().includes('用户已有笔记'),'user-note-preserved');
   check(!JSON.stringify(translated.getRelations()).includes('urn:zotero-codex:'),'new-pair-has-no-custom-relations');
   const tabs=Zotero.getMainWindow().Zotero_Tabs;
   const pairTab=tabs._tabs.find(t=>t.data?.isCodexSplitView);
   check(!!pairTab,'toolbar-opened-comparison');
   const state=split.stateMap.get(pairTab.id);
   check(api.metadata(translated).sourcePageCount===SOURCE_PAGES&&api.metadata(translated).pageCount===TRANSLATED_PAGES,'actual-page-counts-saved');
   if(SOURCE_PAGES!==TRANSLATED_PAGES) {
     check(state.primarySide===null,'unequal-pages-default-independent');
     check(state.container.querySelector('.codex-comparison-page-warning')?.textContent.includes('默认独立浏览'),'unequal-pages-warning');
   } else check(!!state.primarySide,'equal-pages-default-sync');
   for(const tab of [...tabs._tabs])if(tab.data?.isCodexSplitView)await split.closeComparison(tab.id);
   const legacy=await Zotero.Attachments.importFromFile({file:TRANSLATED_PATH,parentItemID:parent.id,title:'Legacy migration fixture'});
   legacy.setNote('<p>Legacy user note</p>');legacy.addRelation('dc:relation','urn:zotero-codex:translation:'+original.key+':hash:legacy-job');
   legacy.addRelation('dc:relation','urn:zotero-codex:page-counts:2:2');await legacy.saveTx();
   await api.initializePairing();
   check(api.metadata(legacy)?.jobId==='legacy-job'&&api.metadata(legacy)?.pageCount===2,'real-database-migration');
   check(!JSON.stringify(legacy.getRelations()).includes('urn:zotero-codex:'),'legacy-urns-removed');
   check(legacy.getNote().includes('Legacy user note'),'migration-preserves-user-note');
   const dirs=await IOUtils.getChildren(Zotero.DataDirectory.dir+'/codex-translate/migration-backups');
   check(dirs.some(p=>p.endsWith('-before.json')),'migration-backup-written');
   const note=legacy.getNote();await api.initializePairing();check(note===legacy.getNote(),'migration-idempotent');
   // Only ordinary item JSON and PDF files travel to profile B; no tasks or DB copied.
   await IOUtils.writeJSON(root+'/transport.json',{parent:parent.toJSON(),original:original.toJSON(),translated:translated.toJSON()});
   await IOUtils.writeJSON(root+'/ids-A.json',{original:original.id,translated:translated.id});
 } else {
   const data=await IOUtils.readJSON(root+'/transport.json');
   if(phase==='B') {
     for(let i=0;i<6;i++){const dummy=new Zotero.Item('book');dummy.setField('title','ID offset '+i);await dummy.saveTx();}
     parent=new Zotero.Item('journalArticle');parent.libraryID=1;parent.key=data.parent.key;await parent.loadPrimaryData();parent.fromJSON(data.parent);await parent.saveTx();
     for(const [json,file] of [[data.original,SOURCE_PATH],[data.translated,TRANSLATED_PATH]]){
       const item=new Zotero.Item('attachment');item.libraryID=1;item.key=json.key;await item.loadPrimaryData();item.fromJSON(json);item.parentID=parent.id;await item.saveTx();
       const directory=Zotero.DataDirectory.dir+'/storage/'+item.key;await IOUtils.makeDirectory(directory,{createAncestors:true});await IOUtils.copy(file,directory+'/'+json.filename);
     }
   }
   original=Zotero.Items.getByLibraryAndKey(1,data.original.key);translated=Zotero.Items.getByLibraryAndKey(1,data.translated.key);
   await original.loadDataType('note');await translated.loadDataType('note');
   const old=await IOUtils.readJSON(root+'/ids-A.json');
   check(original.id!==old.original&&translated.id!==old.translated,'new-local-ids');
   const pair=api.savedPair(translated);check(pair?.original.id===original.id&&pair.translated.id===translated.id,'pair-restored-from-standard-json');
   check(translated.getNote().includes('用户已有笔记'),'transport-preserves-user-note');
   await split.openTranslationPair(pair.original.id,pair.translated.id);await delay(1800);
   const tab=Zotero.getMainWindow().Zotero_Tabs._tabs.find(t=>t.data?.isCodexSplitView);
   check(tab?.data.leftItemID===original.id&&tab?.data.rightItemID===translated.id,'direction-preserved-after-transport-or-restart');
   check(!JSON.stringify(translated.toJSON().relations).includes('urn:zotero-codex:'),'serialized-relations-sync-compatible');
   await split.closeComparison(tab.id);
 }
 report.passed=true;
 }catch(e){report.error=String(e);report.stack=e.stack;}
 await IOUtils.writeJSON(root+'/report-'+phase+'.json',report);
 Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);
})();
'''.replace('ROOT_PATH',json.dumps(str(WORK))).replace('SOURCE_PATH',json.dumps(str(FIXTURE))).replace('TRANSLATED_PATH',json.dumps(str(TRANSLATED))).replace('SOURCE_PAGES',str(source_pages)).replace('TRANSLATED_PAGES',str(translated_pages)))


def install(port,process):
    deadline=time.monotonic()+30
    while True:
        try:s=socket.create_connection(('127.0.0.1',port),2);break
        except OSError:
            if time.monotonic()>deadline or process.poll() is not None:raise
            time.sleep(.2)
    s.settimeout(30)
    def receive():
        size=b''
        while not size.endswith(b':'):size+=s.recv(1)
        data=b''
        while len(data)<int(size[:-1]):data+=s.recv(int(size[:-1])-len(data))
        return json.loads(data)
    def request(to,kind,**args):
        data=json.dumps(dict(to=to,type=kind,**args)).encode();s.sendall(str(len(data)).encode()+b':'+data)
        while True:
            reply=receive()
            if reply.get('from')==to:
                if reply.get('error'):raise RuntimeError(reply)
                return reply
    receive();actors=request('root','getRoot');request(actors['addonsActor'],'installTemporaryAddon',addonPath=str(addon));s.close()

for phase in ['A','B','restart-B']:
    folder=WORK/('B' if phase=='restart-B' else phase);profile=folder/'profile';data=folder/'data';profile.mkdir(parents=True,exist_ok=True);data.mkdir(exist_ok=True)
    (WORK/'phase.json').write_text(json.dumps({'phase':phase}))
    prefs={'extensions.zotero.dataDir':str(data),'extensions.zotero.useDataDir':True,'extensions.zotero.firstRun':False,'extensions.zotero.firstRun2':False,'extensions.zotero.sync.autoSync':False,'extensions.zotero.automaticScraperUpdates':False,'app.update.auto':False,'extensions.update.enabled':False,'devtools.debugger.remote-enabled':True,'devtools.debugger.prompt-connection':False,'devtools.debugger.remote-websocket':False}
    (profile/'user.js').write_text('\n'.join(f'user_pref({json.dumps(k)},{json.dumps(v)});' for k,v in prefs.items()))
    with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    env=os.environ.copy();env['MOZ_HEADLESS']='1'
    with (folder/(phase+'.log')).open('w') as log:
        p=subprocess.Popen([ZOTERO,'-no-remote','-profile',str(profile),'-purgecaches','-start-debugger-server',str(port),'-ZoteroDebugText'],stdout=log,stderr=log,env=env)
        try:
            install(port,p);deadline=time.monotonic()+90;report=WORK/('report-'+phase+'.json')
            while not report.exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.2)
            result=json.loads(report.read_text());print(json.dumps(result,ensure_ascii=False),flush=True)
            if not result['passed']:raise RuntimeError('Pairing integration failed: '+phase)
            p.wait(timeout=15)
        finally:
            if p.poll() is None:p.terminate();p.wait(timeout=15)
print('Reports:',WORK,flush=True)
