import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {build} from 'esbuild';
const bundle=await build({entryPoints:['src/modules/comparisonSync.ts'],bundle:true,write:false,format:'cjs'});
const context={module:{exports:{}},exports:{}};context.exports=context.module.exports;
vm.runInNewContext(bundle.outputFiles[0].text,context);
const {ComparisonSync,readPageAnchor,applyPageAnchor}=context.module.exports;
function viewer(scale=1,rotation=0){
  const container=new EventTarget();
  Object.assign(container,{clientWidth:600,clientHeight:800,scrollTop:0,scrollLeft:0,getBoundingClientRect:()=>({top:0,left:0}),
    scrollTo(left,top){this.scrollTop=top;this.scrollLeft=left;this.dispatchEvent(new Event('scroll'));}});
  const width=(rotation===90?1000:600)*scale,height=(rotation===90?600:1000)*scale;
  const viewport={width,height,viewBox:[0,0,600,1000],
    convertToPdfPoint:(x,y)=>rotation===90?[y/scale,x/scale]:[x/scale,1000-y/scale],
    convertToViewportPoint:(x,y)=>rotation===90?[y*scale,x*scale]:[x*scale,(1000-y)*scale]};
  const pages=Array.from({length:3},(_,i)=>({viewport,div:{getBoundingClientRect:()=>({left:-container.scrollLeft,top:i*(height+20)-container.scrollTop,bottom:i*(height+20)+height-container.scrollTop,width,height})}}));
  return {container,pagesCount:3,currentPageNumber:1,getPageView:i=>pages[i]};
}
function same(a,b,horizontal=true){assert.equal(a.page,b.page);if(horizontal)assert.ok(Math.abs(a.x-b.x)<.003);assert.ok(Math.abs(a.y-b.y)<.003);}
test('PDF coordinates map positions across scale and rotation without coupling zoom',()=>{
  const a=viewer(),b=viewer(1.5,90);a.container.scrollTo(0,1150);
  const anchor=readPageAnchor(a);applyPageAnchor(b,anchor);same(readPageAnchor(b),anchor);
  assert.equal(b.getPageView(0).viewport.width,1500);
});
test('either pane takes control, generated scroll cannot echo, rapid input wins, disposal cancels work',()=>{
  const views={left:viewer(),right:viewer(1.4)},docs={left:new EventTarget(),right:new EventTarget()},frames=new Map();
  let next=0,enabled=true,writes=0;
  for(const v of Object.values(views)){const write=v.container.scrollTo;v.container.scrollTo=function(...args){writes++;write.apply(this,args);};}
  const options={window:{requestAnimationFrame:cb=>{frames.set(++next,cb);return next;},cancelAnimationFrame:id=>frames.delete(id)},initial:'left',viewer:s=>views[s],documents:s=>[docs[s]],enabled:()=>enabled,active:()=>{}};
  const flush=()=>{const pending=[...frames.values()];frames.clear();for(const cb of pending)cb();};
  const sync=new ComparisonSync(options);flush();
  docs.left.dispatchEvent(new Event('wheel'));views.left.container.scrollTo(0,400);flush();same(readPageAnchor(views.left),readPageAnchor(views.right),false);
  docs.right.dispatchEvent(new Event('pointerdown'));views.right.container.scrollTo(0,1650);flush();same(readPageAnchor(views.left),readPageAnchor(views.right),false);
  assert.equal(frames.size,0,'target must not produce a feedback loop');
  docs.left.dispatchEvent(new Event('keydown'));views.left.container.scrollTo(0,700);
  docs.right.dispatchEvent(new Event('keydown'));views.right.container.scrollTo(0,1750);flush();same(readPageAnchor(views.left),readPageAnchor(views.right),false);
  enabled=false;views.right.container.scrollTo(0,500);const before=views.left.container.scrollTop;flush();assert.equal(views.left.container.scrollTop,before);
  enabled=true;sync.changed('right');sync.dispose();const count=writes;flush();assert.equal(writes,count);assert.equal(frames.size,0);
});
