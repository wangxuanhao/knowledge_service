const {test}=require('node:test');
const assert=require('node:assert/strict');
const content=require('../../knowledge_service/web/source-content.js');

const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=';
const pageData=number=>({page_number:number,page_count:3,page_width:300,page_height:400,width:600,height:800,mime_type:'image/png',image_base64:png});
function fixture(fetchOverride){
  class Element{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this.attributes={};this.events={};this.isConnected=true;this.classList={add(){},remove(){}};}
    append(...nodes){for(const node of nodes){node.parentNode=this;this.children.push(node);}}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}removeAttribute(k){delete this.attributes[k];}
    addEventListener(k,fn){(this.events[k]??=[]).push(fn);}removeEventListener(k,fn){this.events[k]=(this.events[k]||[]).filter(item=>item!==fn);}
    replaceWith(node){const at=this.parentNode.children.indexOf(this);this.parentNode.children[at]=node;node.parentNode=this.parentNode;}
    closest(selector){if(selector==='.source-image-canvas'&&this.className==='source-image-canvas')return this;return this.parentNode?.closest(selector)||null;}
    querySelectorAll(selector){const nodes=this.children.flatMap(child=>[child,...child.querySelectorAll(selector)]);
      if(selector==='img'||selector==='img[data-asset-id]')return nodes.filter(n=>n.tagName==='IMG'&&(selector==='img'||n.dataset.assetId));
      if(selector==='video'||selector==='video[data-asset-id]')return [];
      if(selector==='.source-region-focus')return nodes.filter(n=>n.className==='source-region-focus');
      return [];}
    scrollIntoView(){this.scrolled=true;}focus(){this.focused=true;}showModal(){this.open=true;}close(){this.open=false;}remove(){this.isConnected=false;}
  }
  const document={createElement:tag=>new Element(tag)};document.body=new Element('body');
  const host=new Element('article');host.ownerDocument=document;
  const calls=[],revoked=[],blobs=[];
  const session=content.createSession({host,projectId:'p',versionId:'v',fetcher:async(url,options)=>{calls.push({url,options});return fetchOverride?fetchOverride(url,options):{ok:true,json:async()=>pageData(Number(new URL(url,'http://reader.test').searchParams.get('page'))||1),blob:async()=>new Blob(['PDF'],{type:'application/pdf'})};},
    urlApi:{createObjectURL:blob=>{blobs.push(blob);return 'blob:owned-'+blobs.length;},revokeObjectURL:url=>revoked.push(url)}});
  return {host,document,session,calls,revoked,Element};
}

test('PDF detection follows source format and MIME for original and packaged sources',()=>{
  for(const source of [{source_format:'pdf',mime_type:'application/zip'},
    {format:'pdf'},{source_mime_type:'application/pdf'},{mime_type:'application/pdf'},{original_name:'RULE.PDF'},
    {parser_metadata:{source_format:'pdf'},mime_type:'application/zip'}])assert.equal(content.isPdfSource(source),true);
  assert.equal(content.isPdfSource({format:'docx',mime_type:'application/zip'}),false);
});

test('PDF page preview uses authenticated PNG JSON, bounded paging and owned URL cleanup',async()=>{
  const {session,host,calls,revoked}=fixture();
  await session.renderPdf({original_name:'rules.pdf',source_format:'pdf'},2,{source_locations:[{page_no:2,bbox:{l:30,r:90,t:320,b:280,coord_origin:'BOTTOMLEFT'}}]});
  assert.equal(calls[0].url,'/api/projects/p/source-documents/v/pdf-preview?page=2');
  assert.equal(calls[0].options.redirect,'error');
  const toolbar=host.children[0],controls=host.children.find(n=>n.className==='source-pdf-controls');
  const canvas=host.children.find(n=>n.className==='source-pdf-page'),image=canvas.children.find(n=>n.tagName==='IMG');
  assert.equal(image.src,'blob:owned-1');assert.equal(image.width,600);assert.equal(image.height,800);
  session.hydrateImages();assert.equal(calls.length,1,'PDF PNG is already owned and bypasses asset hydration');
  const region=canvas.children.find(n=>n.className==='source-region-focus');
  assert.equal(region.style.top,'20%');image.onload();assert.equal(region.scrolled,true,'native evidence is visible after page pixels load');
  assert.ok(toolbar.children.some(n=>n.download==='rules.pdf'));
  assert.ok(toolbar.children.some(n=>n.textContent==='新窗口打开 PDF'));
  assert.equal(host.children.some(n=>n.tagName==='IFRAME'),false);
  const previous=controls.children.find(n=>n.textContent==='上一页'),next=controls.children.find(n=>n.textContent==='下一页');
  await next.onclick();assert.equal(calls[1].url.endsWith('?page=3'),true);
  assert.equal(next.disabled,true);assert.equal(image.src,'blob:owned-2');assert.deepEqual(revoked,['blob:owned-1']);
  await previous.onclick();assert.equal(image.src,'blob:owned-3');
  const input=controls.children.find(n=>n.tagName==='INPUT');input.value='1';await input.onchange();
  assert.equal(previous.disabled,true);assert.equal(image.src,'blob:owned-4');
  input.value='999';await input.onchange();assert.equal(calls.length,4,'invalid page never requests a render');
  session.dispose();assert.deepEqual(revoked,['blob:owned-1','blob:owned-2','blob:owned-3','blob:owned-4']);
  assert.equal(image.getAttribute('src'),undefined);assert.equal(calls[0].options.signal.aborted,true);
});

