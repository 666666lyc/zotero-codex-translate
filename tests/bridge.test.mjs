import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { build } from "esbuild";

const bundle = await build({stdin:{contents:'export {Bridge} from "./src/modules/bridge"; export {runtime} from "./src/modules/runtime";',resolveDir:process.cwd(),loader:"ts"},bundle:true,write:false,format:"cjs",target:"es2022"});
const deferred = () => { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
const tick = () => new Promise(resolve=>setImmediate(resolve));
function pipe() {
  const values=[], readers=[];
  return {readString(){if(values.length)return Promise.resolve(values.shift()); const d=deferred();readers.push(d);return d.promise;},push(value){const d=readers.shift();if(d)d.resolve(value);else values.push(value);}};
}
function child({hello=true, exitOnClose=true}={}) {
  const stdout=pipe(),stderr=pipe(),exit=deferred();
  const requests=[];
  const result={stdout,stderr,requests,killed:0,closed:0,
    stdin:{async write(line){const m=JSON.parse(line);requests.push(m); if(m.method==='hello'&&hello)result.reply(m,{protocolVersion:1});},async close(){result.closed++;if(exitOnClose)result.finish();}},
    wait:()=>exit.promise,async kill(){result.killed++;result.finish(1);},
    reply(m,value){stdout.push(JSON.stringify({protocolVersion:1,id:m.id,result:value})+'\n');},
    finish(code=0,{eof=true}={}){if(eof)stdout.push('');stderr.push('');exit.resolve({exitCode:code});},
  };
  return result;
}
function fixture(children=[]) {
  const timers=new Map(); let timerID=0;
  const calls=[],errors=[];
  const context={module:{exports:{}},exports:{},console,
    setTimeout(fn,ms){timers.set(++timerID,{fn,ms});return timerID;},clearTimeout(id){timers.delete(id);},
    Zotero:{Prefs:{get:()=>''},DataDirectory:{dir:'test-data'},logError:e=>errors.push(String(e))},
    ztoolkit:{getGlobal:()=>({join:(...p)=>p.join('/')})},
    ChromeUtils:{importESModule:()=>({Subprocess:{getEnvironment:()=>({}),async call(options){calls.push(options);const next=children.shift();if(next instanceof Error)throw next;return await next;}}})},
  };
  context.exports=context.module.exports;vm.runInNewContext(bundle.outputFiles[0].text,context);
  const {Bridge,runtime}=context.module.exports;
  runtime.ensureReady=async()=>({command:'python.exe',arguments:['-I','-m','zotero_codex_bridge']});
  return {bridge:new Bridge(),runtime,calls,timers,errors,async timeout(ms){for(const [id,t]of timers)if(t.ms===ms){timers.delete(id);t.fn();await tick();return;}throw Error('No timer '+ms);}};
}

test('parallel callers share one handshake',async()=>{
  const p=child(),f=fixture([p]);
  const a=f.bridge.request('jobs.list'),b=f.bridge.request('jobs.list');await tick();
  assert.equal(f.calls.length,1);assert.equal(p.requests.filter(r=>r.method==='hello').length,1);
  for(const r of p.requests.filter(r=>r.method==='jobs.list'))p.reply(r,[]);
  await Promise.all([a,b]);await f.bridge.close();
});
test('delayed EOF from old process cannot reject the new handshake',async()=>{
  const old=child({exitOnClose:false}),next=child({hello:false});const f=fixture([old,next]);
  const first=f.bridge.request('jobs.list');await tick();old.reply(old.requests.at(-1),[]);await first;
  const closing=f.bridge.close();old.finish(0,{eof:false});await closing;
  const fresh=f.bridge.request('jobs.list');const settled=fresh.then(()=> 'ok',e=>String(e));await tick();
  old.stdout.push('');await tick();
  next.reply(next.requests[0],{protocolVersion:1});await tick();
  if(next.requests.some(r=>r.method==='jobs.list'))next.reply(next.requests.at(-1),[]);
  assert.equal(await settled,'ok');await f.bridge.close();
});
test('close cancels an environment waiter without starting a stale process',async()=>{
  const environment=deferred(),p=child(),f=fixture([p]);f.runtime.ensureReady=()=>environment.promise;
  const old=f.bridge.request('jobs.list');const rejected=assert.rejects(old,/关闭|取消/);await tick();await f.bridge.close();await rejected;
  environment.resolve({command:'python.exe',arguments:[]});await tick();assert.equal(f.calls.length,0);
});
test('a process returned after close is stopped and cannot affect its successor',async()=>{
  const spawning=deferred(),old=child(),next=child(),f=fixture([spawning.promise,next]);
  const a=f.bridge.request('jobs.list');const rejected=assert.rejects(a,/关闭|取消/);await tick();const closing=f.bridge.close();await rejected;
  const b=f.bridge.request('jobs.list');await tick();assert.equal(f.calls.length,1);
  spawning.resolve(old);await closing;await tick();assert.ok(old.closed||old.killed);
  next.reply(next.requests.at(-1),[]);await b;await f.bridge.close();
});
test('handshake timeout cleans up and a retry uses a fresh process',async()=>{
  const old=child({hello:false}),next=child(),f=fixture([old,next]);
  const a=f.bridge.request('jobs.list');const rejected=assert.rejects(a,/超时/);await tick();await f.timeout(30000);await rejected;await tick();
  assert.ok(old.closed||old.killed);
  const b=f.bridge.request('jobs.list');await tick();next.reply(next.requests.at(-1),[]);await b;await f.bridge.close();
});
test('startup exit reports its code and sanitized stderr',async()=>{
  const p=child({hello:false,exitOnClose:false}),f=fixture([p]);
  const a=f.bridge.request('jobs.list');const rejected=assert.rejects(a,/退出/);await tick();
  p.stderr.push('Traceback\nRuntimeError: startup failed; Bearer secret-token-value\nOPENAI_API_KEY=secret-api-value\n{"api_key":"secret-json-value"}\n');p.finish(23);await rejected;
  const state=f.bridge.status();assert.equal(state.phase,'failed');assert.equal(state.exitCode,23);
  assert.match(state.details,/startup failed/);assert.doesNotMatch(state.details,/secret-token-value|secret-api-value|secret-json-value/);
});
test('a broken listener cannot break transport or other windows',async()=>{
  const p=child(),f=fixture([p]);f.bridge.listeners.add(()=>{throw Error('dead window');});
  const a=f.bridge.request('jobs.list');await tick();p.reply(p.requests.at(-1),[]);await a;await f.bridge.close();
});
