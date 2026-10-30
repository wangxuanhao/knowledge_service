const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const file=path.join(__dirname,'../../knowledge_service/web/entity-mindmap-export.js');
const exporter=fs.existsSync(file)?require(file):{};

function fixture(){
  return {id:'root',entityId:'a',name:'根 <甲> & "乙"',type:'Merchant',collapsed:true,children:[
    {id:'child',entityId:'a',name:'隐藏节点',type:'Rule',predicate:'cites',collapsed:true,children:[
      {id:'leaf',entityId:'c',name:'最后的后代',type:'Rule',predicate:'refers',children:[]},
    ]},
  ]};
}

test('canonical SVG contains all loaded descendants and translated relations regardless of collapse',()=>{
  assert.equal(typeof exporter.buildSvg,'function');
  const tree=fixture(),before=JSON.stringify(tree);
  const options={truncated:true,labelOf:value=>({Merchant:'商户',Rule:'规则',cites:'引用',refers:'参照'}[value]||value),colorFor:()=> '#548e88'};
  const result=exporter.buildSvg(tree,options);
  assert.equal(result.count,3);
  assert.ok(result.width>0&&result.height>0);
  assert.match(result.svg,/^<\?xml[^>]*\?>\s*<svg[^>]*xmlns="http:\/\/www.w3.org\/2000\/svg"/);
  assert.match(result.svg,/实体关系脑图/);
  assert.match(result.svg,/已加载 3 个节点/);
  assert.match(result.svg,/部分结果/);
  for(const text of ['隐藏节点','最后的后代','引用','参照','商户','规则'])assert.ok(result.svg.includes(text),text);
  assert.match(result.svg,/fill="#ffffff"/);
  assert.equal(JSON.stringify(tree),before);
  assert.deepEqual(exporter.buildSvg(tree,options),result,'canonical export must be deterministic');
});

test('SVG safely escapes XML text and rejects style injection or external content',()=>{
  assert.equal(typeof exporter.buildSvg,'function');
  const tree=fixture();
  tree.children[0].name='<script>alert("x")</script>\u0000';
  const result=exporter.buildSvg(tree,{labelOf:value=>value,colorFor:()=> 'red"/><image href="https://secret.invalid/"/>'});
  assert.match(result.svg,/根 &lt;甲&gt; &amp; &quot;乙&quot;/);
  assert.match(result.svg,/&lt;script&gt;/);
  assert.doesNotMatch(result.svg,/<script|<image|<foreignObject|https:\/\/secret|onload=|url\(|\u0000/i);
  assert.doesNotMatch(result.svg,/minimap|search-highlight|trace-highlight/);
});

test('long names and predicates wrap without losing their label meaning',()=>{
  assert.equal(typeof exporter.buildSvg,'function');
  const name='这是需要完整导出的非常长实体名称包含多行中文和ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  const relation='需要完整保留的很长关系谓词ABCDEFGHIJKL';
  const result=exporter.buildSvg({id:'root',name,children:[{id:'child',name:'节点',predicate:relation,children:[]}]});
  const text=result.svg.replace(/<[^>]+>/g,'');
  assert.ok(text.includes(name),'name is preserved across text spans');
  assert.ok(text.includes(relation),'predicate is preserved across text spans');
  assert.doesNotMatch(result.svg,/…/);
  const cards=[...result.svg.matchAll(/<g data-node-id="([^"]+)"[^>]*>[\s\S]*?<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)];
  assert.equal(cards.length,2);
  for(const card of cards){
    assert.ok(Number(card[2])>=0&&Number(card[3])>=0);
    assert.ok(Number(card[2])+Number(card[4])<=result.width);
    assert.ok(Number(card[3])+Number(card[5])<=result.height);
  }
});

test('download rejects empty snapshots and checks freshness before producing output',async()=>{
  assert.equal(typeof exporter.download,'function');
  await assert.rejects(()=>exporter.download(null,'svg'),/脑图|空/);
  await assert.rejects(()=>exporter.download({tree:fixture()},'pdf'),/格式/);
  await assert.rejects(()=>exporter.download({tree:fixture()},'svg',{isCurrent:()=>false}),error=>error.name==='AbortError');
});

// Browser primitives are isolated here; the real browser suite verifies actual PNG pixels.
function browserExport({manualImage=false,emptyBlob=false,failAnchor=false,measureText}={}){
  const objects=new Map(),revoked=[],downloads=[],images=[],draws=[];
  let nextUrl=0;
  const browser={EntityMindmapModel:require('../../knowledge_service/web/entity-mindmap-model.js'),Blob,
    URL:{createObjectURL(blob){const url='blob:export-'+(++nextUrl);objects.set(url,blob);return url;},revokeObjectURL(url){revoked.push(url);}},
    setTimeout(callback){callback();},
    Image:class{
      constructor(){images.push(this);}
      set src(url){this.url=url;if(!manualImage)queueMicrotask(()=>this.onload());}
    },
    document:{body:{appendChild(){}},createElement(tag){
      if(tag==='a'){
        if(failAnchor)throw new Error('anchor creation failed');
        return {click(){downloads.push({filename:this.download,blob:objects.get(this.href)});},remove(){}};
      }
      if(tag==='canvas')return {getContext(){return {fillRect(){},drawImage(...args){draws.push(args);},measureText:measureText&&function(value){return measureText(value,this.font);}};},toBlob(callback){callback(emptyBlob?null:new Blob(['png'],{type:'image/png'}));}};
      throw new Error('Unexpected element '+tag);
    }},
  };
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{window:browser});
  return {api:browser.EntityMindmapExport,objects,revoked,downloads,images,draws};
}

