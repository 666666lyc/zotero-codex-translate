import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {build} from 'esbuild';
import path from 'node:path';
const bundle = await build({entryPoints:['src/modules/runtime.ts'],bundle:true,write:false,format:'cjs'});
function fixture(record,exists=true){
  const calls=[];const root='/data/codex-translate/runtime';
  const context={module:{exports:{}},exports:{},setTimeout,clearTimeout,
    Services:{appinfo:{OS:'Linux',XPCOMABI:'x86_64-gcc3'}},
    Zotero:{DataDirectory:{dir:'/data'},HTTP:{request:async()=>({responseText:JSON.stringify({bridgeVersion:'0.1.1'})})}},
    ztoolkit:{getGlobal:name=>name==='PathUtils'?{join:path.posix.join,parent:path.posix.dirname}:{exists:async()=>exists,readJSON:async()=>record}},
    ChromeUtils:{importESModule:()=>({Subprocess:{getEnvironment:()=>({}),call:async options=>{
      calls.push(options);return {stdin:{close:async()=>{}},stdout:{readString:async()=>''},wait:async()=>({exitCode:0}),kill:async()=>{}};
    }}})},
  };
  context.exports=context.module.exports;vm.runInNewContext(bundle.outputFiles[0].text,context);
  return {runtime:context.module.exports.runtime,calls,root};
}
const valid={runtimeRoot:'/data/codex-translate/runtime',python:'/data/codex-translate/runtime/linux-env/bin/python',resourceDir:'/data/codex-translate/engine-assets',bridgeVersion:'0.1.1'};
test('Linux validates native imports and resource hashes before accepting a runtime',async()=>{
  const {runtime,calls}=fixture(valid);const config=await runtime.ensureReady();
  assert.equal(config.command,valid.python);assert.equal(calls.length,1);
  assert.match(calls[0].arguments[2],/verify_only=True/);
  assert.equal(runtime.status().stage,'ready');
});
test('Linux rejects a migrated or external Python pointer without executing it',async()=>{
  const {runtime,calls}=fixture({...valid,python:'/tmp/untrusted/python'});
  await assert.rejects(runtime.ensureReady(),/路径或桥接版本/);assert.equal(calls.length,0);
});
test('Linux explains missing installation without trying Windows downloads',async()=>{
  const {runtime,calls}=fixture(null,false);
  await assert.rejects(runtime.ensureReady(),/install_linux.py/);assert.equal(calls.length,0);
});
