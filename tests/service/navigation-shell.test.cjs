const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Node extends EventTarget {
  constructor(tag='div',text=''){super();this.tagName=tag;this.textContent=text;this.children=[];this.attributes={};this.dataset={};this.hidden=false;this.classes=new Set();this.classList={add:(...names)=>names.forEach(n=>this.classes.add(n)),remove:(...names)=>names.forEach(n=>this.classes.delete(n)),contains:n=>this.classes.has(n),toggle:(n,on)=>{on=on??!this.classes.has(n);on?this.classes.add(n):this.classes.delete(n);return on;}};}
  set className(value){this.classes=new Set(value.split(' '));} get className(){return [...this.classes].join(' ');}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='id')this.id=v;if(k==='class')this.className=v;}
  getAttribute(k){return this.attributes[k]??null;}
  removeAttribute(k){delete this.attributes[k];}
  append(...nodes){for(const n of nodes){if(n.parentElement)n.parentElement.children=n.parentElement.children.filter(x=>x!==n);n.parentElement=this;this.children.push(n);}}
  prepend(node){this.append(node);this.children.unshift(this.children.pop());}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  matches(selector){return selector==='button[data-tab]'?this.tagName==='button'&&!!this.dataset.tab:selector==='.workspace-icon'?this.classes.has('workspace-icon'):selector.startsWith('#')?this.id===selector.slice(1):selector.startsWith('.')?this.classes.has(selector.slice(1)):this.tagName===selector;}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  focus(){this.focused=true;}
  click(){this.dispatchEvent(new Event('click'));}
}
function setup({saved=null,storageFails=false,mobile=false}={}){
  const body=new Node('body'),aside=new Node('aside'),nav=new Node('nav'),projectLabel=new Node('label','当前知识项目'),project=new Node('select'),session=new Node('div');
  project.id='project';session.id='session';projectLabel.append(project);body.append(aside);aside.append(new Node('div'),projectLabel,nav,session);
  const buttons=['qa','search','sources','ingest'].map(name=>{const b=new Node('button',name);b.dataset.tab=name;nav.append(b);return b;});buttons[1].classList.add('active');buttons[2].hidden=true;
  const document=new Node('document');document.body=body;document.readyState='complete';document.createElement=tag=>new Node(tag);document.createElementNS=(_,tag)=>new Node(tag);document.querySelector=selector=>selector==='body>aside'?aside:body.querySelector(selector);document.getElementById=id=>body.querySelector('#'+id);
  const media=new EventTarget();media.matches=!mobile;media.addListener=listener=>media.addEventListener('change',listener);
  const writes=[];const window={matchMedia:()=>media,localStorage:{getItem(){if(storageFails)throw Error('denied');return saved;},setItem(k,v){if(storageFails)throw Error('denied');writes.push([k,v]);}}};
  const observers=[];
  const context={window,document,Event,MutationObserver:class{constructor(callback){this.callback=callback;observers.push(this);}observe(){}},console};
  for(const name of ['workspace-icons.js','navigation-shell.js']){const file=path.join(__dirname,'../../knowledge_service/web',name);if(fs.existsSync(file))vm.runInNewContext(fs.readFileSync(file,'utf8'),context);}
  return {window,document,body,aside,buttons,project,projectLabel,session,media,writes,observers};
}
test('collapse preserves original destinations, listeners, selected tab and role visibility',()=>{
  const f=setup();let clicked=0;f.buttons[1].addEventListener('click',()=>clicked++);
  const toggle=f.document.getElementById('workspace-nav-collapse');assert.ok(toggle,'desktop collapse control exists');toggle.click();
  assert.equal(f.body.classList.contains('workspace-nav-compact'),true);
  assert.equal(toggle.getAttribute('aria-expanded'),'false');
  assert.equal(f.aside.querySelectorAll('button[data-tab]')[1],f.buttons[1]);
  f.buttons[1].click();assert.equal(clicked,1);assert.equal(f.buttons[1].classList.contains('active'),true);assert.equal(f.buttons[2].hidden,true);
  for(const button of f.buttons){assert.ok(button.getAttribute('aria-label'));assert.ok(button.getAttribute('title'));assert.ok(button.querySelector('.workspace-icon'));}
  assert.equal(f.writes.at(-1)[1],'true');
  assert.equal(f.buttons[1].getAttribute('aria-current'),'page');
  f.buttons[1].classList.remove('active');f.buttons[0].classList.add('active');f.observers.forEach(observer=>observer.callback());
  assert.equal(f.buttons[1].getAttribute('aria-current'),null);assert.equal(f.buttons[0].getAttribute('aria-current'),'page');
});
test('saved desktop collapse is suspended on mobile and restored after resize',()=>{
  const f=setup({saved:'true'});assert.equal(f.body.classList.contains('workspace-nav-compact'),true);
  f.media.matches=false;f.media.dispatchEvent(new Event('change'));
  assert.equal(f.body.classList.contains('workspace-nav-compact'),false);assert.equal(f.projectLabel.hidden,false);
  f.media.matches=true;f.media.dispatchEvent(new Event('change'));assert.equal(f.body.classList.contains('workspace-nav-compact'),true);
});
test('blocked storage still allows toggling and project popover preserves real selector',()=>{
  const f=setup({storageFails:true});const toggle=f.document.getElementById('workspace-nav-collapse');assert.ok(toggle);toggle.click();
  const projectToggle=f.document.getElementById('workspace-project-toggle');assert.ok(projectToggle);projectToggle.click();
  assert.equal(projectToggle.getAttribute('aria-expanded'),'true');assert.equal(f.project.parentElement,f.projectLabel);assert.equal(f.projectLabel.classList.contains('workspace-project-open'),true);
  const escape=new Event('keydown');escape.key='Escape';f.document.dispatchEvent(escape);
  assert.equal(projectToggle.getAttribute('aria-expanded'),'false');assert.equal(projectToggle.focused,true);
  toggle.click();assert.equal(f.body.classList.contains('workspace-nav-compact'),false);
});
test('shared icons use local accessible SVG and decorate without replacing button content',()=>{
  const f=setup();assert.ok(f.window.WorkspaceIcons);
  for(const name of ['search','sliders','filter','expand','settings','zoom-in','zoom-out','maximize','panel','plus','refresh','route','network','locate','chevron']){
    const icon=f.window.WorkspaceIcons.create(name);assert.equal(icon.tagName,'svg');assert.equal(icon.getAttribute('aria-hidden'),'true');assert.equal(icon.getAttribute('stroke'),'currentColor');assert.ok(icon.children.length,name);
  }
  const button=new Node('button','Original label'),child=new Node('span','keep');button.append(child);
  f.window.WorkspaceIcons.decorate(button,'search');f.window.WorkspaceIcons.decorate(button,'filter');
  assert.equal(button.querySelectorAll('.workspace-icon').length,1);assert.equal(button.contains(child),true);
});
test('asynchronous account rendering keeps original password/logout controls and handlers',()=>{
  const f=setup({saved:'true'}),who=new Node('button','Alice 管理员'),password=new Node('button','改口令'),logout=new Node('button','退出登录');
  who.className='session-who';let changes=0,exits=0;password.addEventListener('click',()=>changes++);logout.addEventListener('click',()=>exits++);
  f.session.append(who,password,logout);f.observers.forEach(observer=>observer.callback());
  assert.match(who.getAttribute('aria-label'),/Alice.*账户菜单/);assert.equal(f.session.children[0],who);
  password.click();logout.click();assert.equal(changes,1);assert.equal(exits,1);
});
test('initial phone render ignores compact preference without changing the existing menu',()=>{
  const f=setup({saved:'true',mobile:true});assert.equal(f.body.classList.contains('workspace-nav-compact'),false);
  assert.equal(f.project.parentElement,f.projectLabel);assert.equal(f.buttons[1].classList.contains('active'),true);
  assert.equal(f.aside.classList.contains('qa-nav-open'),false);assert.equal(f.writes.length,0);
});
