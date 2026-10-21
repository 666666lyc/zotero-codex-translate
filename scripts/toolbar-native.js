/* Isolated test controller. Input is supplied only through the native mouse/keyboard. */
var runNativeToolbar = async function (opts) {
  const main=Zotero.getMainWindow(),tabs=main.Zotero_Tabs;
  const file=name=>opts.root+opts.separator+name;
  const delay=ms=>new Promise(r=>main.setTimeout(r,ms));
  const until=async f=>{for(let i=0;i<300;i++){if(await f())return;await delay(100);}throw Error('Reader not ready');};
  const parent=new Zotero.Item('journalArticle');parent.setField('title','Native toolbar acceptance '+opts.version);await parent.saveTx();
  const source=await Zotero.Attachments.importFromFile({file:opts.sourcePdf,parentItemID:parent.id,title:'Native toolbar English'});
  const target=await Zotero.Attachments.importFromFile({file:opts.translatedPdf,parentItemID:parent.id,title:'Native toolbar Chinese'});
  target.addRelation('dc:relation','urn:zotero-codex:translation:'+source.key+':fixture:native');await target.saveTx();
  const events=[],docs=new Map(),ids=new WeakMap();let serial=0,lastCommand=0;
  const id=node=>{if(!node)return null;if(!ids.has(node))ids.set(node,++serial);return ids.get(node);};
  const add=(kind,data={})=>{events.push({at:Date.now(),kind,...data});if(events.length>1200)events.shift();};
  const watch=doc=>{
    if(docs.has(doc))return;
    const capture=e=>{
      const button=e.target.closest?.('button');
      add(e.type,{node:id(button||e.target),compare:!!button?.matches('.codex-comparison-toggle'),connected:!!button?.isConnected,trusted:e.isTrusted});
    };
    for(const type of ['pointerdown','mousedown','pointerup','mouseup','click','focusin','focusout'])doc.addEventListener(type,capture,{capture:true,mozSystemGroup:true});
    const observer=new doc.defaultView.MutationObserver(records=>{
      for(const record of records)for(const removed of record.removedNodes){
        const buttons=[...(removed.querySelectorAll?.('.codex-comparison-toggle')||[])];
        if(removed.matches?.('.codex-comparison-toggle'))buttons.push(removed);
        for(const button of buttons)add('button-detached',{node:id(button),connected:button.isConnected});
      }
    });
    observer.observe(doc.documentElement,{childList:true,subtree:true});docs.set(doc,{capture,observer});
  };
  const listener=({doc})=>{watch(doc);add('renderToolbar',{document:id(doc)});};
  Zotero.Reader.registerEventListener('renderToolbar',listener,'native-toolbar-test@localhost');
  let reader=await Zotero.Reader.open(source.id);
  await until(()=>reader._iframeWindow?.document.querySelector('.codex-comparison-toggle'));
  watch(reader._iframeWindow.document);watch(main.document);main.maximize();
  const inspect=()=>{
    const tab=tabs._getTab(tabs.selectedID)?.tab;
    const container=main.document.getElementById(tabs.selectedID);
    const buttons=[];
    for(const doc of docs.keys())try{
      for(const b of doc.querySelectorAll('.codex-comparison-toggle')){
        const s=doc.defaultView.getComputedStyle(b);
        buttons.push({node:id(b),connected:b.isConnected,text:b.textContent,disabled:b.disabled,pressed:b.getAttribute('aria-pressed'),shadow:s.boxShadow,background:s.backgroundColor});
      }
    }catch{}
    return {version:opts.version,sourceID:source.id,targetID:target.id,split:!!tab?.data?.translationPair,buttons,events,command:lastCommand,panes:container?.querySelectorAll('browser').length};
  };
  await IOUtils.writeJSON(file('native-ready.json'),inspect());
  if(opts.styleAudit){
    const doc=reader._iframeWindow.document,root=doc.documentElement,oldScheme=root.getAttribute('data-color-scheme');
    const group=doc.querySelector('.codex-toolbar-group'),buttons=[...group.querySelectorAll('button')],compare=group.querySelector('.codex-comparison-toggle');
    const checks=[];
    try {
      for(const scheme of ['light','dark'])for(const scale of [1,1.25,1.5]){
        root.setAttribute('data-color-scheme',scheme);group.style.transform=`scale(${scale})`;group.style.transformOrigin='right center';
        compare.setAttribute('aria-pressed','true');await delay(50);
        const style=doc.defaultView.getComputedStyle(compare),bounds=compare.getBoundingClientRect();
        if(style.boxShadow==='none'||bounds.height<27||parseFloat(style.borderRadius)<4)throw Error('Missing themed selection feedback');
        for(let i=1;i<buttons.length;i++){
          if(buttons[i-1].getBoundingClientRect().right>buttons[i].getBoundingClientRect().left)throw Error('Scaled buttons overlap');
        }
        checks.push({scheme,scale,shadow:style.boxShadow,background:style.backgroundColor});
      }
      await IOUtils.writeJSON(file('style-audit.json'),{passed:true,checks,scope:'Reader DOM theme and CSS scale; OS display scaling unchanged'});
    } finally {
      if(oldScheme===null)root.removeAttribute('data-color-scheme');else root.setAttribute('data-color-scheme',oldScheme);
      group.style.transform='';group.style.transformOrigin='';compare.setAttribute('aria-pressed','false');
    }
    Zotero.Reader.unregisterEventListener('renderToolbar',listener);
    return;
  }
  try {
    for(let tick=0;tick<9000;tick++){
      if(await IOUtils.exists(file('native-command.json'))){
        const c=await IOUtils.readJSON(file('native-command.json'));
        if(c.id!==lastCommand){
          lastCommand=c.id;
          if(c.action==='finish')break;
          if(c.action==='reset'){
            // Close only this isolated test's tabs, preserving fixture attachments.
            for(const t of [...tabs._tabs])if(t.type==='reader')tabs.close(t.id);
            await until(()=>!Zotero.Reader._readers.some(r=>r.itemID===source.id||r.itemID===target.id));
            reader=await Zotero.Reader.open(c.chinese?target.id:source.id);
            await until(()=>reader._iframeWindow?.document.querySelector('.codex-comparison-toggle'));
            watch(reader._iframeWindow.document);
          }
          if(c.action==='annotation'){
            const r=reader._internalReader;
            const a=r._annotationManager.addAnnotation(Components.utils.cloneInto({type:'highlight',color:'#ffd400',text:'Test',comment:'',pageLabel:'1',sortIndex:'00000|000000|00000',position:{pageIndex:0,rects:[[50,50,200,80]]}},reader._iframeWindow));
            await delay(300);r._updateState({selectedAnnotationIDs:[a.id]});
          }
          if(c.action==='redraw')reader._internalReader._updateState({selectedAnnotationIDs:[]});
          if(c.action==='arm-rebuild'){
            const doc=reader._iframeWindow.document;
            doc.addEventListener('pointerdown',event=>{
              if(!event.target.closest?.('.codex-comparison-toggle'))return;
              const previous=doc.querySelector('.codex-comparison-toggle');
              const host=doc.querySelector('.toolbar .custom-sections');
              host.replaceChildren();
              Zotero.Reader._dispatchEvent({type:'renderToolbar',doc,reader,params:{},append:(...nodes)=>{
                const section=doc.createElement('div');section.className='section';section.append(...nodes);host.append(section);
              }});
              add('controlled-rebuild-during-pointerdown',{before:id(previous),after:id(doc.querySelector('.codex-comparison-toggle'))});
            },{capture:true,once:true});
          }
          if(c.action==='page-focus'||c.action==='focus-compare'){
            const live=Zotero.Reader.getByTabID(tabs.selectedID);
            const doc=live?._iframeWindow?.document;
            if(doc)watch(doc);
            if(c.action==='page-focus')doc.getElementById('pageNumber').focus();
            else (main.document.getElementById(tabs.selectedID)?.querySelector('.codex-comparison-toggle')||doc?.querySelector('.codex-comparison-toggle')).focus();
          }
          if(c.action==='clear')events.length=0;
          await delay(150);
        }
      }
      await IOUtils.writeJSON(file('native-state.json'),inspect());
      await delay(200);
    }
    await IOUtils.writeJSON(file('native-final.json'),inspect());
  } finally {
    Zotero.Reader.unregisterEventListener('renderToolbar',listener);
    for(const [doc,{capture,observer}] of docs){
      try { observer.disconnect(); } catch { /* original reader has unloaded */ }
      for(const type of ['pointerdown','mousedown','pointerup','mouseup','click','focusin','focusout'])try{doc.removeEventListener(type,capture,{capture:true,mozSystemGroup:true});}catch{}
    }
  }
};