test('ZIP primary page preview retains both original and package download names',async()=>{
  const {session,host}=fixture();
  await session.renderPdf({original_name:'upload.zip',format:'zip',mime_type:'application/zip',parser_metadata:{provenance:{package:{filename:'primary.pdf',entry_path:'nested/primary.pdf',mime_type:'application/pdf'}}}},2);
  const toolbar=host.children[0];
  assert.ok(toolbar.children.some(n=>n.download==='primary.pdf'));
  assert.ok(toolbar.children.some(n=>n.download==='upload.zip'));session.dispose();
});

test('late PDF page responses cannot replace a newer page or create URLs after disposal',async()=>{
  const pending=[];
  const {session,host,revoked}=fixture(()=>new Promise(resolve=>pending.push(resolve)));
  const initial=session.renderPdf({original_name:'rule.pdf'},1);
  pending[0]({ok:true,json:async()=>pageData(1)});await initial;
  const controls=host.children.find(n=>n.className==='source-pdf-controls'),input=controls.children.find(n=>n.tagName==='INPUT');
  input.value='2';const older=input.onchange();input.value='3';const newer=input.onchange();
  pending[2]({ok:true,json:async()=>pageData(3)});await newer;
  pending[1]({ok:true,json:async()=>pageData(2)});await older;
  const canvas=host.children.find(n=>n.className==='source-pdf-page'),image=canvas.children.find(n=>n.tagName==='IMG');
  assert.equal(image.alt,'rule.pdf · 第 3 页');assert.equal(image.src,'blob:owned-2');
  input.value='2';const late=input.onchange();session.dispose();pending[3]({ok:true,json:async()=>pageData(2)});await late;
  assert.deepEqual(revoked,['blob:owned-1','blob:owned-2']);
});

test('native PDF regions respect original page dimensions and coordinate origins without using OCR crop boxes',()=>{
  const box=(page_no,coord_origin,t,b)=>({page_no,bbox:{l:30,r:90,t,b,coord_origin}});
  const doc={blocks:[{block_id:'native',payload:{source_locations:[box(2,'BOTTOMLEFT',320,280)]}}]};
  const page={page_number:2,page_width:300,page_height:400};
  assert.deepEqual(content.pdfRegions(doc,{spans:[{block_id:'native'}]},page),[{x:.1,y:.2,width:.2,height:.1}]);
  assert.deepEqual(content.pdfRegions({}, {source_locations:[box(2,'TOPLEFT',80,120)]},page),[{x:.1,y:.2,width:.2,height:.1}]);
  assert.deepEqual(content.pdfRegions(doc,{spans:[{block_id:'native'}]}, {...page,page_number:1}),[]);
  assert.deepEqual(content.pdfRegions({}, {boxes:[{x:.1,y:.2,width:.2,height:.1}]},page),[]);
  assert.deepEqual(content.pdfRegions({}, {source_locations:[box(2,'UNKNOWN',80,120),{...box(2,'TOPLEFT',80,120),bbox:{l:-10,r:90,t:80,b:120,coord_origin:'TOPLEFT'}}]},page),[]);
});

test('OCR evidence scrolls a bounded region after image load and zoom supports width or original size',async()=>{
  const previous=globalThis.location;globalThis.location={origin:'https://reader.test'};
  const {session,host,document,Element,revoked}=fixture();
  try{
    const canvas=new Element('div');canvas.className='source-image-canvas';
    const img=new Element('img');img.dataset.assetId='image-1';img.dataset.assetUrl='/api/projects/p/source-documents/v/assets/image-1';img.naturalWidth=1080;img.naturalHeight=20000;
    canvas.append(img);host.append(canvas);session.hydrateImages();
    assert.equal(session.focusRegion('image-1',[{x:.1,y:.8,width:.8,height:.025}]),true);
    const marker=canvas.children.find(n=>n.className==='source-region-focus');
    assert.equal(marker.style.top,'80%');assert.equal(marker.style.height,'2.5%');assert.equal(canvas.scrolled,undefined);
    await new Promise(setImmediate);img.onload();
    for(const fn of img.events.load||[])fn();
    assert.equal(marker.scrolled,true,'the evidence region, not the whole image, receives scroll focus');
    const wrapper=canvas.children[0],button=wrapper.children[0];button.onclick();
    const dialog=document.body.children.at(-1),toolbar=dialog.children.find(n=>n.className==='source-image-controls');
    const viewport=dialog.children.find(n=>n.className==='source-image-viewport'),zoomed=viewport.children[0];
    assert.ok(toolbar.children.some(n=>n.textContent==='按宽阅读'));
    toolbar.children.find(n=>n.textContent==='原始比例').onclick();assert.equal(zoomed.style.width,'1080px');
    const slider=toolbar.children.find(n=>n.tagName==='LABEL').children.find(n=>n.tagName==='INPUT');slider.value='150';slider.events.input[0]();assert.equal(zoomed.style.width,'1620px');
    toolbar.children.find(n=>n.textContent==='按宽阅读').onclick();assert.equal(zoomed.style.width,'100%');
    session.dispose();assert.equal(revoked.length,2);
  }finally{session.dispose();globalThis.location=previous;}
});
