const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(
  path.join(__dirname,'../../knowledge_service/web/ingest-results.js'),
  'utf8',
);

function fixture({event='completed',assertions=[],assertionError=null}={}){
  const apiCalls=[],navigationCalls=[],statusCalls=[],intakeCalls=[];

  function element(tag='div'){
    const listeners={};
    const node={tagName:tag.toUpperCase(),children:[],className:'',textContent:'',dataset:{},
      hidden:false,id:'',type:'',title:'',style:{},parentNode:null,
      append(...children){for(const child of children){child.parentNode=this;this.children.push(child);}},
      appendChild(child){this.append(child);return child;},
      replaceChildren(...children){this.children=[];this.append(...children);},
      insertBefore(child,before){child.parentNode=this;const i=this.children.indexOf(before);
        if(i<0)this.children.push(child);else this.children.splice(i,0,child);},
      remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);},
      setAttribute(name,value){this[name]=String(value);},
      addEventListener(name,fn){(listeners[name]??=[]).push(fn);},
      click(){for(const fn of listeners.click??[])fn({preventDefault(){}});},
    };
    node.classList={contains:name=>node.className.split(/\s+/).includes(name)};
    return node;
  }

  const body=element('body'),openButton=element('button');
  const encoder=new TextEncoder();
  let emitted=false;
  const context=vm.createContext({
    window:{
      IngestOntology:{...require('../../knowledge_service/web/ingest-ontology.js'),
        resolveResult:(project,assertions,pinned)=>require('../../knowledge_service/web/ingest-ontology.js')
          .resultRelease(project,assertions,pinned,async()=>({versions:[
            {id:'old-ontology',version:2},{id:'new-ontology',version:4},
          ]}))},
      KnowledgeChat:{splitSseBuffer(buffer){
        if(emitted||!buffer.includes('server-event'))return {events:[],rest:buffer};
        emitted=true;
        const data=event==='completed'?{}:{status:event,error:'boom'};
        return {events:[{event:event==='completed'?'completed':'failed',data}],rest:''};
      }},
      KnowledgeNavigation:{async open(target,options){navigationCalls.push([target,options]);}},
      OntologyIntake:{async open(options){intakeCalls.push(options);}},
    },
    document:{body,createElement:element,getElementById:id=>id==='open-ingest-results'?openButton:null},
    fetch:async()=>({ok:true,status:200,body:{getReader(){let sent=false;return {async read(){
      if(sent)return {done:true};sent=true;return {done:false,value:encoder.encode('server-event')};
    }};}}}),
    api:async url=>{
      apiCalls.push(url);
      if(url.startsWith('/api/jobs/'))return {result:{document:{id:'doc-1'}}};
      if(url.includes('/assertions?')){
        if(assertionError)throw assertionError;
        return {assertions};
      }
      throw Error('Unexpected URL: '+url);
    },
    endpoint:path=>'/api/projects/current-project'+path,
    status:(message,kind)=>statusCalls.push([message,kind]),
    AbortController,TextDecoder,setTimeout,clearTimeout,console,
  });
  vm.runInContext(source,context);

  const drawer=body.children.find(node=>node.id==='ingest-drawer');
  const list=drawer.children.find(node=>node.className==='ingest-drawer__list');
  const all=()=>{
    const result=[];
    const visit=node=>{result.push(node);for(const child of node.children??[])visit(child);};
    visit(drawer);
    return result;
  };
  const text=()=>all().map(node=>node.textContent).filter(Boolean).join('\n');
  const buttons=()=>all().filter(node=>node.tagName==='BUTTON');
  const settle=async()=>{
    for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  };
  return {context,apiCalls,navigationCalls,statusCalls,intakeCalls,list,text,buttons,settle};
}

test('watch keeps the submitting project and mode when loading assertions',async()=>{
  const f=fixture();
  f.context.window.IngestResults.watch('job-1','规则.md',{
    projectId:'task-project',mode:'ontology',
  });
  await f.settle();

  assert.ok(f.apiCalls.includes('/api/projects/task-project/assertions?document_id=doc-1'));
  assert.ok(!f.apiCalls.some(url=>url.startsWith('/api/projects/current-project/assertions')));
  assert.match(f.text(),/使用项目本体 · 正式入图/);
});

