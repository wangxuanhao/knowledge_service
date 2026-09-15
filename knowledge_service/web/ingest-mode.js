/* Document input modes keep file content and pasted drafts separate. */
(() => {
  const file=document.getElementById('doc-file'), text=document.getElementById('doc-text');
  const modes=[...document.querySelectorAll('input[name="document-input-mode"]')];
  const mode=()=>modes.find(input=>input.checked).value;
  const refresh=()=>{
    const upload=mode()==='file';
    const batch=upload&&file.files.length>1;
    const title=document.getElementById('doc-title');
    title.disabled=batch;title.required=!batch;
    const list=document.getElementById('doc-file-list');
    if(list){list.hidden=!upload;list.textContent=file.files.length?`已选 ${file.files.length} 个文件\n`+[...file.files].map(f=>f.name).join('\n'):'';}
    const help=document.getElementById('doc-batch-help');
    if(help)help.textContent=batch?'多文件按文件名分别生成标题；下方发布时间和 Metadata 为本批文件共用，发布时间不同请分批上传。':'';
    file.closest('label').classList.toggle('hidden',!upload);
    text.closest('label').classList.toggle('hidden',upload);
    file.disabled=!upload;text.disabled=upload;file.required=upload;text.required=!upload;
    document.getElementById('ingest').textContent=batch?`上传并解析 ${file.files.length} 个文件`:upload?'上传并处理':'提交正文并处理';
  };
  modes.forEach(input=>input.addEventListener('change',refresh));
  // Keep the title in sync with the selected file: auto-fill it from the file name,
  // and refresh it when the user swaps files — unless they typed a custom title.
  let previousFileName='';
  file.onchange=()=>{
    const titleInput=document.getElementById('doc-title');
    const first=file.files[0];
    const current=titleInput.value.trim();
    if(first&&(!current||current===previousFileName))titleInput.value=first.name;
    previousFileName=first?first.name:'';
    refresh();
  };
  window.readDocumentInput=async()=>{
    const title=document.getElementById('doc-title');
    if(!document.getElementById('project').value)throw Error('请先在左侧选择或创建项目（必填）。');
    if(!title.value.trim()){title.focus();throw Error('请填写文档标题（必填）。');}
    if(title.value.trim().length>300)throw Error('文档标题不能超过 300 个字符。');
    const metadata=document.getElementById('doc-meta');
    if(metadata.value.trim()){
      let value;
      try{value=JSON.parse(metadata.value);}catch(error){metadata.focus();throw Error('Metadata 格式不正确，请填写 JSON 对象；不需要时可以留空。');}
      if(!value||Array.isArray(value)||typeof value!=='object'){metadata.focus();throw Error('Metadata 必须是 JSON 对象，例如 {"平台":"美团"}，或直接留空。');}
    }
    let content;
    if(mode()==='file'){
      const selected=file.files[0];
      if(!selected)throw Error('请先选择 .txt 或 .md 文件，或切换到“粘贴正文”。');
      if(!/\.(txt|md)$/i.test(selected.name))throw Error('请选择 .txt 或 .md 文件。');
      try{content=new TextDecoder('utf-8',{fatal:true}).decode(await selected.arrayBuffer());}
      catch(error){throw Error('文件读取失败，请确认文件可访问且为 UTF-8 编码。');}
    }else content=text.value;
    if(!content.trim())throw Error('正文不能为空，请填写正文或选择有内容的文件。');
    return content;
  };
  window.readDocumentMetadata=()=>{
    const metadata=JSON.parse(document.getElementById('doc-meta').value.trim()||'{}');
    const published=document.getElementById('doc-published-at').value;
    if(published){
      const instant=new Date(published);
      if(!Number.isFinite(instant.getTime()))throw Error('请输入有效的发布时间。');
      metadata.published_at=instant.toISOString();
    }
    return metadata;
  };
  window.readDocumentBatch=async()=>{
    // Snapshot the selected files before awaiting reads or sending requests.
    const selected=mode()==='file'?[...file.files]:[];
    const metadata=window.readDocumentMetadata();
    if(!metadata||Array.isArray(metadata)||typeof metadata!=='object')throw Error('Metadata 必须是 JSON 对象。');
    const first=await window.readDocumentInput();
    if(!selected.length)return [{title:document.getElementById('doc-title').value.trim(),text:first,metadata}];
    const documents=[];
    for(const [index,f] of selected.entries()){
      if(!/\.(txt|md)$/i.test(f.name))throw Error(f.name+'：请选择 .txt 或 .md 文件。');
      let content=first;
      if(index){try{content=new TextDecoder('utf-8',{fatal:true}).decode(await f.arrayBuffer());}catch{throw Error(f.name+'：文件读取失败或不是 UTF-8 编码。');}}
      if(!content.trim())throw Error(f.name+'：正文不能为空。');
      const title=selected.length>1?f.name:document.getElementById('doc-title').value.trim();
      if(title.length>300)throw Error(f.name+'：标题超过 300 个字符。');
      documents.push({file:f,title,text:content,metadata:{...metadata,source_file:f.name}});
    }
    return documents;
  };
  refresh();
})();
