import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {build} from 'esbuild';
const bundle=await build({entryPoints:['src/modules/readerToolbar.ts'],bundle:true,write:false,format:'cjs'});
const context={module:{exports:{}},exports:{}};context.exports=context.module.exports;
vm.runInNewContext(bundle.outputFiles[0].text,context);
const {ReaderToolbarRegistry,ComparisonOperations,preserveToolbarClick}=context.module.exports;

function clickFixture() {
  const listeners=new Map();let activations=0;
  const doc={addEventListener:(k,fn)=>listeners.set(k,fn),removeEventListener:k=>listeners.delete(k)};
  const button={disabled:false,isConnected:true,closest:()=>button,click:()=>activations++};
  const outside={closest:()=>null},group={contains:b=>b===button};
  const release=preserveToolbarClick(doc,group);
  const send=(type,extra={})=>listeners.get(type)?.({target:button,isTrusted:true,button:0,clientX:10,clientY:10,detail:1,timeStamp:10,preventDefault(){},stopPropagation(){},...extra});
  return {button,outside,send,release,get activations(){return activations;}};
}

test('a retargeted native click activates once; pointerdown/up alone never execute',()=>{
  const f=clickFixture();f.send('pointerdown');f.send('pointerup');assert.equal(f.activations,0);
  f.send('click',{target:f.outside});assert.equal(f.activations,1);
  f.send('click',{target:f.outside});assert.equal(f.activations,1);f.release();
});

test('normal clicks, keyboard clicks, cancelled presses, drags and outside-up are not replayed',()=>{
  for(const kind of ['normal','keyboard','cancel','drag','outside','timeout']){
    const f=clickFixture();f.send('pointerdown');
    if(kind==='cancel')f.send('pointercancel');
    if(kind==='drag')f.send('pointermove',{clientX:25});
    f.send('pointerup',{target:kind==='outside'?f.outside:f.button});
    f.send('click',{target:kind==='normal'?f.button:f.outside,detail:kind==='keyboard'?0:1,timeStamp:kind==='timeout'?700:11});
    assert.equal(f.activations,0,kind);f.release();
  }
});

test('reader toolbar retains exact nodes and one handler across repeated render callbacks',()=>{
  const registry=new ReaderToolbarRegistry(),win=new EventTarget(),doc={defaultView:win};
  let creates=0,releases=0,clicks=0;
  const make=()=>{creates++;const button=new EventTarget();button.addEventListener('click',()=>clicks++);return {button};};
  const entry=registry.get(doc,make,()=>releases++);
  // Upstream clears/reappends a custom section between pointerdown and pointerup.
  entry.button.dispatchEvent(new Event('pointerdown'));
  for(let i=0;i<10;i++)assert.equal(registry.get(doc,make,()=>releases++),entry);
  entry.button.dispatchEvent(new Event('pointerup'));entry.button.dispatchEvent(new Event('click'));
  assert.equal(clicks,1);assert.equal(creates,1);
  win.dispatchEvent(new Event('unload'));assert.equal(releases,1);
  registry.clear();assert.equal(releases,1);
});

test('comparison guards survive toolbar replacement and unrelated refreshes',async()=>{
  const operations=new ComparisonOperations();let resolve,calls=0;
  const task=new Promise(r=>resolve=r);
  const one=operations.run('tab','opening',()=>{calls++;return task;});
  assert.equal(operations.status('tab'),'opening');
  const duplicate=operations.run('tab','closing',()=>assert.fail('Must share pending transition'));
  assert.equal(one,duplicate);
  operations.changed();assert.equal(operations.status('tab'),'opening');
  await Promise.resolve();assert.equal(calls,1);
  resolve();await one;assert.equal(operations.status('tab'),undefined);
});

test('distinct Gecko wrappers for one DOM document reuse the same toolbar',()=>{
  const registry=new ReaderToolbarRegistry(),win=new EventTarget();
  const first={defaultView:win,isSameNode:doc=>doc.identity===1,identity:1};
  const alias={defaultView:win,isSameNode:doc=>doc.identity===1,identity:1};
  const entry=registry.get(first,()=>({button:new EventTarget()}),()=>{});
  assert.equal(registry.get(alias,()=>assert.fail('Must reuse document across compartments'),()=>{}),entry);
  registry.clear();
});

test('failed comparison unlocks every control and allows one clean retry',async()=>{
  const operations=new ComparisonOperations(),states=[];
  operations.listeners.add(()=>states.push(operations.status('tab')));
  await assert.rejects(operations.run('tab','opening',async()=>{throw Error('Missing PDF');}),/Missing PDF/);
  assert.equal(operations.status('tab'),undefined);
  let retry=0;await operations.run('tab','opening',async()=>retry++);
  assert.equal(retry,1);assert.deepEqual(states,['opening',undefined,'opening',undefined]);
});

test('different tabs have independent transitions and registry clears all documents',async()=>{
  const operations=new ComparisonOperations();let finish;
  const slow=operations.run('a','opening',()=>new Promise(r=>finish=r));
  await operations.run('b','opening',async()=>{});
  assert.equal(operations.status('a'),'opening');finish();await slow;
  const registry=new ReaderToolbarRegistry();let releases=0;
  for(let i=0;i<3;i++)registry.get({defaultView:new EventTarget()},()=>({}),()=>releases++);
  registry.clear();assert.equal(releases,3);
});