test('repeated watch does not overwrite the original task ownership',async()=>{
  const f=fixture();
  f.context.window.IngestResults.watch('job-1','规则.md',{
    projectId:'project-first',mode:'ontology',
  });
  f.context.window.IngestResults.watch('job-1','错误归属.md',{
    projectId:'project-later',mode:'documents',
  });
  await f.settle();

  assert.ok(f.apiCalls.some(url=>url.startsWith('/api/projects/project-first/assertions?')));
  assert.ok(!f.apiCalls.some(url=>url.startsWith('/api/projects/project-later/assertions?')));
  assert.match(f.text(),/使用项目本体 · 正式入图/);
  assert.doesNotMatch(f.text(),/仅文档检索 · 不抽取图谱/);
});

test('empty results explain documents separately from extraction modes',async()=>{
  const documents=fixture();
  documents.context.window.IngestResults.watch('job-doc','资料.md',{
    projectId:'p-doc',mode:'documents',
  });
  await documents.settle();
  assert.match(documents.text(),/仅文档检索 · 不抽取图谱/);
  assert.match(documents.text(),/主动未执行图谱知识抽取/);
  assert.doesNotMatch(documents.text(),/已执行抽取但没有识别出知识/);

  for(const [mode,label] of [
    ['ontology','使用项目本体 · 正式入图'],
    ['discovery','开放本体发现 · 候选暂存'],
  ]){
    const extracted=fixture();
    extracted.context.window.IngestResults.watch('job-'+mode,'资料.md',{projectId:'p-'+mode,mode});
    await extracted.settle();
    assert.match(extracted.text(),new RegExp(label));
    assert.match(extracted.text(),/已执行抽取但没有识别出知识/);
    assert.doesNotMatch(extracted.text(),/主动未执行图谱知识抽取/);
  }
});

test('result cards offer only the next actions supported by their result',async()=>{
  const accepted=fixture({assertions:[{kind:'entity',status:'accepted',payload:{text:'商户',type:'Merchant'}}]});
  accepted.context.window.IngestResults.watch('job-a','a.md',{projectId:'pa',mode:'ontology'});
  await accepted.settle();
  accepted.buttons().find(button=>button.textContent==='去知识台账核对').click();
  assert.deepEqual(JSON.parse(JSON.stringify(accepted.navigationCalls)),[
    ['records',{projectId:'pa'}],
  ]);

  for(const [mode,label,target] of [
    ['ontology','去知识审核','reviews'],
    ['discovery','核对候选与完善结构','ontology-model'],
  ]){
    const pending=fixture({assertions:[{kind:'entity',status:'pending',payload:{text:'商户',proposed_type:'Merchant'}}]});
    pending.context.window.IngestResults.watch('job-'+mode,'a.md',{projectId:'p-'+mode,mode});
    await pending.settle();
    pending.buttons().find(button=>button.textContent===label).click();
    assert.deepEqual(JSON.parse(JSON.stringify(pending.navigationCalls)),[
      [target,{projectId:'p-'+mode}],
    ]);
  }

  const documents=fixture();
  documents.context.window.IngestResults.watch('job-doc','a.md',{projectId:'pd',mode:'documents'});
  await documents.settle();
  documents.buttons().find(button=>button.textContent==='去原文数据源').click();
  assert.deepEqual(JSON.parse(JSON.stringify(documents.navigationCalls)),[
    ['sources',{projectId:'pd'}],
  ]);
});

