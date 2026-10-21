"""Install the unchanged release XPI, restart Zotero and click real controls.

Uses a separate temporary controller addon, an isolated profile/library and the
debugger only for loading that controller. Never touches the user's profile.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--zotero", type=Path, required=True)
parser.add_argument("--xpi", type=Path, help="Explicit archived XPI for a version comparison")
parser.add_argument("--root", type=Path)
parser.add_argument("--data-dir", type=Path, help="Reuse ONLY a prior workspace tmp test library/cache")
parser.add_argument("--timeout", type=int, default=900)
parser.add_argument("--background", action="store_true", help="Opt-in real Codex translation of a synthetic attachment through its toolbar")
parser.add_argument("--reading", action="store_true")
parser.add_argument("--recover-existing", action="store_true")
parser.add_argument("--reading-only", action="store_true")
parser.add_argument("--source-pdf", type=Path)
parser.add_argument("--translated-pdf", type=Path)
parser.add_argument("--interactive", action="store_true", help="Hold an interactive reading test for native mouse/keyboard checks")
parser.add_argument("--native-toolbar", action="store_true", help="Native input diagnostics, no programmatic button clicks")
parser.add_argument("--style-audit", action="store_true", help="Reader theme/scale computed-style checks, then exit")
args = parser.parse_args()
if args.native_toolbar:
    args.interactive = True
work = (args.root or ROOT / "tmp" / ("deployment-smoke-" + uuid.uuid4().hex[:8])).resolve()
work.relative_to((ROOT / "tmp").resolve())
data = (args.data_dir or work / "data").resolve()
data.relative_to((ROOT / "tmp").resolve())
profile = work / "profile"
profile.mkdir(parents=True, exist_ok=True)
data.mkdir(parents=True, exist_ok=True)
controller = work / "controller"
controller.mkdir(exist_ok=True)
package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
xpi = (args.xpi or ROOT / "dist" / f"zotero-codex-translate-{package['version']}.xpi").resolve()
assert xpi.is_file()
if args.xpi:
    import zipfile
    with zipfile.ZipFile(xpi) as archive:
        package['version'] = json.loads(archive.read('manifest.json'))['version']
if args.background:
    import pymupdf
    fixture = pymupdf.open()
    page = fixture.new_page()
    page.insert_textbox((50, 50, 500, 160), "Background translation test. This scientific paper describes a robot learning to walk on uneven ground. The proposed method improves stability and preserves the experimental results.", fontsize=11)
    fixture.save(work / "background.pdf")
    fixture.close()
(controller / "manifest.json").write_text(json.dumps({"manifest_version": 2, "name": "Isolated deployment test controller", "version": "1.0", "applications": {"zotero": {"id": "codex-deployment-test@localhost", "strict_min_version": "9.0.0", "strict_max_version": "9.0.*", "update_url": "https://raw.githubusercontent.com/666666lyc/zotero-codex-translate/main/updates.json"}}}), encoding="utf-8")
(controller / "bootstrap.js").write_text("function install(){}\nfunction uninstall(){}\nfunction shutdown(){}\nfunction startup(data){ Services.scriptloader.loadSubScript(data.rootURI+'test.js',{Zotero,Services,ChromeUtils,Components,IOUtils}); }", encoding="utf-8")

HARNESS = r'''
(async () => {
 const report={passed:false,checks:[]};
 const join=(...p)=>p.join(OPTS.separator);
 const delay=ms=>new Promise(resolve=>Zotero.getMainWindow().setTimeout(resolve,ms));
 const write=()=>Zotero.File.putContentsAsync(join(OPTS.root,OPTS.mode+'-report.json'),JSON.stringify(report,null,2));
 const record=async name=>{report.checks.push(name);await Zotero.File.putContentsAsync(join(OPTS.root,'progress.json'),JSON.stringify(report,null,2));};
 const until=async (condition,label,timeout=30000)=>{const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await condition())return;await delay(100);}throw Error('Timeout: '+label);};
 let dialog;
 try {
  await Promise.all([Zotero.initializationPromise,Zotero.unlockPromise,Zotero.uiReadyPromise]);
  const {AddonManager}=ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
  if(OPTS.mode==='install') {
   const installation=await AddonManager.getInstallForFile(Zotero.File.pathToFile(OPTS.xpi));
   await installation.install();
   const installed=await AddonManager.getAddonByID('codex-translate@zotero.local');
   if(installed?.version!==OPTS.version)throw Error('Installed version mismatch');
   await record('unchanged-release-xpi-installed');
  } else {
   await until(()=>Zotero.ZoteroCodexTranslate?.data.initialized,'persistent plugin startup');
   const installed=await AddonManager.getAddonByID('codex-translate@zotero.local');
   if(installed?.temporarilyInstalled||installed?.version!==OPTS.version)throw Error('Expected permanently installed release XPI');
   await record('persistent-xpi-loaded-after-restart');
   if(OPTS.nativeToolbar){
     const scope={Zotero,Services,Components,IOUtils};
     Services.scriptloader.loadSubScript(Services.io.newFileURI(Zotero.File.pathToFile(join(OPTS.root,'controller','toolbar-native.js'))).spec,scope);
     await scope.runNativeToolbar(OPTS);
     report.passed=true;await write();Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);return;
   }
   if(!OPTS.readingOnly){
   dialog=await Zotero.ZoteroCodexTranslate.api.openSettings();
   await until(()=>dialog.document.readyState==='complete','dialog load');
   const el=id=>dialog.document.getElementById(id);
   const button=action=>dialog.document.querySelector('[data-action="'+action+'"]');
   const set=(id,value)=>{el(id).value=value;el(id).dispatchEvent(new dialog.Event('change',{bubbles:true}));};
   const completed=()=>!el('operation-state').textContent;
   const click=async(action,expect)=>{
    if(button(action).disabled)throw Error('Button disabled: '+action);
    button(action).click();
    if(!el('operation-state').textContent)throw Error('No immediate action status: '+action);
    const box=el('message').getBoundingClientRect();
    if(box.top<0||box.bottom>dialog.innerHeight)throw Error('Feedback outside initial viewport');
    if(!button('diagnose').disabled||!button('reconnect').disabled)throw Error('Conflicting buttons still enabled');
    button(action).click(); // A disabled control must not start a second operation.
    await until(completed,action,OPTS.timeout*1000);
    if(!expect.test(el('message').textContent))throw Error(action+': '+el('message').textContent);
   };
   await until(()=>el('environment-state').textContent.startsWith('环境就绪'),'upstream environment',OPTS.timeout*1000);
   await record('managed-environment-ready');
   await click('diagnose',/Codex 已登录/);
   const modelRadios=()=>[...el('model-options').querySelectorAll('input[type="radio"]')];
   if(modelRadios().length<2)throw Error('Runtime model list empty');
   await record('diagnose-click-and-runtime-models');
   let picker=el('model-picker');
   el('model-current').click();
   if(!picker.open)throw Error('Model list did not open');
   const listBox=el('model-options').getBoundingClientRect();
   const actionsBox=button('diagnose').parentElement.getBoundingClientRect();
   if(listBox.height<50||listBox.bottom>actionsBox.top)throw Error('Model choices overlay following controls');
   const chosen=modelRadios()[1];
   chosen.click();
   if(picker.open||el('model').value!==chosen.value||!el('model-current').textContent.includes(chosen.parentElement.textContent))throw Error('Model radio click did not select and close');
   if(Zotero.Prefs.get('extensions.zotero.zotero-codex-translate.model',true)!==chosen.value)throw Error('Model preference not saved');
   dialog.close();
   dialog=await Zotero.ZoteroCodexTranslate.api.openSettings();
   await until(()=>dialog.document.readyState==='complete'&&el('model').value===chosen.value,'model preference after reopening settings');
   picker=el('model-picker');
   el('model-current').click();
   const originalRadio=modelRadios()[1];
   originalRadio.focus();
   dialog.renderCodexPanel({title:'选择回归',bridgePath:'',codexPath:'',offlineAssets:'',models:modelRadios().slice(1).map(r=>({model:r.value,displayName:r.parentElement.textContent})),model:chosen.value,jobs:[],runtime:{stage:'ready'}});
   if(modelRadios()[1]!==originalRadio||dialog.document.activeElement!==originalRadio||!picker.open)throw Error('State refresh reset an open model list or focused radio');
   modelRadios()[0].click();
   if(el('model').value!==''||Zotero.Prefs.get('extensions.zotero.zotero-codex-translate.model',true)!=='')throw Error('Default model not restored');
   await record('model-list-real-click-layout-selection-persistence-and-refresh');
   const job={id:'picker-test-job-0001',model:chosen.value,state:'failed'};
   dialog.renderCodexPanel({title:'任务选择回归',bridgePath:'',codexPath:'',offlineAssets:'',models:[],model:'',jobs:[job],selectedJob:job,runtime:{stage:'ready'}});
   el('jobs-current').click();
   if(!el('jobs-picker').open||el('jobs-options').getBoundingClientRect().bottom>el('progress').getBoundingClientRect().top)throw Error('Job choices overlap progress');
   // This synthetic row exercises the real radio handler; selectJob receives
   // its ID, finds no corresponding bridge job, and safely clears selection.
   el('jobs-options').querySelector('input').checked=false;
   el('jobs-options').querySelector('input').click();
   if(el('jobs-picker').open)throw Error('Job picker did not close after choice');
   await record('job-choice-list-click-and-layout');
   dialog.scrollTo(0,0); // The job radio deliberately focused a control below the initial viewport.
   button('diagnose').click();
   button('cancelConnection').click();
   await until(completed,'cancel diagnosis');
   if(!el('message').textContent.includes('已取消'))throw Error('Cancellation feedback missing');
   await record('cancel-diagnosis-and-unlock');
   await click('reconnect',/已重新连接/);
   set('codexPath',join(OPTS.root,'missing-codex.exe'));
   await click('diagnose',/CODEX_MISSING/);
   if(!el('connection-state').textContent.includes('已连接'))throw Error('Codex configuration error killed bridge');
   await record('bad-cli-path-visible-and-bridge-preserved');
   set('codexPath','');
   set('bridgePath',join(OPTS.root,'missing-bridge.exe'));
   await click('reconnect',/操作失败/);
   if(!el('connection-state').textContent.includes('连接失败'))throw Error('Missing failed bridge status');
   if(!el('connection-diagnostics').textContent.includes('missing-bridge.exe'))throw Error('Missing startup diagnostics');
   await record('bridge-startup-failure-visible');
   const beforeRepair=await IOUtils.readJSON(join(OPTS.data,'codex-translate','runtime','current.json'));
   set('bridgePath',beforeRepair.python); // An interpreter alone is not a bridge EXE: exits 2.
   await click('reconnect',/操作失败/);
   if(!/退出码.*2/.test(el('connection-diagnostics').textContent))throw Error('Native exit code missing');
   if(!/unknown option/i.test(el('connection-diagnostics').textContent))throw Error('Native stderr missing');
   await record('native-bridge-exit-code-and-stderr-visible');
   set('bridgePath','');
   if(button('repair').disabled)throw Error('Clearing external path did not re-enable repair');
   await click('repair',/检查与修复完成/);
   if(!el('connection-state').textContent.includes('已连接'))throw Error('Repair did not reconnect bridge');
   const pointer=await IOUtils.readJSON(join(OPTS.data,'codex-translate','runtime','current.json'));
   if(pointer.bridgeVersion!==OPTS.version||pointer.resources.downloadedBytes!==0)throw Error('Repair failed to reuse verified resource cache');
   await record('failed-bridge-repaired-and-resource-cache-reused');
   for(let i=0;i<5;i++)await click('reconnect',/已重新连接/);
   await record('five-real-reconnect-clicks');
   await click('diagnose',/Codex 已登录/);
   await record('login-and-models-restored-after-repair');
   }

   if(OPTS.reading) {
    dialog?.close();dialog=null;
    const main=Zotero.getMainWindow(),tabs=main.Zotero_Tabs;
    const parent=new Zotero.Item('journalArticle');parent.setField('title','Reading acceptance 0.1.4');await parent.saveTx();
    const source=await Zotero.Attachments.importFromFile({file:OPTS.sourcePdf,parentItemID:parent.id,title:'English original'});
    let target=await Zotero.Attachments.importFromFile({file:OPTS.translatedPdf,parentItemID:parent.id,title:'Chinese native layout'});
    target.addRelation('dc:relation','urn:zotero-codex:translation:'+source.key+':fixture:reading');await target.saveTx();

    if(OPTS.recoverExisting){
      const oldID='afafafaf-1111-4444-8888-121212121212';
      const oldDir=join(OPTS.data,'codex-translate','jobs',oldID);
      await IOUtils.makeDirectory(oldDir,{createAncestors:true});
      await IOUtils.copy(OPTS.sourcePdf,join(oldDir,'input.pdf'));
      await IOUtils.copy(OPTS.translatedPdf,join(oldDir,'input.zh.mono.pdf'));
      const old={id:oldID,state:'failed',createdAt:Date.now()/1000,sourceItemKey:source.key,parentItemKey:parent.key,libraryID:source.libraryID,model:'gpt-6.1-sol',fingerprint:OPTS.sourceHash,bridgeVersion:'0.1.4',error:{code:'UNREADABLE_TEXT',message:'legacy 7pt gate'}};
      await IOUtils.writeJSON(join(oldDir,'job.json'),old);
      dialog=await Zotero.ZoteroCodexTranslate.api.openTasks();
      await until(()=>dialog.document.readyState==='complete','recovery task center');
      const chooseOld=async()=>{
       dialog.document.querySelector('[data-action="refresh"]').click();
       await until(()=>dialog.document.querySelector(`#jobs-options input[value="${oldID}"]`),'failed task loaded');
       dialog.document.getElementById('jobs-current').click();
       dialog.document.querySelector(`#jobs-options input[value="${oldID}"]`).click();
       await until(()=>!dialog.document.getElementById('recover-output').disabled,'recovery enabled');
      };
      await chooseOld();
      dialog.document.getElementById('recover-output').click();
      await until(()=>parent.getAttachments().length===3,'recovered attachment imported');
      await until(()=>dialog.document.getElementById('stage').textContent.includes('已导入'),'recovered job acknowledged');
      const recovered=parent.getAttachments().map(id=>Zotero.Items.get(id)).find(i=>i.id!==source.id&&i.id!==target.id);
      const bytes=await IOUtils.read(await recovered.getFilePathAsync()),expected=await IOUtils.read(OPTS.translatedPdf);
      if(bytes.length!==expected.length||!bytes.every((v,i)=>v===expected[i]))throw Error('Recovered PDF bytes changed');
      if((await IOUtils.readJSON(join(oldDir,'job.json'))).state!=='failed')throw Error('Original failed job was overwritten');
      await record('recovery-real-click-native-bytes-import-and-original-job-preserved');
      await chooseOld();dialog.document.getElementById('recover-output').click();
      await until(()=>dialog.document.getElementById('stage').textContent.includes('已导入'),'repeat recovery acknowledged');
      if(parent.getAttachments().length!==3)throw Error('Recovery imported duplicate attachment');
      await record('repeat-recovery-no-duplicate-import');
      target=recovered;dialog.close();dialog=null;
    }
    let reader=await Zotero.Reader.open(source.id);
    const outerDoc=()=>reader._iframeWindow?.document;
    await until(()=>outerDoc()?.querySelector('.codex-comparison-toggle'),'direct compare toolbar');
    const initialTab=reader.tabID;
    if(outerDoc().querySelector('.codex-comparison-toggle').disabled)throw Error('Associated translation not found');
    outerDoc().querySelector('.codex-comparison-toggle').click();
    await until(()=>tabs._getTab(tabs.selectedID)?.tab?.data?.translationPair,'comparison in current tab');
    if(tabs.selectedID!==initialTab)throw Error('Toolbar opened a duplicate tab');
    let tabID=tabs.selectedID;
    const pair=()=>[...main.document.getElementById(tabID).querySelectorAll('browser')].slice(0,2);
    const internal=b=>(b.contentWindow.wrappedJSObject||b.contentWindow)._reader;
    const pdfWindow=b=>internal(b)._primaryView._iframeWindow;
    const viewer=b=>pdfWindow(b).PDFViewerApplication.pdfViewer;
    await until(()=>pair().length===2&&pair().every(b=>viewer(b)?.pagesCount>1),'two PDF views loaded');
    await delay(1600);
    let [left,right]=pair();
    const gesture=(b,kind='wheel')=>{
      const w=pdfWindow(b);w.document.dispatchEvent(new w.Event(kind,{bubbles:true}));
    };
    const page=async(b,index)=>{gesture(b,'keydown');await internal(b).navigate(Components.utils.cloneInto({pageIndex:index},b.contentWindow));await delay(700);};
    await page(left,3);
    if(viewer(right).currentPageNumber!==4)throw Error('English page did not drive Chinese');
    await page(right,5);
    if(viewer(left).currentPageNumber!==6){
      report.readingDebug=[left,right].map(b=>{const v=viewer(b);return {page:v.currentPageNumber,top:v.container.scrollTop,height:v.container.clientHeight,scale:v.currentScale,pages:[3,4,5,6].map(i=>{const r=v.getPageView(i).div.getBoundingClientRect();return {page:i+1,top:r.top,bottom:r.bottom};})};});
      throw Error('Chinese page did not drive English');
    }
    const bounds=main.document.getElementById(tabID).querySelector('.codex-comparison-toggle').getBoundingClientRect();
    if(bounds.width<40||bounds.height<15||bounds.right>main.innerWidth)throw Error('Comparison control not visible');
    await record('bidirectional-page-navigation');
    const scroll=async(b,delta,kind='wheel')=>{gesture(b,kind);viewer(b).container.scrollTop+=delta;await delay(500);};
    await scroll(left,120);const before=viewer(left).container.scrollTop;
    await scroll(right,100);
    if(Math.abs(viewer(left).container.scrollTop-before)<30)throw Error('Chinese continuous scroll did not drive English');
    await record('bidirectional-continuous-scroll');
    const syncButton=b=>main.document.getElementById(tabID).querySelector('.codex-comparison-sync');
    if(!syncButton(left))throw Error('Split toolbar sync control missing');
    syncButton(left).click();
    const fixed=viewer(left).container.scrollTop;
    await scroll(right,160,'pointerdown');
    if(Math.abs(viewer(left).container.scrollTop-fixed)>2)throw Error('Independent view still synchronized');
    syncButton(right).click();await delay(500);
    await scroll(right,100,'keydown');
    if(Math.abs(viewer(left).container.scrollTop-fixed)<30)throw Error('Re-enabled synchronization failed');
    await record('independent-browsing-and-sync-restored-with-toolbar');
    viewer(right).currentScale=1.5;await delay(500);
    const oldScale=viewer(left).currentScale;
    await scroll(right,150);
    if(viewer(left).currentScale!==oldScale)throw Error('Synchronization changed independent zoom');
    await record('position-sync-keeps-independent-zoom');
    for(const b of [left,right]){
      internal(b)._annotationManager.addAnnotation(Components.utils.cloneInto({type:'highlight',color:'#ffd400',text:'Reading test',comment:'',pageLabel:'1',sortIndex:'00000|000000|00000',position:{pageIndex:0,rects:[[50,50,100,65]]}},b.contentWindow));
    }
    await until(()=>source.getAnnotations().length===1&&target.getAnnotations().length===1,'separate annotation save');
    await record('annotations-saved-to-correct-attachments');
    gesture(right,'pointerdown');
    main.document.getElementById(tabID).querySelector('.codex-comparison-toggle').click();
    await until(()=>!tabs._getTab(tabs.selectedID)?.tab?.data?.isCodexSplitView&&Zotero.Reader.getByTabID(tabs.selectedID)?.itemID===target.id,'exit keeps Chinese active reader');
    await record('toolbar-exit-restores-active-attachment');
    reader=Zotero.Reader.getByTabID(tabs.selectedID);
    await until(()=>outerDoc()?.querySelector('.codex-comparison-toggle'),'Chinese compare toolbar');
    outerDoc().querySelector('.codex-comparison-toggle').click();
    await until(()=>tabs._getTab(tabs.selectedID)?.tab?.data?.translationPair,'comparison from Chinese');
    tabID=tabs.selectedID;await delay(1600);[left,right]=pair();
    const data=tabs._getTab(tabID).tab.data;
    if(data.leftItemID!==source.id||data.rightItemID!==target.id)throw Error('Chinese entry reversed source and target');
    await record('Chinese-entry-keeps-version-and-English-left');
    // Closing the bridge does not prevent already imported PDF comparison.
    if(OPTS.interactive){
      main.maximize();await record('interactive-reading-ready');
      await until(()=>IOUtils.exists(join(OPTS.root,'finish-interactive')),'native UI reading checks',600000);
    }
    const selectedContainer=main.document.getElementById(tabs.selectedID);
    if(tabs._getTab(tabs.selectedID)?.tab?.data?.isCodexSplitView)selectedContainer.querySelector('.codex-comparison-toggle').click();
    await until(()=>!tabs._getTab(tabs.selectedID)?.tab?.data?.isCodexSplitView,'exit before addon disable');
    await (await AddonManager.getAddonByID('codex-translate@zotero.local')).disable();
    await record('addon-disable-preserves-normal-reader');
   }
   if(OPTS.background) {
    dialog.close(); dialog=null;
    const main=Zotero.getMainWindow();
    const parent=new Zotero.Item('journalArticle');parent.setField('title','Synthetic background translation regression');await parent.saveTx();
    const source=await Zotero.Attachments.importFromFile({file:join(OPTS.root,'background.pdf'),parentItemID:parent.id,title:'Synthetic English PDF'});
    const reader=await Zotero.Reader.open(source.id);
    const readerDocument=()=>reader._iframeWindow?.document;
    const translateButton=()=>[...(readerDocument()?.querySelectorAll('button')||[])].find(b=>b.textContent==='Codex 全文翻译');
    await until(()=>translateButton(),'reader translation toolbar');
    const visibleDialogs=()=>[...Services.wm.getEnumerator(null)].filter(w=>w.location?.href?.endsWith('/content/translation.xhtml'));
    translateButton().click();
    translateButton().click(); // Real duplicate click must be coalesced.
    await delay(500);
    if(visibleDialogs().length)throw Error('Starting translation opened a progress dialog');
    await record('real-toolbar-start-without-dialog');
    const readJob=async()=>{
     const dirs=await IOUtils.getChildren(join(OPTS.data,'codex-translate','jobs'));
     const matches=[];
     for(const dir of dirs){try{const j=await IOUtils.readJSON(join(dir,'job.json'));if(j.sourceItemKey===source.key)matches.push(j);}catch{}}
     return matches.sort((a,b)=>b.createdAt-a.createdAt)[0];
    };
    await until(readJob,'background job persisted',60000);
    const statusButton=readerDocument().querySelector('.codex-translation-task-status');
    if(!statusButton?.textContent.includes('翻译中'))throw Error('Toolbar task status not updated');
    statusButton.click();
    await until(()=>visibleDialogs().length,'open task center');
    dialog=visibleDialogs()[0];
    await until(()=>dialog.document.getElementById('jobs-options')?.querySelectorAll('input').length,'task center list');
    dialog.close(); dialog=null;
    await record('task-center-open-close-does-not-cancel');
    await until(async()=>['completed','imported','failed'].includes((await readJob())?.state),'background completion',OPTS.timeout*1000);
    const job=await readJob();
    if(job.state==='failed')throw Error(JSON.stringify(job.error));
    await until(()=>parent.getAttachments().length===2,'automatic background import');
    if(visibleDialogs().length)throw Error('Completion opened a dialog');
    await delay(500);
    if(parent.getAttachments().length!==2)throw Error('Duplicate result imported');
    if(job.metrics?.requests<1)throw Error('No recorded model request');
    await record('background-real-translation-import-and-no-duplicate');
    statusButton.click();
    await until(()=>visibleDialogs().length,'reopen task center');
    dialog=visibleDialogs()[0];
    await until(()=>dialog.document.getElementById('job-metrics')?.textContent.includes('模型请求'),'persisted task metrics');
    const errors=dialog.document.getElementById('job-error').textContent;
    if(errors)throw Error(errors);
    await record('task-center-after-completion-with-metrics');
    const retranslate=dialog.document.querySelector('[data-action="retranslate"]');
    if(retranslate.disabled)throw Error('Explicit new version button disabled after completion');
    retranslate.click();
    await until(async()=>{const latest=await readJob();return latest?.id!==job.id&&latest?.state==='running';},'new version started');
    const next=await readJob();
    await until(()=>[...dialog.document.querySelectorAll('#jobs-options input')].some(r=>r.value===next.id),'new job selectable');
    dialog.document.querySelector(`#jobs-options input[value="${next.id}"]`).click();
    const cancelButton=dialog.document.querySelector('[data-action="cancel"]');
    if(cancelButton.disabled)throw Error('Active job cancel disabled');
    cancelButton.click();
    await until(async()=>(await readJob())?.state==='cancelled','cancel background task');
    await until(()=>!dialog.document.querySelector('[data-action="resume"]').disabled,'resume enabled');
    dialog.document.querySelector('[data-action="resume"]').click();
    await until(async()=>['imported','failed'].includes((await readJob())?.state),'resume completion',OPTS.timeout*1000);
    const resumed=await readJob();
    if(resumed.state!=='imported'||resumed.id!==next.id||resumed.metrics.requests!==0)throw Error('Resume changed identity, failed or unexpectedly called model');
    if(parent.getAttachments().length!==3)throw Error('Explicit new version did not preserve prior attachment');
    await record('real-cancel-resume-zero-model-calls-and-version-preservation');
   }
  }
  report.passed=true;
 } catch(error){report.error=String(error);report.stack=error.stack;}
 if(dialog)dialog.close();
 await write();
 Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);
})();
'''


def load_controller(port, process):
    connection = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and process.poll() is None:
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            time.sleep(.5)
    if connection is None:
        raise RuntimeError("Isolated debugger unavailable")
    connection.settimeout(180)
    def receive():
        prefix = b""
        while not prefix.endswith(b":"):
            chunk = connection.recv(1)
            if not chunk:
                raise RuntimeError("Debugger disconnected")
            prefix += chunk
        size = int(prefix[:-1])
        body = b""
        while len(body) < size:
            body += connection.recv(size-len(body))
        return json.loads(body)
    def request(actor, kind, **params):
        payload = json.dumps({"to": actor, "type": kind, **params}).encode()
        connection.sendall(str(len(payload)).encode()+b":"+payload)
        while True:
            reply = receive()
            if reply.get("from") == actor:
                if reply.get("error"):
                    raise RuntimeError(str(reply))
                return reply
    try:
        receive()
        actors = request("root", "getRoot")
        request(actors["addonsActor"], "installTemporaryAddon", addonPath=str(controller))
    finally:
        connection.close()


for mode in ("install", "test"):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    options = dict(root=str(work), data=str(data), xpi=str(xpi), version=package["version"], mode=mode, separator=os.sep, timeout=args.timeout, background=args.background, sourceHash=__import__("hashlib").sha256(args.source_pdf.read_bytes()).hexdigest() if args.source_pdf else "", recoverExisting=args.recover_existing, reading=args.reading, readingOnly=args.reading_only, interactive=args.interactive, nativeToolbar=args.native_toolbar, styleAudit=args.style_audit, sourcePdf=str(args.source_pdf.resolve()) if args.source_pdf else "", translatedPdf=str(args.translated_pdf.resolve()) if args.translated_pdf else "")
    if args.native_toolbar:
        import shutil
        shutil.copyfile(ROOT / 'scripts/toolbar-native.js',controller / 'toolbar-native.js')
    (controller / "test.js").write_text("const OPTS="+json.dumps(options)+";\n"+HARNESS, encoding="utf-8")
    prefs = {"extensions.zotero.dataDir": str(data), "extensions.zotero.useDataDir": True, "extensions.zotero.firstRun": False, "extensions.zotero.firstRun2": False, "extensions.zotero.sync.autoSync": False, "extensions.zotero.automaticScraperUpdates": False, "extensions.autoDisableScopes": 0, "extensions.enabledScopes": 15, "extensions.zoteroWinWordIntegration.skipInstallation": True, "extensions.zoteroMacWordIntegration.skipInstallation": True, "extensions.zoteroOpenOfficeIntegration.skipInstallation": True, "extensions.update.enabled": False, "app.update.auto": False, "devtools.debugger.remote-enabled": True, "devtools.debugger.remote-websocket": False, "devtools.debugger.prompt-connection": False, "layers.acceleration.disabled": True, "gfx.webrender.force-disabled": True}
    (profile / "user.js").write_text("\n".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k,v in prefs.items()), encoding="utf-8")
    output = work / f"{mode}-report.json"
    output.unlink(missing_ok=True)
    startup = None
    if os.name == "nt" and not args.interactive:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (work / f"{mode}-zotero.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(args.zotero), "-no-remote", "-profile", str(profile), "--purgecaches", "--start-debugger-server", str(port), "-ZoteroDebugText"], stdout=log, stderr=log, startupinfo=startup, env={**{k:v for k,v in os.environ.items() if k != "MOZ_HEADLESS"}, **({} if args.interactive else {"MOZ_HEADLESS":"1"})})
        try:
            load_controller(port, process)
            deadline = time.monotonic() + args.timeout
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(.5)
            if not output.exists():
                # Windows launcher exit can precede visibility of the final async report.
                for _ in range(20):
                    if output.exists(): break
                    time.sleep(.1)
            if not output.exists():
                raise RuntimeError(f"{mode} did not finish: {work}")
            report = json.loads(output.read_text(encoding="utf-8"))
            print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
            if not report["passed"]:
                raise SystemExit(1)
        finally:
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                import psutil
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
print(work)
