const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const file=path.resolve(__dirname,'../../knowledge_service/web/library-workspace.js');
function state(options={}){assert.ok(fs.existsSync(file),'library workspace controller must exist');return require(file).createState(options);}
test('closing and reopening preserves the same-project upload step; old completions cannot reopen it',()=>{
  const ui=state({canWrite:()=>true});ui.open('upload','p');ui.step(2);const ticket=ui.ticket();
  ui.close();assert.equal(ui.accepts(ticket,'p'),false);ui.open('upload','p');
  assert.equal(ui.snapshot().step,2);assert.equal(ui.accepts(ticket,'p'),false);
});
test('a late project action cannot replace the new project notice or re-enable its pending button',async()=>{
  const source=fs.readFileSync(path.resolve(__dirname,'../../knowledge_service/web/app.js'),'utf8');
  const block=source.slice(source.indexOf('function bind('),source.indexOf('async function projects('));
  const button={disabled:false},messages=[];let finishA,finishB;
  const context={current:'a',$:()=>button,status:(...args)=>messages.push(args),
    run:()=>new Promise(resolve=>{if(context.current==='a')finishA=resolve;else finishB=resolve;})};
  vm.createContext(context);vm.runInContext(block+"bind('ingest',run)",context);
  const a=button.onclick();context.current='b';const b=button.onclick();finishA();await a;
  assert.equal(button.disabled,true);assert.equal(messages.filter(x=>x[0]==='完成').length,0);
  finishB();await b;assert.equal(button.disabled,false);
});
test('switching project through the shared cleanup clears a draft once and rejects prior tickets',()=>{
  let clears=0;const ui=state({canWrite:()=>true,clear:()=>clears++});
  ui.open('upload','p');ui.step(2);const ticket=ui.ticket();ui.projectChanged('q');
  assert.equal(clears,1);assert.equal(ui.accepts(ticket,'q'),false);assert.equal(ui.snapshot().active,false);
  ui.open('upload','q');assert.equal(clears,1);assert.equal(ui.snapshot().step,0);
});
test('read-only sessions cannot open upload or structured forms, while batch viewing remains bounded by existing route access',()=>{
  const ui=state();assert.throws(()=>ui.open('upload','p'),/管理员/);assert.throws(()=>ui.open('structured','p'),/管理员/);
  assert.equal(ui.open('batches','p').mode,'batches');assert.throws(()=>ui.open('other','p'),/未知/);
});
