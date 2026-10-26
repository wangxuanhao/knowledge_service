const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  const nodes = [];
  const element = tag => {
    const node = {tag, children:[], attrs:{}, listeners:{}, dataset:{}, parent:null, textContent:'',
      get isConnected() { return this === root || !!this.parent?.isConnected; },
      append(...items) { for (const item of items) { item.parent=this; this.children.push(item); } },
      replaceChildren(...items) { this.children.forEach(item=>item.parent=null); this.children=[]; this.append(...items); },
      setAttribute(name,value) { this.attrs[name]=value; }, removeAttribute(name) { delete this.attrs[name]; },
      addEventListener(name,handler) { this.listeners[name]=handler; }, focus() {},
      querySelector(selector) { return all(this).find(item=>item.tag===selector); },
    };
    nodes.push(node); return node;
  };
  const all = node => [node,...node.children.flatMap(all)];
  const root = element('root');
  const requests=[];
  const context={current:'p',window:{OntologyModel:{S:{}}},console,
    document:{createElement:element,getElementById:id=>id==='tab-ontology-model'?root:null,body:root},
    endpoint:suffix=>'/'+context.current+suffix,
    api:async url=>{ if (!url.endsWith('/ontologies')) return {};
      return new Promise(resolve=>requests.push({url,resolve})); },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../knowledge_service/web/ontology-model-panel.js'),'utf8'),context);
  const panel=context.window.OntologyPanel;
  panel.mount(null,null,context.window.OntologyModel);
  return {root,requests,panel,all,context,mount:()=>panel.mount(null,null,context.window.OntologyModel)};
}

test('history drawer mounts into the current skeleton after a project switch',async()=>{
  const f=fixture();
  const first=f.panel.openHistory();
  f.requests[0].resolve({versions:[]}); await first;
  const old=f.root.children[0];
  f.root.replaceChildren(); f.context.current='next'; f.mount();
  const second=f.panel.openHistory();
  f.requests.at(-1).resolve({versions:[]}); await second;
  assert.equal(old.isConnected,false);
  assert.equal(f.root.children.length,1,'a new connected overlay must replace the detached cache');
  assert.equal(f.root.children[0].isConnected,true);
  assert.equal(f.root.children[0].attrs.hidden,undefined);
});

test('a new project history request does not reuse a previous project in-flight promise',async()=>{
  const f=fixture();
  const first=f.panel.openHistory();
  f.root.replaceChildren(); f.context.current='next'; f.mount();
  const second=f.panel.openHistory();
  assert.deepEqual(f.requests.map(item=>item.url),['/p/ontologies','/next/ontologies']);
  f.requests[1].resolve({versions:[]}); await second;
  f.requests[0].resolve({versions:[{id:'old-project-version',version:1}]}); await first;
  assert.ok(!f.all(f.root).some(item=>String(item.textContent).includes('old-project-version')));
});
test('historical publication retains each source outcome and its skip reason',async()=>{
  const f=fixture(),opening=f.panel.openHistory();
  f.requests[0].resolve({versions:[{id:'published',version:3,metadata:{publication:{receipt:{facts:{selected:5,added:2,reused:1,skipped:2,per_batch:[
    {run_id:'source-run-1',selected:2,added:1,reused:1,skipped:0,candidate_outcomes:[{candidate_id:'candidate-a',status:'materialized',reason_code:'materialized'}]},
    {run_id:'source-run-2',selected:3,added:1,reused:0,skipped:2,candidate_outcomes:[{candidate_id:'candidate-b',status:'skipped',reason_code:'required_operation_rejected'}]},
  ]},index:{status:'pending',task_id:'projection-1'}}}}}]});await opening;
  const nodes=f.all(f.root);assert.ok(nodes.some(node=>node.textContent==='本次发布实际处理的关联知识'));
  assert.equal(nodes.filter(node=>node.className==='om-publication-outcome').length,0);
  const batch=nodes.filter(node=>node.className==='om-publication-batch')[1];assert.ok(!batch.open);
  batch.open=true;batch.listeners.toggle();
  assert.ok(f.all(f.root).some(node=>String(node.textContent).includes('source-run-2')));
  assert.ok(f.all(f.root).some(node=>String(node.textContent).includes('candidate-b')));
  assert.ok(f.all(f.root).some(node=>String(node.textContent).includes('required_operation_rejected')));
});