test('browser SVG download uses canonical SVG and releases its object URL',async()=>{
  const browser=browserExport(),snapshot={tree:fixture(),truncated:true};
  const result=await browser.api.download(snapshot,'svg',{filename:'我的脑图.png'});
  assert.equal(browser.downloads.length,1);
  assert.equal(browser.downloads[0].filename,'我的脑图.svg');
  assert.equal(await browser.downloads[0].blob.text(),browser.api.buildSvg(snapshot.tree,{truncated:true}).svg);
  assert.equal(result.count,3);
  assert.deepEqual(browser.revoked,[...browser.objects.keys()]);
});

test('PNG download scales the whole canonical image within side and pixel bounds',async()=>{
  const tree={id:'0',name:'根节点',children:[]};let tail=tree;
  for(let i=1;i<=30;i++){const child={id:String(i),name:'层级 '+i,children:[]};tail.children.push(child);tail=child;}
  for(let i=0;i<200;i++)tail.children.push({id:'leaf-'+i,name:'后代 '+i,children:[]});
  const browser=browserExport(),result=await browser.api.download({tree},'png');
  assert.equal(result.count,231);
  assert.ok(result.width<=8192&&result.height<=8192);
  assert.ok(result.width*result.height<=16000000);
  assert.ok(Math.abs(result.width/result.sourceWidth-result.height/result.sourceHeight)<.001);
  assert.deepEqual(browser.draws[0].slice(1),[0,0,result.width,result.height]);
  assert.match(await browser.objects.get(browser.images[0].url).text(),/data-node-id="leaf-199"/);
  assert.equal(browser.downloads[0].blob.type,'image/png');
  assert.deepEqual(browser.revoked,[...browser.objects.keys()]);
});

test('an export invalidated during rasterization never downloads stale data',async()=>{
  const browser=browserExport({manualImage:true});let current=true;
  const pending=browser.api.download({tree:fixture()},'png',{isCurrent:()=>current});
  current=false;browser.images[0].onload();
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(browser.downloads.length,0);
  assert.deepEqual(browser.revoked,[...browser.objects.keys()]);
});

test('rasterization and anchor creation errors surface while releasing object URLs',async()=>{
  for(const settings of [{emptyBlob:true},{failAnchor:true}]){
    const browser=browserExport(settings);
    await assert.rejects(()=>browser.api.download({tree:fixture()},settings.emptyBlob?'png':'svg'),/PNG|anchor creation failed/);
    assert.equal(browser.downloads.length,0);
    assert.deepEqual(browser.revoked,[...browser.objects.keys()]);
  }
});

function wideTextFixture(){
  return {id:'root',name:'W'.repeat(31),type:'W'.repeat(42),children:[
    {id:'child',name:'W'.repeat(31),type:'W'.repeat(42),predicate:'W'.repeat(42),children:[]},
  ]};
}
function wideTextLines(svg){
  const lines=[];
  for(const match of svg.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/g)){
    const size=Number(match[1].match(/font-size="([\d.]+)"/)?.[1]);
    for(const span of match[2].matchAll(/<tspan\b[^>]*>(W+)<\/tspan>/g))lines.push({text:span[1],size});
  }
  return lines;
}

test('wide Latin glyphs fit card interiors with deterministic non-DOM wrapping',()=>{
  const result=exporter.buildSvg(wideTextFixture()),lines=wideTextLines(result.svg);
  assert.ok(lines.length>5,'wide labels require more than one line');
  assert.equal(lines.reduce((sum,line)=>sum+line.text.length,0),31*2+42*3,'every name, type and predicate character is preserved');
  assert.ok(lines.every(line=>line.text.length*line.size<=244),JSON.stringify(lines));
});

test('browser wrapping measures exact declared fonts including glyph overhang',()=>{
  const measuredFonts=new Set(),browser=browserExport({measureText(value,font){
    measuredFonts.add(font);
    const size=Number(font.match(/([\d.]+)px/)?.[1]);
    return {width:value.length*size,actualBoundingBoxLeft:0,actualBoundingBoxRight:value.length*size+3};
  }});
  const result=browser.api.buildSvg(wideTextFixture()),lines=wideTextLines(result.svg);
  assert.ok(measuredFonts.size>=3,'name, predicate and type styles must each be measured');
  assert.ok([...measuredFonts].some(font=>/^600 15px /.test(font)));
  assert.ok([...measuredFonts].some(font=>/^600 11px /.test(font)));
  assert.ok([...measuredFonts].some(font=>/^400 11px /.test(font)));
  assert.ok(lines.every(line=>line.text.length*line.size+3<=244),JSON.stringify(lines));
});
