const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
test('success expires, errors and progress persist, newer notices cancel older timers',()=>{
  const source=fs.readFileSync('knowledge_service/web/app.js','utf8');
  const code=source.slice(source.indexOf('let statusTimer;'),source.indexOf('async function api'));
  const timers=new Map();let id=0;
  const element={textContent:'',classList:{toggle(){},remove(){}}};
  const context=vm.createContext({$:()=>element,setTimeout:(fn,ms)=>{assert.equal(ms,4000);timers.set(++id,fn);return id;},clearTimeout:n=>timers.delete(n)});
  vm.runInContext(code,context);
  context.status('项目已切换');assert.equal(timers.size,1);
  const expire=[...timers.values()][0];timers.clear();expire();assert.equal(element.textContent,'');
  context.status('保存成功');context.status('连接失败',true);assert.equal(timers.size,0);assert.equal(element.textContent,'连接失败');
  context.status('处理中…');assert.equal(timers.size,0);
  context.status('项目已切换');context.status('已完成');assert.equal(timers.size,1);
});
