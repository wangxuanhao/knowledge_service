const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
// app.js 的 status() 现在把提示渲染成**实体卡片**（图标 + 文本 + 关闭按钮），
// 不再是"往一个节点写 textContent"。所以这个用例要给它一个够用的假 DOM：
//   · 假节点要支持 replaceChildren / append / classList.toggle(name, on) / setAttribute / addEventListener；
//   · 断言"提示说了什么"要去卡片的文本节点里看，而不是 element.textContent。
// 要验的策略没变：成功 → 4s 后自动消失；错误与进行中 → 一直留着；新提示顶掉旧的计时器。
function fakeNode(tag='div'){
  const node={tagName:tag,children:[],attributes:{},textContent:'',className:'',
    classList:{_set:new Set(),
      add(name){this._set.add(name);},remove(name){this._set.delete(name);},
      contains(name){return this._set.has(name);},
      toggle(name,on){if(on===undefined){this._set.has(name)?this._set.delete(name):this._set.add(name);}
        else if(on){this._set.add(name);}else{this._set.delete(name);}}},
    setAttribute(name,value){node.attributes[name]=String(value);},
    getAttribute(name){return name in node.attributes?node.attributes[name]:null;},
    append(...kids){node.children.push(...kids);},
    replaceChildren(...kids){node.children=kids;},
    addEventListener(){},remove(){},
    // 提示卡片的文本：卡片结构是 [图标, 文本, 关闭]
    cardText(){const card=node.children[0];return card&&card.children[1]?card.children[1].textContent:'';},
  };
  return node;
}

test('success expires, errors and progress persist, newer notices cancel older timers',()=>{
  const source=fs.readFileSync('knowledge_service/web/app.js','utf8');
  const code=source.slice(source.indexOf('let statusTimer;'),source.indexOf('async function api'));
  const timers=new Map();let id=0;
  const element=fakeNode();
  const context=vm.createContext({
    $:()=>element,
    document:{createElement:tag=>fakeNode(tag)},
    setTimeout:(fn,ms)=>{assert.equal(ms,4000);timers.set(++id,fn);return id;},
    clearTimeout:n=>timers.delete(n),
  });
  vm.runInContext(code,context);

  context.status('项目已切换');
  assert.equal(timers.size,1);
  assert.equal(element.cardText(),'项目已切换');
  const expire=[...timers.values()][0];timers.clear();expire();
  assert.equal(element.children.length,0,'成功提示到期后要自己让位（不能被当成常驻）');

  context.status('保存成功');context.status('连接失败',true);
  assert.equal(timers.size,0);
  assert.equal(element.cardText(),'连接失败');
  assert.ok(element.classList.contains('error'));

  context.status('处理中…');
  assert.equal(timers.size,0,'进行中的提示不设自动消失');

  context.status('项目已切换');context.status('已完成');
  assert.equal(timers.size,1,'新提示只留一个计时器（旧的要被清掉）');
});
