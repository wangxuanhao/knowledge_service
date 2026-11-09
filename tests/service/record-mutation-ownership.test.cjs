const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('a completed restore never writes its version into another record editor',async()=>{
  const callbacks={},fields={'revision-id':{value:'a'},'restore-number':{value:'1'},'revision-version':{value:'4'}};
  let finish;
  const context={window:{KnowledgeChanges:{notify:async()=>false}},current:'p',
    $:id=>fields[id],bind:(id,fn)=>callbacks[id]=fn,endpoint:suffix=>'/api/projects/p'+suffix,
    api:()=>new Promise(resolve=>finish=resolve),status(){},console};
  vm.createContext(context);
  const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workbench.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('async function committedKnowledgeChange('),source.indexOf("bind('load-operations',operations)")),context);
  const pending=callbacks['restore-version']();
  fields['revision-id'].value='b';fields['revision-version'].value='9';
  finish({id:'a',version:5});await pending;
  assert.equal(fields['revision-version'].value,'9');
});

test('a deferred page refresh reruns after a newer mutation generation',async()=>{
  let finish,calls=0;
  const context={window:{},knowledgeGraphRefresh:null,knowledgeRefreshGeneration:1,changedGraph:{},
    get:()=>({classList:{contains:()=>false}}),refreshChangedGraph:async()=>{
      if(++calls===1)await new Promise(resolve=>finish=resolve);
      else context.changedGraph=null;
      return true;
    }};
  vm.createContext(context);
  const source=fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/workspace.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('  window.refreshKnowledgeGraph='),source.indexOf('  window.clearGraphWorkspace=')),context);
  const pending=context.window.refreshKnowledgeGraph();
  context.knowledgeRefreshGeneration=2;
  const second=context.window.refreshKnowledgeGraph();finish();
  await Promise.all([pending,second]);assert.equal(calls,2);
});
