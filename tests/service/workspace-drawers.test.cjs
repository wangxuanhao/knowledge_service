const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const file=path.resolve(__dirname,'../../knowledge_service/web/workspace-drawers.js');
function fixture({delayed=false}={}){
  const pending=[];
  class Node{
    constructor(tag){this.tagName=tag;this.children=[];this.events={};this.open=false;this.isConnected=true;}
    append(...items){for(const item of items){item.remove?.();item.parent=this;this.children.push(item);}}
    setAttribute(k,v){this[k]=v;}addEventListener(k,fn){this.events[k]=fn;}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}
    showModal(){this.open=true;}close(){this.open=false;if(delayed)pending.push(()=>this.events.close?.());else this.events.close?.();}
    focus(){document.activeElement=this;}
  }
  const document={createElement:tag=>new Node(tag),body:new Node('body'),activeElement:null};
  assert.ok(fs.existsSync(file),'shared drawer shell must exist');
  const manager=require(file).createManager(document);
  return {document,manager,Node,flush(){while(pending.length)pending.shift()();}};
}
test('moves original controls and restores trigger focus on Escape without destroying content',()=>{
  const {manager,document,Node}=fixture(),trigger=new Node('button'),input=new Node('input');
  input.value='not yet submitted';let closed=0;
  const drawer=manager.create({id:'test',title:'Upload',content:input,onClose:()=>closed++});
  drawer.open({trigger});assert.equal(drawer.dialog.open,true);assert.equal(drawer.body.children[0],input);
  drawer.dialog.events.cancel({preventDefault(){}});
  assert.equal(drawer.dialog.open,false);assert.equal(document.activeElement,trigger);assert.equal(closed,1);
  drawer.open({trigger});assert.equal(input.value,'not yet submitted');
});
test('queued native close events do not invalidate a drawer reopened in the same task',()=>{
  const {manager,Node,flush}=fixture({delayed:true});let closed=0;
  const drawer=manager.create({id:'same',title:'Same',content:new Node('div'),onClose:()=>closed++});
  drawer.open();drawer.close();drawer.open();flush();
  assert.equal(drawer.dialog.open,true);assert.equal(manager.active,drawer);assert.equal(closed,1);
});
test('opening another drawer invalidates the prior one exactly once and keeps new drawer focus',()=>{
  const {manager,document,Node}=fixture();let closed=0;
  const a=manager.create({id:'a',title:'A',content:new Node('div'),onClose:()=>closed++});
  const b=manager.create({id:'b',title:'B',content:new Node('div')});
  const origin=new Node('button');a.open({trigger:origin});b.open();
  assert.equal(a.dialog.open,false);assert.equal(b.dialog.open,true);assert.equal(closed,1);
  assert.notEqual(document.activeElement,origin);
  manager.close({restoreFocus:false});manager.close();assert.equal(closed,1);
});