test('discovery follow-up pins its document and pending candidate identities',async()=>{
  const f=fixture({assertions:[{id:'selected-candidate',kind:'entity',status:'pending',payload:{text:'客户',proposed_type:'Customer'}},{id:'already-accepted',kind:'entity',status:'accepted',payload:{text:'旧客户',type:'Customer'}}]});
  f.context.window.IngestResults.watch('job-discovery','资料.md',{projectId:'owned-project',mode:'discovery'});
  await f.settle();
  f.buttons().find(button=>button.textContent==='核对候选与完善结构').click();await f.settle();
  assert.equal(f.intakeCalls.length,1);assert.equal(f.intakeCalls[0].projectId,'owned-project');
  assert.equal(f.intakeCalls[0].documentId,'doc-1');
  assert.deepEqual(Array.from(f.intakeCalls[0].candidateIds),['selected-candidate']);
});

test('failed tasks and assertion read failures link to the task log',async()=>{
  for(const taskStatus of ['failed','cancelled','interrupted']){
    const failed=fixture({event:taskStatus});
    failed.context.window.IngestResults.watch('job-'+taskStatus,'坏文件.md',{
      projectId:'p-'+taskStatus,mode:'ontology',
    });
    await failed.settle();
    failed.buttons().find(button=>button.textContent==='查看任务日志').click();
    assert.deepEqual(JSON.parse(JSON.stringify(failed.navigationCalls)),[
      ['jobs',{projectId:'p-'+taskStatus}],
    ]);
  }

  const unreadable=fixture({assertionError:Error('该任务所属项目已不存在')});
  unreadable.context.window.IngestResults.watch('job-u','旧文件.md',{projectId:'deleted',mode:'ontology'});
  await unreadable.settle();
  assert.match(unreadable.text(),/结果读取失败：该任务所属项目已不存在/);
  unreadable.buttons().find(button=>button.textContent==='查看任务日志').click();
  assert.deepEqual(JSON.parse(JSON.stringify(unreadable.navigationCalls)),[
    ['jobs',{projectId:'deleted'}],
  ]);
});

test('navigation errors are shown through the existing status surface',async()=>{
  const f=fixture();
  f.context.window.KnowledgeNavigation.open=async()=>{throw Error('该任务所属项目已不存在');};
  f.context.window.IngestResults.watch('job-doc','资料.md',{projectId:'deleted',mode:'documents'});
  await f.settle();
  f.buttons().find(button=>button.textContent==='去原文数据源').click();
  await f.settle();
  assert.deepEqual(f.statusCalls,[['该任务所属项目已不存在',true]]);
});

test('completed batch items open existing results with fixed project and document',async()=>{
  const f=fixture();
  await f.context.window.IngestResults.showBatchItem({id:'item-1',original_name:'规则.md',
    document_id:'batch-doc',status:'completed'}, {projectId:'batch-project',mode:'documents'});
  assert.ok(f.apiCalls.includes('/api/projects/batch-project/assertions?document_id=batch-doc'));
  assert.ok(!f.apiCalls.some(url=>url.startsWith('/api/jobs/')));
  assert.match(f.text(),/主动未执行图谱知识抽取/);
});

test('results show the actual historical ontology instead of the latest release',async()=>{
  const f=fixture({assertions:[{kind:'entity',status:'accepted',ontology_id:'old-ontology',
    payload:{text:'商户',type:'Merchant'}}]});
  f.context.window.IngestResults.watch('job-version','规则.md',{projectId:'p',mode:'ontology',
    ontologyRelease:{id:'new-ontology',version:4}});
  await f.settle();
  assert.match(f.text(),/实际提取本体：本体 v2/);
  assert.match(f.text(),/old-ontology/);
  assert.doesNotMatch(f.text(),/实际提取本体：本体 v4/);
});

test('empty completed batch retains its pinned ontology and unknown legacy results stay unknown',async()=>{
  const f=fixture();
  await f.context.window.IngestResults.showBatchItem({id:'version-item',original_name:'规则.md',document_id:'d'},
    {projectId:'p',mode:'ontology',ontologyId:'old-ontology'});
  assert.match(f.text(),/实际提取本体：本体 v2/);
  const legacy=fixture();
  await legacy.context.window.IngestResults.showBatchItem({id:'legacy',original_name:'旧.md',document_id:'d'},
    {projectId:'p',mode:'ontology'});
  assert.match(legacy.text(),/实际本体版本未知/);
});
