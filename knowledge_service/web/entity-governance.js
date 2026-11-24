/* Authoritative fusion preview and resolution decisions shared by the ledger. */
(() => {
  function duplicateConfirmationPayload(groups, selected) {
    const chosen = groups.filter(group => selected.has(group.key));
    if (!chosen.length) throw new Error('请先选择已预览的重复实体组。');
    if (chosen.length > 20) throw new Error('每次最多确认 20 组。');
    if (chosen.some(group => !isPreviewed(group)))
      throw new Error('请先预览每个选中的重复实体组。');
    const memberIds = new Set();
    for (const group of chosen) {
      if (overlaps(group, memberIds)) throw new Error('选中的重复组存在重叠实体，请取消重叠选择。');
      for (const member of group.members || []) memberIds.add(member.id);
    }
    return {groups:chosen.map(({key,preview_fingerprint})=>({key,preview_fingerprint}))};
  }
  const needsMemberReview = group => group.blocked || group.reviewRequired || group.review_receipt ||
    group.partitionDraft || group.rescanRequired || group.overlapsReceipt;
  const isPreviewed = group => !needsMemberReview(group) && /^[a-f0-9]{64}$/.test(group.preview_fingerprint || '');
  const overlaps = (group, memberIds) => (group.members || []).some(row => memberIds.has(row.id));
  function conflictDecisionPayload(set, choices, reason) {
    if (!reason?.trim()) throw new Error('请填写审核理由。');
    if (!set?.members?.length || set.members.some(member => !['accept','reject'].includes(choices[member.record_id]?.decision)))
      throw new Error('请逐值选择接受或拒绝；未确定时可保留待审核。');
    return {expected_version:set.version,expected_members:set.members,
      decisions:set.members.map(member=>({attribute_id:member.record_id,...choices[member.record_id]})),reason:reason.trim()};
  }
  function createWinnerSelection(conflicts) {
    const chosen = new Map();
    return {
      choose(index, id) {
        if (!conflicts[index]?.record_ids.includes(id)) throw new Error('请选择本组候选属性记录。');
        chosen.set(index, id);
      },
      winners() {
        if (chosen.size !== conflicts.length) throw new Error('请逐组选择要保留的属性值。');
        const ids = [...new Set(chosen.values())];
        if (conflicts.some(group => ids.filter(id => group.record_ids.includes(id)).length !== 1))
          throw new Error('重叠冲突组的选择不一致，请重新逐组核对。');
        return ids;
      },
    };
  }
  function reviewDecisionPayload(review, decision, reason, expected, preview) {
    if (!reason?.trim()) throw new Error('请填写复核原因。');
    if (!['merged','separate','rejected'].includes(decision)) throw new Error('未知的复核决定。');
    if (decision === 'merged' && !preview?.preview_fingerprint) throw new Error('请重新预览融合影响并确认属性选择。');
    return {decision, reason:reason.trim(), expected_version:review.decision_version,
      ...(decision === 'merged' ? {expected_entity_versions:expected,
        attribute_winners:preview.attribute_winners || [], preview_fingerprint:preview.preview_fingerprint} : {})};
  }
  const el = (tag, cls, text) => {
    const node = document.createElement(tag); if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text; return node;
  };
  const project = () => typeof current === 'undefined' ? '' : current;
  const valueText = value => typeof value === 'string' ? value : JSON.stringify(value);
  let host = null, reload = null, serial = 0, applyScannerScope = null, dismissStalePartition = null;
  let scannerScope = {projectId:'', mode:'all', document:null};
  const assertProject = expected => { if (project() !== expected) throw new Error('项目已切换，请重新打开治理流程。'); };

  function setDocumentScope(document) {
    const projectId = project();
    const selected = document?.id && document.version_id
      ? {id:document.id, version_id:document.version_id, title:document.title || document.id} : null;
    const same = scannerScope.projectId === projectId && selected?.id === scannerScope.document?.id
      && selected?.version_id === scannerScope.document?.version_id;
    const mode = selected ? (same && scannerScope.mode === 'related' ? 'related' : 'within') : 'all';
    const changed = !same || scannerScope.mode !== mode;
    scannerScope = {projectId, document:selected, mode};
    applyScannerScope?.(scannerScope, changed);
  }

  async function preview(keepId, dropId, expectedVersions) {
    const p = project(), base = endpoint('');
    if (!expectedVersions) {
      const [keep, drop] = await Promise.all([keepId, dropId].map(id => api(base + '/records/' + encodeURIComponent(id), undefined, 'GET')));
      expectedVersions = {[keepId]:keep.version,[dropId]:drop.version};
    }
    assertProject(p);
    const data = await api(base + '/merge/preview', {keep_id:keepId, drop_id:dropId,
      expected_versions:expectedVersions});
    assertProject(p);
    return {p, base, data};
  }
  function chooseConflicts(data, confirmLabel='确认使用这些选择') {
    return new Promise(resolve => {
      const dialog = el('dialog','entity-governance-dialog');
      const selection = createWinnerSelection(data.attribute_conflicts || []);
      dialog.append(el('h3',null,'确认实体融合'), el('p',null,`${data.drop.text} → ${data.keep.text}`),
        el('p','entity-governance-impact',`服务端影响：${data.affected_records.relations} 条关系，${data.affected_records.attributes} 条属性参与检查。旧修订和来源断言保留。`));
      const compare = el('div','entity-governance-compare');
      for (const [label, row] of [['保留',data.keep],['并入',data.drop]]) {
        const item = el('div');
        item.append(el('b',null,`${label}：${row.text}`), el('small',null,`类型 ${row.type} · 修订 ${row.version} · 本体 ${row.ontology_id || '未指定'}`),
          el('small',null,`业务时间 ${window.DisplayTime.format(row.valid_from,"不限")} — ${window.DisplayTime.format(row.valid_until,"不限")}`),
          el('small',null,`适用范围 ${showApplicability(row.metadata?.applicability)}`));
        compare.append(item);
      }
      dialog.append(compare);
      const feedback = el('p','entity-governance-feedback'); feedback.setAttribute('role','status');
      const accept = el('button','primary',confirmLabel); accept.type = 'button'; accept.disabled = true;
      const acknowledge = el('input'); acknowledge.type = 'checkbox';
      const sync = () => { try { selection.winners(); accept.disabled = !acknowledge.checked; feedback.textContent = ''; }
        catch (error) { accept.disabled = true; feedback.textContent = error.message; } };
      (data.attribute_conflicts || []).forEach((group,index) => {
        const field = el('fieldset'); field.append(el('legend',null,`单值属性冲突 ${index + 1}：${group.predicate}`));
        group.values.forEach(value => {
          const label = el('label','entity-governance-choice'), radio = el('input');
          radio.type = 'radio'; radio.name = `winner-${index}`; radio.value = value.id;
          radio.addEventListener('change', () => { selection.choose(index,value.id); sync(); });
          label.append(radio, el('span',null,`${valueText(value.value)} · ${value.datatype || '未指定类型'}`),
            el('small',null,`${value.id} · 修订 ${value.version ?? '未提供'} · ${window.DisplayTime.format(value.valid_from,"不限")} — ${window.DisplayTime.format(value.valid_until,"不限")}`)); field.append(label);
        }); dialog.append(field);
      });
      if (!(data.attribute_conflicts || []).length) dialog.append(el('p',null,'本次未检测到 maxCount 1 属性冲突。'));
      const ack = el('label','entity-governance-choice');
      ack.append(acknowledge,el('span',null,'已核对影响与来源；将重复实体停用，关系转到保留实体。'));
      acknowledge.addEventListener('change',sync);
      const cancel = el('button','secondary','取消'); cancel.type='button';
      let finished = false;
      const finish = value => { if (finished) return; finished=true; dialog.close(); dialog.remove(); resolve(value); };
      cancel.addEventListener('click',()=>finish(null));
      dialog.addEventListener('cancel',event=>{ event.preventDefault(); finish(null); });
      accept.addEventListener('click',()=>{ try { if (acknowledge.checked) finish(selection.winners()); }
        catch(error) { feedback.textContent=error.message; } });
      const actions=el('div','entity-governance-actions');actions.append(cancel,accept);
      dialog.append(ack,feedback,actions);document.body.append(dialog);dialog.showModal();sync();
    });
  }
  async function prepareMerge(keepId, dropId) {
    const result = await preview(keepId, dropId);
    if (!result.data.preview_fingerprint) throw new Error('融合预览未包含版本指纹，请刷新后重新预览。');
    const winners = await chooseConflicts(result.data);
    if (winners === null) return null;
    assertProject(result.p);
    return {attribute_winners:winners, preview_fingerprint:result.data.preview_fingerprint,
      expected_versions:result.data.expected_versions};
  }
  async function decide(review, decision, reason) {
    const p=project(), base=endpoint('');
    let selection, expected;
    if (decision === 'merged') {
      selection = await prepareMerge(review.candidate_entity_id,review.source_entity_id);
      if (selection === null) return false;
      expected = selection.expected_versions;
    }
    assertProject(p);
    const result=await api(base + '/resolution-reviews/' + encodeURIComponent(review.id), reviewDecisionPayload(review,decision,reason,expected,selection));
    if(decision==='merged')await window.KnowledgeChanges?.notify({projectId:p,records:result.merge?.records||[],kind:'merge'});
    assertProject(p); return true;
  }
  const duplicateType = type => type && (typeof labelOf === 'function' ? labelOf(type) : String(type).split(/[/#]/).pop()) || '未标注类型';
  const duplicateName = row => row.text || row.name || row.id;
  const dateRange = row => `${window.DisplayTime.format(row.valid_from,'未知起点')} — ${window.DisplayTime.format(row.valid_until,'未知终点')}`;
  const hasValue = value => value != null && value !== '' && (typeof value !== 'object' || Object.keys(value).length > 0);
  const showValue = value => !hasValue(value) ? '未提供' : Array.isArray(value) ? value.map(valueText).join('、') : valueText(value);
  const showApplicability = value => window.ApplicabilityUI?.describe?.({applicability:value}) || showValue(value);
  const orderedValue = value => value && typeof value === 'object'
    ? (Array.isArray(value) ? value.map(orderedValue).sort() : Object.fromEntries(Object.keys(value).sort().map(key=>[key,orderedValue(value[key])])))
    : value;

  function mergeElapsed(started, complete=false) {
    const seconds=Math.max(0,Math.floor((Date.now()-started)/1000));
    if (complete && !seconds) return '不到 1 秒';
    return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
  }

  function showMergeProgress(status, message, started) {
    const time=el('span','duplicate-merge-time');
    time.setAttribute('aria-hidden','true');
    const tick=()=>{time.textContent=`已用时 ${mergeElapsed(started)}`;};
    status.setAttribute('data-state','running');
    status.replaceChildren(el('strong',null,message),time);
    tick();
    const timer=window.setInterval?.(tick,1000);
    return ()=>window.clearInterval?.(timer);
  }

  function duplicateSources(group, row, projectId, isCurrent, report) {
    const evidence = el('section','duplicate-source-contexts');
    evidence.append(el('h6',null,'来源证据'));
    const sources = row.source_contexts || [];
    if (!sources.length) evidence.append(el('p','duplicate-missing-evidence','未提供来源证据'));
    else evidence.append(el('p','duplicate-muted',`${row.document_count ?? 0} 份文档 · ${row.support_count ?? sources.length} 条支撑`));
    const sourceList = el('div');
    const appendSources = items => {
      for (const source of items) {
        const detail = el('details');
        detail.append(el('summary',null,source.document_title || source.document_id || '人工声明'),
          el('small','duplicate-muted',source.document_version_id || '无文档版本'),
          el('p',null,'来源有效期 '+dateRange(source)),
          el('p',null,'适用范围 '+showApplicability(source.applicability)),
          el('pre',source.quote ? '' : 'duplicate-missing-evidence',source.quote || '未提供原文引文'));
        if (source.quote_truncated) detail.append(el('small',null,'引文仅显示前 2000 字。'));
        const readerVersion = source.source_document_version_id;
        if (readerVersion) {
          const open = el('button','secondary','查看冻结原文');
          open.type = 'button';
          open.addEventListener('click',async()=>{
            if (!isCurrent()) return;
            try {
              if (!window.SourceReader) throw new Error('原文阅读器尚未就绪，请刷新页面。');
              await window.SourceReader.open({projectId,versionId:readerVersion,assertionId:source.assertion_id,focusText:source.quote,
                fallbackEvidence:{documentTitle:source.document_title,documentVersionId:source.document_version_id,
                  quote:source.quote,quoteTruncated:source.quote_truncated}});
            } catch(error) {if (isCurrent()) report(error.message);}
          });
          detail.append(open);
        } else if (source.document_id || source.document_version_id) {
          detail.append(el('p','duplicate-muted',source.quote
            ? '这条证据没有关联冻结文件版本，可核对上方已保存的引文。'
            : '这条证据没有关联冻结文件版本。'));
        }
        sourceList.append(detail);
      }
    };
    evidence.append(sourceList);
    appendSources(sources);
    let cursor = row.source_next_cursor;
    if (cursor) {
      const more = el('button','secondary','更多来源时间与证据');
      more.type = 'button';
      more.addEventListener('click',async()=>{
        if (!isCurrent()) return;
        more.disabled=true;
        try {
          const page=await api(endpoint('/duplicates/sources'),{key:group.key,preview_fingerprint:group.preview_fingerprint,
            entity_id:row.id,expected_version_id:row.version_id,limit:10,cursor});
          if (!isCurrent()) return;
          if (page.entity_id!==row.id || page.version_id!==row.version_id) throw new Error('来源响应不属于当前实体修订，请重新预览。');
          appendSources(page.source_contexts || []);
          cursor=page.next_cursor;
          more.hidden=!cursor;
        } catch(error) {if (isCurrent()) report(error.message);}
        finally {more.disabled=false;}
      });
      evidence.append(el('small','duplicate-muted',`先显示 ${sources.length} 条来源支撑，可继续读取。`),more);
    }
    return evidence;
  }

  function identityFields(members, onlyDifferences) {
    return [
      ['身份键',row=>row.metadata?.identity_keys ?? row.identity_keys],
      ['身份范围',row=>row.metadata?.identity_scope],
      ['别名',row=>row.metadata?.aliases ?? row.aliases],
      ['业务有效期',row=>dateRange(row)],
      ['适用范围',row=>row.metadata?.applicability ?? row.applicability,showApplicability],
    ].flatMap(([label,get,format=showValue])=>{
      const values = members.map(get);
      const differs=new Set(values.map(value=>JSON.stringify(orderedValue(value)))).size>1;
      if (!values.some(hasValue) || (onlyDifferences && !differs)) return [];
      return [{label,get,format,differs}];
    });
  }

  function memberEvidence(group,row,fields,projectId,isCurrent,report) {
    const details=el('div');
    const identity=el('dl');
    for (const {label,get,format,differs} of fields) identity.append(el('dt',null,label),
      el('dd',differs?'duplicate-field-difference':null,format(get(row))));
    details.append(identity);
    if (row.relation_context) details.append(duplicateRelationContext(row.relation_context));
    details.append(duplicateSources(group,row,projectId,()=>isCurrent()&&details.isConnected!==false,report));
    return details;
  }

  function duplicateComparison(group, onlyDifferences, projectId, isCurrent, report, memberActions) {
    const layout = el('div','duplicate-comparison');
    const members = group.members || [];
    const fields = identityFields(members,onlyDifferences);
    for (const row of members) {
      const member=el('article','duplicate-member');
      member.append(el('h5',null,duplicateName(row)));
      if (group.keep_id) member.append(el('small','duplicate-member-role',row.id === group.keep_id ? '保留实体' : '并入实体'));
      member.append(el('p','duplicate-muted',`${duplicateType(row.type || group.type)} · ${row.id} · 修订 ${row.version ?? '未提供'}`));
      member.append(memberEvidence(group,row,fields,projectId,isCurrent,report));
      memberActions?.(member,row);
      layout.append(member);
    }
    return layout;
  }

  function duplicateRelationContext(context) {
    const section=el('section','duplicate-member-relations');
    const examples=context.examples || [];
    section.append(el('h6',null,'当前关系'));
    if (!context.total) {
      section.append(el('p','duplicate-muted','当前无关系记录'));
      return section;
    }
    section.append(el('p','duplicate-muted',`${context.total} 条关系${context.total>examples.length?` · 显示 ${examples.length} 条示例`:''}`));
    const list=el('ul');
    for (const row of examples) {
      const item=el('li',null,`${row.subject_label || row.subject_id} — ${duplicateType(row.type)} → ${row.object_label || row.object_id}`);
      if (row.valid_from || row.valid_until) item.append(el('small','duplicate-muted',dateRange(row)));
      list.append(item);
    }
    section.append(list);
    return section;
  }

  function duplicateImpact(group,showSummary=true) {
    const impact = el('section','duplicate-impact');
    const affected = group.affected_records || {};
    const conflicts = group.attribute_conflicts || [];
    const keep=group.members?.find(row=>row.id===group.keep_id);
    if (showSummary) impact.append(el('p',null,`合并后继续使用「${keep?duplicateName(keep):group.name || group.keep_id}」 · 影响 ${affected.relations ?? 0} 条关系、${affected.attributes ?? 0} 条属性 · ${conflicts.length} 组冲突`));
    for (const conflict of conflicts) {
      const detail = el('details','duplicate-impact-conflict');
      detail.append(el('summary',null,`${duplicateType(conflict.predicate)} · ${conflict.values.length} 个冲突值`),
        el('p',null,'合并时隔离冲突值，后续在知识审核逐值处理。'));
      const parseBound=value=>window.DisplayTime.format(value,'') ? Date.parse(value) : NaN;
      const intervals=conflict.values.map(row=>({row,from:parseBound(row.valid_from),until:parseBound(row.valid_until)}));
      if (intervals.some(({from,until})=>!Number.isFinite(from) || !Number.isFinite(until) || from>=until)) {
        detail.append(el('p','duplicate-muted','重叠区间边界待核对原文。'));
      } else if (intervals.length>1) {
        const start=intervals.reduce((latest,next)=>next.from>latest.from?next:latest);
        const end=intervals.reduce((earliest,next)=>next.until<earliest.until?next:earliest);
        detail.append(el('p','duplicate-muted',start.from<end.until
          ? '共同重叠区间 '+dateRange({valid_from:start.row.valid_from,valid_until:end.row.valid_until})
          : '各值没有共同重叠区间，请逐值核对。'));
      }
      for (const value of conflict.values) {
        detail.append(el('p',null,valueText(value.value)+(value.value_truncated?'…（预览仅显示前 240 字）':'')));
        if (value.valid_from || value.valid_until) detail.append(el('small','duplicate-muted',dateRange(value)));
      }
      impact.append(detail);
    }
    if (group.affected_record_ids || group.fact_deduplication?.length) {
      const details=el('details');
      details.append(el('summary',null,'受影响事实与重复支撑归并'));
      for (const [kind,label] of [['relations','关系'],['attributes','属性']]) {
        const ids=group.affected_record_ids?.[kind] || [];
        details.append(el('p',null,`${label}：${ids.join('、') || '无'}${group.affected_records_truncated?.[kind]?'（仅显示前 100 条）':''}`));
      }
      for (const fact of group.fact_deduplication || []) details.append(el('p',null,
        `${duplicateType(fact.predicate)}：${fact.record_count} 条同事实归到 ${fact.survivor_id}；保留 ${fact.support_count} 条支撑、${fact.document_count} 份文档。`));
      if (group.fact_deduplication_truncated) details.append(el('p',null,`共有 ${group.fact_deduplication_total} 组重复事实，当前显示前 20 组。`));
      impact.append(details);
    }
    return impact;
  }

  const groupSize = group => group.member_count ?? group.members?.length ?? 0;
  const memberNumber = index => String(index+1).padStart(2,'0');
  const partitionCounts = (assigned,total,kept) => `已安排 ${assigned}/${total} · 保留 ${kept} 条 · 并入 ${assigned-kept} 条`;

  function partitionColumns(members,all) {
    const scope=row=>row.metadata?.applicability ?? row.applicability ?? {};
    const keys=row=>row.metadata?.identity_keys ?? row.identity_keys ?? {};
    const dimension=value=>value?.mode==='all'?'不限':value?.mode==='unknown'?'未知':
      Array.isArray(value?.values)?value.values.join('、'):showValue(value);
    const date=value=>window.DisplayTime.format(value,'—').replace('年','-').replace('月','-').replace('日','')
      .replace(/ 00:00:00$/,'').replace(/(\d\d:\d\d):00$/,'$1');
    const dimensions={platforms:'平台',businesses:'业务',audiences:'受众'};
    const scopeKeys=new Set(members.flatMap(row=>Object.keys(scope(row))));
    const identityKeys=new Set(members.flatMap(row=>Object.keys(keys(row))));
    const fields=[
      ...[...scopeKeys].map(key=>[dimensions[key] || duplicateType(key),row=>scope(row)[key],dimension]),
      ['生效时间',row=>row.valid_from,date],['失效时间',row=>row.valid_until,date],
      ...[...identityKeys].map(key=>['身份键 · '+duplicateType(key),row=>keys(row)[key]]),
      ['身份范围',row=>row.metadata?.identity_scope ?? row.identity_scope],
      ['别名',row=>row.metadata?.aliases ?? row.aliases],
    ];
    return fields.flatMap(([label,get,format=showValue])=>{
      const values=members.map(get),differs=new Set(values.map(v=>JSON.stringify(orderedValue(hasValue(v)?v:null)))).size>1;
      return values.some(hasValue) && (differs || all)?[{label,get,format,differs,isDate:format===date}]:[];
    });
  }

  function tint(node,number) {
    if (number) node.dataset.tone=String((Number(number)-1)%4);
    else delete node.dataset.tone;
    return node;
  }

  function partitionMap(group,targets,withImpact=false) {
    const layout=el('div','partition-map');
    const ids=targets.flatMap(target=>[target.keep_id,...target.drop_ids]).sort();
    const members=new Map((group.members || []).map(row=>[row.id,row]));
    const number=id=>memberNumber(ids.indexOf(id));
    const token=(id,anchor=false)=>{
      const row=members.get(id),source=row?.source_contexts?.[0];
      const context=row && duplicateName(row)!==group.name?duplicateName(row):source?.document_title || source?.document_id || id;
      const chip=el('span',anchor?'partition-token partition-anchor':'partition-token');
      chip.title=id+' · '+context;
      chip.append(el('b',null,number(id)),el('span',null,context));
      return chip;
    };
    for (const target of [...targets].sort((a,b)=>Number(number(a.keep_id))-Number(number(b.keep_id)))) {
      const article=tint(el('article','partition-family'),number(target.keep_id));
      const mapping=el('div','partition-mapping'),keeper=el('div','partition-keeper'),drops=el('div','partition-drops');
      keeper.append(el('small',null,'保留'),token(target.keep_id,true));
      if (target.drop_ids.length) {
        drops.append(el('small',null,'并入'));
        const chips=el('div','partition-tokens');
        for (const id of [...target.drop_ids].sort()) chips.append(token(id));
        drops.append(chips);
      } else drops.append(el('small',null,'单独保留'));
      mapping.append(keeper,drops);article.append(mapping);
      if (withImpact) {
        const metrics=el('div','partition-metrics');
        for (const [label,value] of [['关系',target.affected_records?.relations],['属性',target.affected_records?.attributes],
          ['冲突',target.attribute_conflicts?.length]]) {
          const metric=el('span',label==='冲突' && value?'partition-has-conflict':'');
          metric.append(el('b',null,String(value || 0)),el('small',null,label));
          metrics.append(metric);
        }
        article.append(metrics);
        if (target.attribute_conflicts?.length || Object.values(target.affected_record_ids || {}).some(ids=>ids.length) || target.fact_deduplication?.length)
          article.append(duplicateImpact(target,false));
      }
      layout.append(article);
    }
    return layout;
  }

  function partitionReceipt(group,receipt,expanded=false) {
    const section=el('section','duplicate-partition-receipt');
    section.append(el('h5',null,'分组复核已完成'),
      el('p',null,partitionCounts(receipt.assigned_count,receipt.assigned_count,receipt.kept_count)));
    const mapping=partitionMap(group,receipt.targets || []);
    if (expanded) section.append(mapping);
    else {
      const details=el('details');
      details.append(el('summary',null,'查看分组结果'),mapping);
      details.open=receipt.kept_count<=3;
      section.append(details);
    }
    return section;
  }

  function openPartitionReview(group,projectId,isCurrent,{onDraft,onWriting,onCompleted,onUncertain,onClose}) {
    const dialog=el('dialog','entity-governance-dialog duplicate-review-dialog duplicate-partition-dialog');
    const title=el('h3',null,group.name || '复核组内成员');
    dialog.setAttribute('aria-label','复核组内成员');
    const button=(label,variant='secondary')=>{
      const node=el('button',variant,label);node.type='button';return node;
    };
    const close=button('关闭');close.setAttribute('aria-label','关闭复核');
    close.autofocus=true;
    const header=el('header','duplicate-review-header'),heading=el('div');
    heading.append(title,el('small','duplicate-muted',`${duplicateType(group.type)} · ${groupSize(group)} 条候选`));
    header.append(heading,close);
    const progress=el('div','duplicate-partition-progress'),counts=el('span'),meter=el('progress'),families=el('div','partition-live-groups');
    counts.setAttribute('role','status');meter.setAttribute('aria-label','分组安排进度');
    meter.max=groupSize(group);progress.append(counts,meter,families);
    const main=el('div','duplicate-partition-body');
    const diffLabel=el('label','entity-governance-choice'),diff=el('input');
    diff.type='checkbox';diffLabel.append(diff,el('span',null,'全部字段'));
    const membersList=el('div','duplicate-partition-members'),effects=el('section','duplicate-partition-effects');
    effects.tabIndex=-1;effects.hidden=true;
    const instructions=el('div','duplicate-partition-instructions');
    const tableTitle=el('h4',null,'成员对比');
    instructions.append(tableTitle,diffLabel);
    if (group.previewError || group.blocked_reason) {
      const why=el('details','partition-block-reason');
      why.append(el('summary',null,'匹配提示'),el('p',null,group.previewError || group.blocked_reason));
      instructions.append(why);
    }
    main.append(instructions,membersList,effects);
    const footer=el('footer','duplicate-partition-footer');
    const feedback=el('p','entity-governance-feedback');
    feedback.setAttribute('role','status');
    const reason=el('p','duplicate-muted');reason.id='duplicate-partition-hint';
    const ackLabel=el('label','entity-governance-choice'),ack=el('input');
    ack.type='checkbox';ackLabel.append(ack,el('span',null,'已核对归并影响'));
    const actions=el('div','duplicate-card-actions');
    const reloadMembers=button('重新读取成员'),edit=button('编辑分组'),previewPlan=button('预览分组方案','primary'),confirm=button('确认分组方案','primary');
    previewPlan.setAttribute('aria-describedby',reason.id);
    confirm.setAttribute('aria-describedby',reason.id);
    actions.append(reloadMembers,edit,previewPlan,confirm);
    footer.append(feedback,reason,ackLabel,actions);
    dialog.append(header,progress,main,footer);
    document.body.append(dialog);
    dialog.showModal();

    let members=[],controls=[],numbers=new Map(),assignments=new Map(),expected={},plan=null;
    let busy=false,submitting=false,uncertain=false,mustReload=false,complete=false,stopClock=null;
    const active=()=>{
      if (!isCurrent() && dialog.open) dialog.close();
      return dialog.open;
    };
    dismissStalePartition=active;
    const projectChanged=event=>{if (event.target?.id==='project') active();};
    document.addEventListener('change',projectChanged);
    const writable=()=>Boolean(window.Auth?.isAdmin?.());
    const report=text=>{feedback.setAttribute('data-state','idle');feedback.textContent=text;};
    const selectedKeeps=()=>members.filter(row=>assignments.get(row.id)===row.id);
    const payload=()=>({key:group.key,assignments:Object.fromEntries(assignments),expected_versions:expected});
    const rowLabel=row=>`${numbers.get(row.id)} ${duplicateName(row)}`;
    function syncTargets(control,keeps,locked) {
      const {row,keep,target,tr}=control,number=numbers.get(row.id),destination=assignments.get(row.id);
      keep.checked=destination===row.id;
      keep.disabled=locked;
      tint(tr,numbers.get(destination));
      const key=keep.checked?'kept':keeps.map(survivor=>numbers.get(survivor.id)).join(',');
      if (key!==control.key) {
        control.key=key;control.chips=[];control.select=null;target.replaceChildren();
        if (keep.checked || !keeps.length) target.append(el('span','partition-role',keep.checked?'保留':'—'));
        else if (keeps.length<=3) {
          for (const survivor of keeps) {
            const to=numbers.get(survivor.id),chip=tint(button(to,'partition-choice'),to);
            chip.setAttribute('aria-label',number+' 归入 '+to);
            chip.title='归入 '+rowLabel(survivor);
            chip.addEventListener('click',()=>{
              if (!active() || chip.disabled) return;
              if (assignments.get(row.id)===survivor.id) assignments.delete(row.id);
              else assignments.set(row.id,survivor.id);
              changed();
            });
            control.chips.push({id:survivor.id,chip});target.append(chip);
          }
        } else {
          const select=el('select');
          select.setAttribute('aria-label',number+' 的归并目标');
          const hint=el('option',null,'选目标');hint.value='';select.append(hint);
          for (const survivor of keeps) {
            const option=el('option',null,rowLabel(survivor));option.value=survivor.id;
            select.append(option);
          }
          select.addEventListener('change',()=>{
            if (!active() || select.disabled) return;
            if (select.value) assignments.set(row.id,select.value);
            else assignments.delete(row.id);
            changed();
          });
          control.select=select;target.append(select);
        }
      }
      for (const {id,chip} of control.chips) {
        chip.setAttribute('aria-pressed',String(destination===id));chip.disabled=locked;
      }
      if (control.select) {control.select.disabled=locked;control.select.value=destination || '';}
    }

    function sync() {
      const keeps=selectedKeeps(),total=groupSize(group);
      const locked=busy || uncertain || mustReload || complete;
      counts.textContent=partitionCounts(assignments.size,total,keeps.length);meter.value=assignments.size;
      families.replaceChildren();
      families.hidden=Boolean(plan) || !keeps.length;
      if (!plan) for (const survivor of keeps) {
        const number=numbers.get(survivor.id),count=[...assignments.values()].filter(id=>id===survivor.id).length;
        const chip=tint(el('span','partition-live-chip'),number);
        chip.append(el('b',null,number),el('span',null,count+' 条'));families.append(chip);
      }
      for (const control of controls) syncTargets(control,keeps,locked);
      membersList.hidden=instructions.hidden=Boolean(plan);
      effects.hidden=!plan;
      diff.disabled=locked;
      close.disabled=submitting;
      reloadMembers.hidden=Boolean(plan) || (!mustReload && (members.length>0 || busy)) || uncertain || complete;
      reloadMembers.disabled=busy || uncertain || complete;
      edit.hidden=!plan;edit.disabled=locked;
      previewPlan.hidden=Boolean(plan);confirm.hidden=!plan;
      previewPlan.disabled=locked || !members.length || assignments.size!==total;
      confirm.disabled=locked || !plan || !ack.checked || !writable();
      ackLabel.hidden=!plan || !writable();
      ack.disabled=locked;
      reason.textContent=uncertain?'关闭后重新扫描，核对提交结果。':
        mustReload?'请重新读取成员。':!writable()?'仅管理员可确认。':
        !members.length || plan?'':!keeps.length?'先勾选保留项。':
        assignments.size<total?(keeps.length>3?`为剩余 ${total-assignments.size} 条选择目标。`:`点目标编号安排剩余 ${total-assignments.size} 条。`):'';
    }
    function changed() {
      plan=null;ack.checked=false;effects.hidden=true;effects.replaceChildren();
      report('');onDraft(assignments.size?{...payload(),kept_count:selectedKeeps().length}:null);
      sync();
    }
    function renderMembers() {
      const fields=partitionColumns(members,diff.checked);
      const openEvidence=new Set(controls.filter(control=>!control.detail.hidden).map(control=>control.row.id));
      const showNames=new Set(members.map(duplicateName)).size>1;
      const showQuotes=new Set(members.map(row=>row.source_contexts?.[0]?.quote || '')).size>1;
      const identity=identityFields(members,false);
      controls=[];
      const table=el('table','partition-table'),head=el('thead'),labels=el('tr'),body=el('tbody');
      table.setAttribute('aria-label','成员差异对比');
      for (const label of ['成员','保留','归入',...fields.map(field=>field.label),'来源']) {
        const th=el('th',null,label);th.scope='col';labels.append(th);
      }
      head.append(labels);table.append(head,body);
      for (const row of members) {
        const number=numbers.get(row.id),tr=el('tr','partition-data-row');
        const id=el('th','partition-member-id');id.scope='row';id.title=row.id;
        id.append(el('b',null,number));
        if (showNames) id.append(el('small',null,duplicateName(row)));
        const keepCell=el('td','partition-keep-cell'),keep=el('input');
        keep.type='checkbox';keep.setAttribute('aria-label','保留实体 '+number);
        keep.addEventListener('change',()=>{
          if (!active() || keep.disabled) return;
          if (keep.checked) assignments.set(row.id,row.id);
          else for (const [id,target] of assignments) if (target===row.id) assignments.delete(id);
          changed();
        });
        keepCell.append(keep);
        const targetCell=el('td','partition-target-cell'),target=el('div','partition-targets');
        targetCell.append(target);tr.append(id,keepCell,targetCell);
        for (const {get,format,differs,isDate} of fields) {
          const value=get(row),display=hasValue(value)?format(value):'—';
          const cell=el('td',differs?'partition-difference':'partition-common');
          cell.append(el('span',null,display));cell.title=display;
          if (isDate && value) {
            cell.classList.add('partition-date');cell.title=window.DisplayTime.format(value);
          }
          tr.append(cell);
        }
        const sourceCell=el('td','partition-source-cell'),source=row.source_contexts?.[0];
        const toggle=button(source?.document_title || source?.document_id || (source?'人工声明':'查看记录'),'partition-source-toggle');
        toggle.setAttribute('aria-label','查看 '+number+' 的身份与来源');
        toggle.title=toggle.textContent;
        if (row.document_count>1) toggle.append(el('small',null,' +'+(row.document_count-1)));
        sourceCell.append(toggle);
        if (showQuotes && source?.quote) sourceCell.append(el('small','partition-quote',source.quote));
        tr.append(sourceCell);
        const detail=el('tr','partition-evidence-row'),detailCell=el('td','duplicate-member');
        detailCell.colSpan=fields.length+4;
        const container=el('div','partition-evidence-content');
        detailCell.append(container);detail.append(detailCell);
        detail.id='duplicate-partition-evidence-'+number;toggle.setAttribute('aria-controls',detail.id);
        const expand=open=>{
          detail.hidden=!open;toggle.setAttribute('aria-expanded',String(open));
          if (!open || container.childElementCount) return;
          const evidence=memberEvidence({key:group.key},row,identity,projectId,active,report);
          const first=evidence.querySelector('.duplicate-source-contexts details');if (first) first.open=true;
          container.append(el('b',null,rowLabel(row)),el('small','duplicate-muted',row.id+' · 修订 '+row.version),evidence);
        };
        expand(openEvidence.has(row.id));
        toggle.addEventListener('click',()=>expand(detail.hidden));
        body.append(tr,detail);controls.push({row,tr,keep,target,detail,chips:[]});
      }
      membersList.replaceChildren(table);
      membersList.tabIndex=0;membersList.setAttribute('role','region');
      membersList.setAttribute('aria-label','横向浏览成员字段');
      sync();
    }
    async function load() {
      if (!active() || busy || uncertain) return;
      busy=true;plan=null;ack.checked=false;effects.hidden=true;sync();report('正在读取全部成员…');
      try {
        const page=await api(endpoint('/duplicates/members'),{key:group.key,limit:20,after_id:null});
        if (!active()) return;
        if (page.key!==group.key || page.next_after_id || page.members?.length!==groupSize(group))
          throw new Error('成员范围已变化，请关闭复核并重新扫描。');
        if (page.members.some(row=>!row.id || !Number.isInteger(row.version)) ||
            new Set(page.members.map(row=>row.id)).size!==page.members.length)
          throw new Error('成员身份或修订不完整，请重新扫描。');
        members=[...page.members].sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
        numbers=new Map(members.map((row,index)=>[row.id,memberNumber(index)]));
        expected=Object.fromEntries(members.map(row=>[row.id,row.version]));
        const draft=group.partitionDraft;
        const restored=draft && Object.keys(draft.expected_versions).length===members.length &&
          members.every(row=>draft.expected_versions[row.id]===row.version);
        assignments=restored?new Map(Object.entries(draft.assignments)):new Map();
        if (draft && !restored) onDraft(null);
        mustReload=false;
        renderMembers();
        report(draft && !restored?'实体修订已变化，请重新安排。':'');
      } catch(error) {
        if (active()) {
          members=[];controls=[];assignments.clear();membersList.replaceChildren();
          report('成员读取失败。'+error.message);
        }
      } finally {
        busy=false;
        if (active()) sync();
      }
    }
    function matchesPlan(result) {
      if (!Array.isArray(result.targets)) return false;
      const seen=new Set();
      for (const {keep_id,drop_ids} of result.targets) {
        if (seen.has(keep_id) || assignments.get(keep_id)!==keep_id || !Array.isArray(drop_ids)) return false;
        seen.add(keep_id);
        for (const id of drop_ids) {
          if (seen.has(id) || assignments.get(id)!==keep_id) return false;
          seen.add(id);
        }
      }
      return assignments.size===seen.size;
    }
    previewPlan.addEventListener('click',async()=>{
      if (!active() || previewPlan.disabled) return;
      busy=true;plan=null;ack.checked=false;effects.hidden=true;sync();report('正在预览各个归并目标…');
      try {
        const result=await api(endpoint('/duplicates/partition/preview'),payload());
        if (!active()) return;
        if (!result.preview_fingerprint || !matchesPlan(result)) throw new Error('服务端返回的分组与当前安排不一致，请重新读取成员。');
        effects.replaceChildren(el('h4',null,'分组预览'),partitionMap({name:group.name,members},result.targets,true));
        plan=result;sync();main.scrollTop=0;effects.focus();report('');
      } catch(error) {if (active()) report('分组预览失败。'+error.message);}
      finally {busy=false;if (active()) sync();}
    });
    confirm.addEventListener('click',async()=>{
      if (!active() || confirm.disabled || !writable() || !ack.checked || !plan) return;
      busy=submitting=true;onWriting(true);sync();
      stopClock=showMergeProgress(feedback,'正在确认分组方案',Date.now());
      try {
        const receipt=await api(endpoint('/duplicates/partition/confirm'),{...payload(),preview_fingerprint:plan.preview_fingerprint});
        if (!active()) return;
        if (receipt.status!=='completed' || !matchesPlan(receipt) || receipt.assigned_count!==members.length ||
            receipt.kept_count!==selectedKeeps().length || receipt.merged_count!==members.length-selectedKeeps().length)
          throw new Error('未收到与当前方案一致的完整回执。');
        complete=true;
        onCompleted(receipt,members);
        progress.hidden=true;footer.hidden=true;
        const result=partitionReceipt(group,receipt,true);
        main.replaceChildren(result);main.scrollTop=0;
        close.disabled=false;close.focus();
      } catch(error) {
        if (active()) {
          plan=null;ack.checked=false;effects.hidden=true;
          if ([400,401,403,404,409,422].includes(error.status)) {
            mustReload=[404,409].includes(error.status) || /版本冲突|修订已变化/.test(error.message);
            report('服务端未接受分组方案。'+error.message);
          } else {
            uncertain=true;onUncertain();
            report('确认结果待核对。'+error.message);
          }
        }
      } finally {stopClock?.();stopClock=null;submitting=busy=false;onWriting(false);if (active()) sync();}
    });
    ack.addEventListener('change',sync);
    edit.addEventListener('click',()=>{
      if (!active() || edit.disabled) return;
      plan=null;ack.checked=false;report('');sync();previewPlan.focus();
    });
    diff.addEventListener('change',renderMembers);
    reloadMembers.addEventListener('click',load);
    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('cancel',event=>{if (submitting && isCurrent()) event.preventDefault();});
    dialog.addEventListener('close',()=>{
      stopClock?.();stopClock=null;
      document.removeEventListener('change',projectChanged);
      if (dismissStalePartition===active) dismissStalePartition=null;
      dialog.remove();
      if (isCurrent()) onClose();
    });
    void load();
  }

  function openDuplicateMemberReview(group, projectId, isCurrent, onMerged) {
    const dialog=el('dialog','entity-governance-dialog duplicate-review-dialog');
    const title=el('h3',null,'复核组内成员');title.id='duplicate-member-review-title';
    dialog.setAttribute('aria-labelledby',title.id);
    const button=(label,variant='secondary')=>{
      const node=el('button',variant,label);node.type='button';return node;
    };
    const close=button('关闭复核'),previous=button('上一页成员'),next=button('下一页成员');
    close.autofocus=true;
    const refresh=button('重新读取成员'),previewPair=button('预览所选两条','primary');
    const selection=el('p','duplicate-member-role'),pageLabel=el('span','duplicate-muted');
    const feedback=el('p','entity-governance-feedback');feedback.setAttribute('role','status');
    const content=el('div');
    const actions=el('div','duplicate-card-actions');actions.append(previewPair);
    const paging=el('div','duplicate-card-actions');paging.append(previous,pageLabel,next,refresh);
    const header=el('header','duplicate-review-header');
    const heading=el('div');heading.append(title,el('p','duplicate-muted',`${group.name} · ${group.member_count ?? group.members?.length ?? 0} 个候选实体`));
    header.append(heading,close);
    const reason=el('div','duplicate-review-reason');
    reason.append(el('strong',null,'超过 20 条，分次复核'),
      el('p',null,'跨页选择一条保留、一条并入。剩余至 20 条即可整组安排。'));
    if (group.previewError) reason.append(el('p','duplicate-muted',group.previewError));
    dialog.append(header,reason,selection,actions,feedback,content,paging);
    document.body.append(dialog);
    dialog.showModal();
    const active=()=>isCurrent()&&dialog.open;
    let keep=null,drop=null,page=0,cursors=[null],nextAfter=null,busy=false,submitting=false,needsReload=false;
    let choices=[];
    const report=text=>{feedback.setAttribute('data-state','idle');feedback.textContent=text;};

    function sync() {
      const selected=row=>row?`${duplicateName(row)}（${row.id}）`:'未选择';
      selection.textContent=`保留 ${selected(keep)} · 并入 ${selected(drop)}`;
      previewPair.disabled=busy||needsReload||!keep||!drop;
      previous.disabled=busy||needsReload||page===0;
      next.disabled=busy||needsReload||!nextAfter;
      refresh.disabled=busy;
      close.disabled=submitting;
      pageLabel.textContent=`第 ${page+1} 页`;
      for (const {row,role,control} of choices) {
        control.disabled=busy||needsReload;
        control.setAttribute('aria-pressed',String((role==='keep'?keep:drop)?.id===row.id));
      }
    }

    async function load(target) {
      if (!active()||busy) return;
      busy=true;sync();report('正在读取这一页成员…');
      try {
        const result=await api(endpoint('/duplicates/members'),{key:group.key,limit:10,after_id:cursors[target]});
        if (!active()) return;
        if (result.key!==group.key) throw new Error('成员响应不属于当前重复组。');
        page=target;nextAfter=result.next_after_id;choices=[];
        const members=result.members || [];
        content.replaceChildren(members.length?duplicateComparison({key:group.key,type:group.type,members},
          false,projectId,active,report,(member,row)=>{
            const controls=el('div','duplicate-card-actions');
            for (const [role,label] of [['keep','设为保留'],['drop','设为并入']]) {
              const control=button(label);
              choices.push({row,role,control});
              control.addEventListener('click',()=>{
                if (role==='keep') {keep=row;if (drop?.id===row.id) drop=null;}
                else {drop=row;if (keep?.id===row.id) keep=null;}
                report('');sync();
              });
              controls.append(control);
            }
            member.append(controls);
          }):el('p',null,'这一页没有成员，可重新读取或回到列表重新扫描。'));
        needsReload=false;report('');
      } catch(error) {
        if (active()) report('成员读取失败：'+error.message);
      } finally {
        busy=false;
        if (active()) sync();
      }
    }

    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>dialog.remove());
    dialog.addEventListener('cancel',event=>{if (submitting) event.preventDefault();});
    previous.addEventListener('click',()=>load(page-1));
    next.addEventListener('click',()=>{cursors[page+1]=nextAfter;void load(page+1);});
    refresh.addEventListener('click',()=>{
      keep=drop=null;page=0;cursors=[null];nextAfter=null;choices=[];content.replaceChildren();void load(0);
    });
    previewPair.addEventListener('click',async()=>{
      if (!active()||previewPair.disabled) return;
      const [kept,merged]=[keep,drop];
      busy=true;sync();report('正在预检所选两条实体…');
      let stopClock;
      try {
        const result=await preview(kept.id,merged.id,{[kept.id]:kept.version,[merged.id]:merged.version});
        if (!active()) return;
        if (!result.data.preview_fingerprint) throw new Error('预检缺少版本指纹，请重新读取成员。');
        if (!window.Auth?.isAdmin?.()) {
          const impact=result.data.affected_records;
          report(`预检通过 · 影响 ${impact.relations} 条关系、检查 ${impact.attributes} 条属性 · ${result.data.attribute_conflicts?.length || 0} 组属性冲突。管理员可确认合并。`);
          return;
        }
        const winners=await chooseConflicts(result.data,'确认合并这两条');
        if (winners===null||!active()||!window.Auth?.isAdmin?.()) {report('');return;}
        submitting=true;sync();
        const started=Date.now();
        stopClock=showMergeProgress(feedback,'正在合并所选两条',started);
        const writeResult=await api(result.base+'/merge',{keep_id:kept.id,drop_id:merged.id,
          expected_versions:result.data.expected_versions,attribute_winners:winners,
          preview_fingerprint:result.data.preview_fingerprint});
        if (!active()) return;
        submitting=false;dialog.close();onMerged(kept,merged,writeResult,started);
      } catch(error) {
        if (!active()) return;
        if (submitting) {
          needsReload=true;
          report('提交结果待核对。请重新读取成员后再继续。'+error.message);
        } else report('所选两条暂不能合并。'+error.message);
      } finally {
        stopClock?.();
        busy=submitting=false;
        if (active()) sync();
      }
    });
    void load(0);
  }

  function duplicateScanner(projectId, mountId) {
    const section = el('section','duplicate-scanner');
    const button = (text, variant='secondary') => {
      const node = el('button',variant,text);
      node.type='button';
      return node;
    };
    const checkbox = (cls,id) => {
      const node = el('input',cls);
      node.type='checkbox';
      if (id) node.id=id;
      return node;
    };
    const feedback = el('div','entity-governance-feedback');
    feedback.setAttribute('role','status');
    const report = text => {feedback.textContent=text;};
    const scopeSelect = el('select');
    scopeSelect.id='duplicate-scan-scope';
    for (const [value,text] of [['all','全项目'],['within','仅此文档版本'],['related','此文档版本匹配全项目']]) {
      const option=el('option',null,text);option.value=value;scopeSelect.append(option);
    }
    const scopeLabel=el('label',null,'扫描范围');scopeLabel.htmlFor=scopeSelect.id;
    const scopeFields=el('div','duplicate-scope-field');scopeFields.append(scopeLabel,scopeSelect);
    const docSelection=el('p','duplicate-document-selection');
    docSelection.setAttribute('aria-live','polite');
    const search=el('form','duplicate-document-search');
    const query=el('input');query.type='search';query.id='duplicate-document-query';query.placeholder='按标题或关键词查找文档';
    query.setAttribute('aria-label','文档关键词');
    const searchButton=button('查找文档');searchButton.type='submit';
    const searchResults=el('ul','duplicate-document-results');searchResults.hidden=true;
    const searchFeedback=el('p','duplicate-muted');searchFeedback.setAttribute('role','status');searchFeedback.hidden=true;
    search.append(query,searchButton);
    const scan=button('扫描已有重复实体','primary'), more=button('继续扫描');
    const scanBar=el('div','duplicate-scan-actions');scanBar.append(scan,more);
    const scanOptions=el('div','duplicate-scan-options');
    scanOptions.append(scopeFields,docSelection,search,searchFeedback,searchResults);
    const scanToolbar=el('div','duplicate-scan-toolbar');scanToolbar.append(scanOptions,scanBar);
    const batch=button('批量预览');batch.id='duplicate-preview-batch';
    const selectPreview=button('勾选前 20 组待预览');
    const onlyDifferences=checkbox(null,'duplicate-diff-only');
    const differenceLabel=el('label','entity-governance-choice');differenceLabel.append(onlyDifferences,el('span',null,'仅显示差异'));
    const candidateActions=el('div','duplicate-candidate-actions');
    candidateActions.append(selectPreview,batch,differenceLabel);
    const mergeGuide=el('p','duplicate-muted','复核可保留多条。批量合并每组仅保留一条。');
    const list=el('div','duplicate-candidates');
    const deferredSection=el('details','duplicate-deferred');
    const confirm=button('确认合并所选组','primary');confirm.id='duplicate-confirm';
    const ack=checkbox(null,'duplicate-merge-ack');
    const ackLabel=el('label','entity-governance-choice');
    ackLabel.append(ack,el('span',null,'已核对所选组的身份、来源和影响；冲突值隔离后在知识审核处理。'));
    const plan=el('section','duplicate-merge-plan'),planDetails=el('div');
    const planTitle=el('h5',null,'待确认的合并影响');
    const mergeStatus=el('div','duplicate-merge-status');mergeStatus.setAttribute('role','status');
    plan.append(planTitle,planDetails,ackLabel,confirm,mergeStatus);
    const selected=new Set(), previewKeys=new Set(), deferred=new Set(), expanded=new Set();
    let scopeState, queuedScope, groups=[], controls=[], deferredSummary, cursor=null, busy=false, writing=false, generation=0;
    let searchRequest=0, searching=false, documentButtons=[];
    let mergeReceipt=false,mergeAttempt=0;
    const mounted=()=>project()===projectId && mountId===serial;
    const currentRound=ticket=>mounted() && generation===ticket;

    function clearRound(preserveFeedback=false) {
      generation++;
      dismissStalePartition?.();
      groups=[];cursor=null;busy=false;ack.checked=false;
      selected.clear();previewKeys.clear();deferred.clear();expanded.clear();
      if (!preserveFeedback) {
        report('');mergeReceipt=false;mergeStatus.replaceChildren();
      }
      render();
    }

    function changeScope(next, reset, preserveFeedback=false) {
      if (!mounted() || projectId!==next.projectId) return;
      if (writing) {queuedScope=next;return;}
      scopeState=next;
      scopeSelect.value=next.mode;
      docSelection.hidden=search.hidden=next.mode==='all';
      docSelection.textContent=next.document ? `${next.document.title} · 版本 ${next.document.version_id}` : '请选择一个文档版本。';
      if (reset) {
        searchRequest++;
        searching=false;documentButtons=[];
        searchResults.hidden=searchFeedback.hidden=true;
        searchResults.replaceChildren();
        clearRound(preserveFeedback);
      } else sync();
    }

    function settleWriting() {
      writing=busy=false;
      if (queuedScope) {
        const next=queuedScope;
        queuedScope=null;
        const reset=next.mode!==scopeState.mode || next.document?.id!==scopeState.document?.id
          || next.document?.version_id!==scopeState.document?.version_id;
        changeScope(next,reset,true);
        if (reset) return;
      }
      render();
    }

    scopeSelect.addEventListener('change',()=>{
      if (!mounted() || writing) return;
      const mode=scopeSelect.value;
      if (mode===scopeState.mode) return;
      scannerScope={...scopeState,mode};
      changeScope(scannerScope,true);
    });

    search.addEventListener('submit',async event=>{
      event.preventDefault();
      if (!mounted() || writing || scopeState.mode==='all') return;
      const term=query.value.trim();
      searchFeedback.hidden=false;
      searchResults.hidden=true;
      searchResults.replaceChildren();
      documentButtons=[];
      const request=++searchRequest;
      searching=Boolean(term);sync();
      if (!term) {searchFeedback.textContent='请输入文档关键词。';return;}
      searchFeedback.textContent='正在查找文档…';
      try {
        const result=await api(endpoint('/sources?summary_only=true&state=all&limit=20&query='+encodeURIComponent(term)),{});
        if (!mounted() || request!==searchRequest) return;
        const docs=(result.documents || []).filter(row=>row.id && row.version_id);
        searchFeedback.textContent=docs.length ? '选择一个匹配的文档版本。' : '未找到有可用版本的文档。';
        for (const row of docs) {
          const title=row.title || row.metadata?.title || row.text || row.id;
          const item=el('li'),choose=button(title);
          choose.setAttribute('aria-pressed',String(scopeState.document?.id===row.id && scopeState.document?.version_id===row.version_id));
          choose.append(el('small','duplicate-muted',row.version_id));
          choose.addEventListener('click',()=>{
            if (!mounted() || writing || request!==searchRequest) return;
            scannerScope={...scopeState,document:{id:row.id,version_id:row.version_id,title}};
            changeScope(scannerScope,true);
            scan.focus?.();
          });
          item.append(choose);searchResults.append(item);documentButtons.push(choose);
        }
        searchResults.hidden=!docs.length;
      } catch(error) {
        if (mounted() && request===searchRequest) searchFeedback.textContent='文档查找失败：'+error.message;
      } finally {
        if (mounted() && request===searchRequest) {searching=false;sync();}
      }
    });

    function sync() {
      const active=groups.filter(group=>!deferred.has(group.key) && !group.review_receipt && !group.rescanRequired);
      const chosen=active.filter(group=>selected.has(group.key));
      const occupied=new Set(chosen.flatMap(group=>(group.members || []).map(row=>row.id)));
      const unpreviewed=active.some(group=>!needsMemberReview(group) && !isPreviewed(group));
      const writable=Boolean(window.Auth?.isAdmin?.());
      scopeSelect.disabled=query.disabled=writing;
      searchButton.disabled=writing || searching;
      for (const choose of documentButtons) choose.disabled=writing;
      scan.disabled=busy || (scopeState.mode!=='all' && !scopeState.document);
      more.hidden=!cursor;more.disabled=busy;
      candidateActions.hidden=!active.length;
      mergeGuide.hidden=!active.length;
      selectPreview.hidden=batch.hidden=!unpreviewed;
      selectPreview.disabled=busy;
      batch.disabled=busy || !previewKeys.size;
      batch.textContent=`批量预览（${previewKeys.size}/20）`;
      differenceLabel.hidden=!active.some(isPreviewed);
      for (const {group,previewChoice,mergeChoice,mergeHint,previewButton,reviewButton,deferButton} of controls) {
        previewChoice.disabled=busy || needsMemberReview(group) || isPreviewed(group) || (!previewKeys.has(group.key) && previewKeys.size>=20);
        previewChoice.checked=previewKeys.has(group.key);
        previewButton.disabled=busy || needsMemberReview(group);
        reviewButton.disabled=busy;
        deferButton.disabled=busy;
        const checked=selected.has(group.key);
        const overlap=!checked && overlaps(group,occupied);
        mergeChoice.checked=checked;
        mergeChoice.disabled=busy || !writable || !isPreviewed(group) || overlap || (!checked && selected.size>=20);
        mergeHint.hidden=!overlap;
      }
      plan.hidden=!writable || (!chosen.length && !mergeReceipt);
      planTitle.textContent=chosen.length?'待确认的合并影响':'合并结果';
      planDetails.hidden=ackLabel.hidden=confirm.hidden=!chosen.length;
      mergeStatus.hidden=!mergeReceipt;
      confirm.textContent=writing?'正在合并…':'确认合并所选组';
      confirm.disabled=busy || !writable || !chosen.length || !ack.checked;
      ack.disabled=busy;
      planDetails.replaceChildren();
      if (chosen.length) {
        const rows=el('ul');
        const totals={relations:0,attributes:0,conflicts:0,facts:0};
        for (const group of chosen) {
          const relations=group.affected_records?.relations ?? 0, attributes=group.affected_records?.attributes ?? 0;
          const conflicts=group.attribute_conflicts?.length || 0, facts=group.fact_deduplication_total ?? group.fact_deduplication?.length ?? 0;
          totals.relations+=relations;totals.attributes+=attributes;totals.conflicts+=conflicts;totals.facts+=facts;
          const keep=group.members?.find(row=>row.id===group.keep_id);
          const name=keep?duplicateName(keep):group.name;
          rows.append(el('li',null,`继续使用「${name}」 · ${duplicateType(group.type)} · ${relations} 条关系 · ${attributes} 条属性 · ${conflicts} 组冲突 · ${facts} 组重复事实`));
        }
        planDetails.append(el('p',null,`${chosen.length} 组 · ${totals.relations} 条关系 · ${totals.attributes} 条属性 · ${totals.conflicts} 组冲突 · ${totals.facts} 组重复事实`),
          el('small','duplicate-muted','按逐组统计相加，跨组关联的记录可能重复计入。'),rows);
      }
    }

    function focusCandidate(key) {
      const control=controls.find(item=>item.group.key===key);
      const target=control?.summary || (control?.previewButton.hidden ? control.reviewButton : control?.previewButton);
      target?.focus?.();
    }

    function discardSelection(group) {
      selected.delete(group.key);previewKeys.delete(group.key);expanded.delete(group.key);
      group.preview_fingerprint=null;group.previewError=null;
      ack.checked=false;
    }

    function protectReviewedMembers() {
      const kept=new Set(groups.flatMap(group=>(group.review_receipt?.targets || []).map(target=>target.keep_id)));
      if (!kept.size) return;
      for (const candidate of groups) {
        if (candidate.review_receipt || !overlaps(candidate,kept)) continue;
        candidate.overlapsReceipt=true;
        discardSelection(candidate);
      }
    }

    function partitionCompleted(group,receipt,members) {
      const reviewed=new Set(members.map(row=>row.id));
      group.review_receipt=receipt;
      group.members=members;
      for (const candidate of groups) {
        if (candidate!==group && !overlaps(candidate,reviewed)) continue;
        discardSelection(candidate);
        delete candidate.partitionDraft;
        deferred.delete(candidate.key);
        if (candidate!==group) candidate.rescanRequired=true;
      }
      render();
      if (!receipt.merged_count) return;
      const notify=()=>window.KnowledgeChanges
        ? window.KnowledgeChanges.notify({projectId,records:receipt.records || [],kind:'merge',preserveGovernance:true})
        : project()===projectId && reload?.();
      void Promise.resolve().then(notify).catch(()=>{if (mounted()) report('方案已保存，台账刷新失败，可重新加载查看。');});
    }

    function openReview(group) {
      const ticket=generation;
      const active=()=>currentRound(ticket);
      if (groupSize(group)>20) {
        openDuplicateMemberReview(group,projectId,active,(...args)=>pairMerged(group,...args));
        return;
      }
      openPartitionReview(group,projectId,active,{
        onWriting(value) {
          if (!active()) return;
          if (value) {writing=busy=true;sync();}
          else settleWriting();
        },
        onDraft(draft) {
          discardSelection(group);
          group.partitionDraft=draft;
        },
        onCompleted:(receipt,members)=>partitionCompleted(group,receipt,members),
        onUncertain() {
          const ids=new Set(group.members?.map(row=>row.id));
          for (const candidate of groups) {
            if (candidate!==group && !overlaps(candidate,ids)) continue;
            discardSelection(candidate);candidate.rescanRequired=true;
            delete candidate.partitionDraft;
          }
        },
        onClose() {
          render();
          const target=controls.find(item=>item.group===group)?.reviewButton;
          (target || scan).focus?.();
        },
      });
    }

    function pairMerged(group, keep, drop, result, started) {
      for (const candidate of groups) {
        const includesDrop=candidate===group || candidate.members?.some(row=>row.id===drop.id);
        if (!includesDrop && !candidate.members?.some(row=>row.id===keep.id)) continue;
        selected.delete(candidate.key);previewKeys.delete(candidate.key);expanded.delete(candidate.key);
        candidate.preview_fingerprint=null;candidate.previewError=null;candidate.reviewRequired=false;
        delete candidate.partitionDraft;
        if (candidate.review_receipt) candidate.rescanRequired=true;
        if (includesDrop) {
          const count=candidate.member_count ?? candidate.members?.length ?? 0;
          candidate.members=(candidate.members || []).filter(row=>row.id!==drop.id);
          candidate.member_count=count-1;
          candidate.blocked=candidate.member_count>20;
          if (!candidate.blocked) candidate.blocked_reason=null;
          if (candidate.member_count<2) deferred.delete(candidate.key);
        }
      }
      groups=groups.filter(candidate=>candidate.member_count==null || candidate.member_count>=2);
      ack.checked=false;
      const message=`已合并所选两条，保留 ${duplicateName(keep)} · 用时 ${mergeElapsed(started,true)}。`;
      report(message);render();
      if (groups.includes(group)) focusCandidate(group.key);
      else scan.focus?.();
      const refresh=window.KnowledgeChanges
        ? window.KnowledgeChanges.notify({projectId,records:result.records || [],kind:'merge',preserveGovernance:true})
        : reload?.();
      Promise.resolve(refresh).catch(error=>{if(mounted())report(message+'台账刷新失败：'+error.message);});
    }

    function render() {
      const focusFields=['summary','previewButton','reviewButton','deferButton','previewChoice','mergeChoice'];
      const active=document.activeElement;
      const focused=active && controls.find(item=>focusFields.some(field=>item[field]===active));
      const focusField=focused && focusFields.find(field=>focused[field]===active);
      list.replaceChildren();controls=[];
      for (const group of groups) {
        if (deferred.has(group.key)) continue;
        const ready=isPreviewed(group), requiresReview=needsMemberReview(group), card=el('article','entity-review-card duplicate-candidate');
        const header=el('div','duplicate-candidate-header');
        const heading=el('h5',null,group.name || '未命名实体');
        if (group.review_receipt || group.rescanRequired) {
          const reviewed=group.review_receipt && !group.rescanRequired;
          const state=el('span','duplicate-candidate-state',reviewed?'已完成':'需重新扫描');
          state.setAttribute('data-state',reviewed?'ready':'review');
          header.append(heading,state);card.append(header);
          if (reviewed) card.append(partitionReceipt(group,group.review_receipt));
          else {
            const rescan=button('重新扫描候选组');rescan.disabled=busy;
            rescan.addEventListener('click',()=>read(true));
            card.append(el('p','duplicate-muted','组内成员已处理或提交结果待核对，请重新扫描后继续。'),rescan);
          }
          list.append(card);continue;
        }
        const state=el('span','duplicate-candidate-state',ready?'已预览':requiresReview?'需要人工复核':group.previewError?'预览失败':'待预览');
        state.setAttribute('data-state',ready?'ready':requiresReview?'review':group.previewError?'failed':'pending');
        header.append(heading,state);
        const matchLabels={name:'规范名称或别名相同',key:'唯一键相同',name_alias:'规范名称或别名相同',identity_key:'唯一键相同'};
        const reason=group.reasons?.join('；') || matchLabels[group.match_kind] || (group.match_kind?.startsWith('key:')
          ? `唯一键 ${duplicateType(group.match_kind.slice(4))} 相同` : '规范名称、别名或身份键匹配');
        card.append(header,el('p','duplicate-muted',`${duplicateType(group.type)} · ${group.member_count ?? group.members?.length ?? 0} 个实体 · ${reason}`));
        if (group.previewError || group.blocked) card.append(el('p','entity-governance-feedback',group.previewError || group.blocked_reason || '需要单独复核本组。'));
        if (group.partitionDraft) {
          const {assignments,kept_count}=group.partitionDraft;
          card.append(el('p','duplicate-member-role',partitionCounts(Object.keys(assignments).length,groupSize(group),kept_count)));
        } else if (group.overlapsReceipt) {
          card.append(el('p','duplicate-muted','本组含有已复核保留的实体，请逐条安排。'));
        }

        const actions=el('div','duplicate-card-actions');
        const previewChoice=checkbox('duplicate-preview-choice'),mergeChoice=checkbox('duplicate-merge-choice');
        const previewLabel=el('label','entity-governance-choice'),mergeLabel=el('label','entity-governance-choice');
        previewLabel.append(previewChoice,el('span',null,'加入批量预览'));
        mergeLabel.append(mergeChoice,el('span',null,'选中合并'));
        // Keep confirmation visible as unavailable until the group is previewed.
        previewLabel.hidden=ready || requiresReview;
        mergeLabel.hidden=!window.Auth?.isAdmin?.() || requiresReview;
        const mergeHint=el('small','duplicate-muted','与已选组含有相同实体');
        previewChoice.addEventListener('change',()=>{
          if (previewChoice.checked && previewKeys.size<20 && !isPreviewed(group)) previewKeys.add(group.key);
          else previewKeys.delete(group.key);
          sync();
        });
        mergeChoice.addEventListener('change',()=>{
          if (!mergeChoice.disabled && mergeChoice.checked) selected.add(group.key);
          else selected.delete(group.key);
          ack.checked=false;
          sync();
        });
        const previewButton=button(ready?'重新预览':'预览本组影响');
        previewButton.hidden=requiresReview;
        previewButton.addEventListener('click',()=>loadPreviews([group.key],false,previewButton));
        const reviewButton=button('复核组内成员',requiresReview || groupSize(group)>2?'primary':'secondary');
        reviewButton.addEventListener('click',()=>{if (!busy && mounted()) openReview(group);});
        const deferButton=button('本轮暂缓');
        deferButton.addEventListener('click',()=>{
          deferred.add(group.key);previewKeys.delete(group.key);selected.delete(group.key);ack.checked=false;
          deferredSection.open=true;
          render();
          deferredSummary.focus?.();
        });
        const control={group,previewChoice,mergeChoice,mergeHint,previewButton,reviewButton,deferButton};
        controls.push(control);
        actions.append(reviewButton,previewLabel,mergeLabel,previewButton,deferButton,mergeHint);
        card.append(actions);
        if (ready) {
          const comparison=el('details','duplicate-preview-detail');
          const ticket=generation, fingerprint=group.preview_fingerprint;
          let loaded=false;
          const showDetail=()=>{
            if (loaded) return;
            loaded=true;
            comparison.append(duplicateComparison(group,onlyDifferences.checked,projectId,()=>currentRound(ticket)&&
              group.preview_fingerprint===fingerprint&&!deferred.has(group.key)&&comparison.isConnected!==false,report),duplicateImpact(group));
          };
          comparison.open=expanded.has(group.key);
          control.summary=el('summary',null,'核对身份、来源和合并影响');
          comparison.append(control.summary);
          if (comparison.open) showDetail();
          comparison.addEventListener('toggle',()=>{
            if (comparison.isConnected===false) return;
            if (comparison.open) {expanded.add(group.key);showDetail();}
            else expanded.delete(group.key);
          });
          card.append(comparison);
        }
        list.append(card);
      }
      if (mergeReceipt && !groups.length) list.append(el('p','duplicate-muted','本轮列出的候选组已处理。'));
      deferredSection.hidden=!deferred.size;
      deferredSummary=el('summary',null,`本轮暂缓 · ${deferred.size} 组`);
      deferredSection.replaceChildren(deferredSummary,el('p','duplicate-muted','暂缓的组可在这里随时恢复。本轮结束后，重新扫描仍会检查这些实体。'));
      for (const group of groups.filter(group=>deferred.has(group.key))) {
        const row=el('div','duplicate-deferred-row'),restore=button('恢复本轮复核');
        restore.disabled=busy;
        restore.addEventListener('click',()=>{deferred.delete(group.key);render();focusCandidate(group.key);});
        row.append(el('span',null,`${group.name} · ${duplicateType(group.type)}`),restore);
        deferredSection.append(row);
      }
      sync();
      const replacement=focused && controls.find(item=>item.group.key===focused.group.key)?.[focusField];
      if (replacement && !replacement.disabled && !replacement.hidden) replacement.focus?.();
    }

    async function read(reset) {
      if (busy || !mounted() || scan.disabled) return;
      if (reset) clearRound();
      const ticket=generation;
      const payload={cursor,limit:20};
      if (scopeState.mode!=='all') Object.assign(payload,{document_id:scopeState.document.id,
        document_version_id:scopeState.document.version_id,document_mode:scopeState.mode});
      busy=true;sync();report('正在扫描重复实体…');
      try {
        const page=await api(endpoint('/duplicates/scan'),payload);
        if (!currentRound(ticket)) return;
        const seen=new Set(groups.map(group=>group.key));
        groups.push(...(page.groups || []).filter(group=>!seen.has(group.key)).map(group=>({...group})));
        protectReviewedMembers();
        cursor=page.next_cursor || null;
        const count=`已找到 ${groups.length} 组疑似重复实体`;
        report(cursor ? `${count}，可继续扫描。` : groups.length ? `扫描完成，${count}。` : '当前范围未发现重复实体组。');
      } catch(error) {
        if (currentRound(ticket)) report('扫描失败：'+error.message);
      } finally {
        if (currentRound(ticket)) {busy=false;render();}
      }
    }

    async function loadPreviews(keys,batchMode=false,trigger=null) {
      if (busy || !mounted() || !keys.length) return;
      const ticket=generation;
      const preserveTrigger=trigger && document.activeElement===trigger;
      busy=true;ack.checked=false;sync();report('正在读取各组的合并影响…');
      try {
        const results=batchMode
          ? (await api(endpoint('/duplicates/preview-batch'),{keys})).results
          : [{status:'ready',...await api(endpoint('/duplicates/preview'),{key:keys[0]})}];
        if (!currentRound(ticket)) return;
        const byKey=new Map((results || []).map(item=>[item.key,item]));
        let readyCount=0,reviewCount=0;
        for (const key of keys) {
          const group=groups.find(item=>item.key===key),result=byKey.get(key);
          if (!group) continue;
          selected.delete(key);previewKeys.delete(key);
          if (result?.status==='ready' && isPreviewed(result)) {
            Object.assign(group,result,{previewError:null,reviewRequired:false});
            if (!batchMode || keys.length===1) expanded.add(key);
            readyCount++;
          } else {
            group.preview_fingerprint=null;
            group.reviewRequired=result?.status==='review_required';
            if (group.reviewRequired) reviewCount++;
            group.previewError=result?.error || '未收到有效的预览结果，可重试本组。';
          }
        }
        const failedCount=keys.length-readyCount-reviewCount;
        report(`已预览 ${readyCount} 组${reviewCount?` · ${reviewCount} 组可逐对复核`:''}${failedCount?` · ${failedCount} 组失败，可重试`:''}。`);
      } catch(error) {
        if (currentRound(ticket)) {
          if (!batchMode) {
            const group=groups.find(item=>item.key===keys[0]);
            group.preview_fingerprint=null;group.previewError=error.message;selected.delete(group.key);
          }
          report('预览失败：'+error.message);
        }
      } finally {
        if (currentRound(ticket)) {
          const restoreFocus=preserveTrigger && (document.activeElement===trigger || document.activeElement===document.body);
          busy=false;render();
          if (restoreFocus) focusCandidate(keys[0]);
        }
      }
    }

    confirm.addEventListener('click',async()=>{
      if (!ack.checked || busy || !mounted() || !window.Auth?.isAdmin?.()) return;
      const ticket=generation, attempt=++mergeAttempt, reloadRecords=reload;
      let submittedKeys=[], merged=[], stopClock;
      const started=Date.now();
      writing=busy=true;
      try {
        const payload=duplicateConfirmationPayload(groups,selected);
        submittedKeys=payload.groups.map(group=>group.key);
        mergeReceipt=true;report('');sync();
        stopClock=showMergeProgress(mergeStatus,`正在合并 ${submittedKeys.length} 组`,started);
        const result=await api(endpoint('/duplicates/confirm'),payload);
        const results=result.results;
        const resultKeys=new Set(results.map(item=>item.key));
        if (results.length!==submittedKeys.length || submittedKeys.some(key=>!resultKeys.has(key)))
          throw new Error('未收到完整的逐组回执。');
        merged=results.filter(item=>item.status==='merged');
        submittedKeys=[];
        if (currentRound(ticket)) {
          const complete=merged.length===results.length;
          mergeStatus.setAttribute('data-state',complete?'complete':'partial');
          mergeStatus.replaceChildren(el('strong',null,`${complete?'合并完成':'处理结束'} · ${merged.length}/${results.length} 组成功 · 用时 ${mergeElapsed(started,true)}`));
          const outcomes=el('ul');
          for (const item of results) {
            const outcome=item.status==='merged'?'已融合':item.refresh_required?'结果待核对':'未合并，可重新预览';
            outcomes.append(el('li',null,`${outcome}：${item.name || item.key}${item.error?' '+item.error:''}`));
            selected.delete(item.key);
            if (item.status==='merged') {
              groups=groups.filter(group=>group.key!==item.key);
              expanded.delete(item.key);
            } else {
              const group=groups.find(group=>group.key===item.key);
              if (group) {group.preview_fingerprint=null;group.previewError=item.error || '确认失败，请重新预览。';}
            }
          }
          mergeStatus.append(outcomes);
          ack.checked=false;
        }
      } catch(error) {
        if (currentRound(ticket)) {
          ack.checked=false;
          if (submittedKeys.length) {
            for (const key of submittedKeys) {
              const group=groups.find(item=>item.key===key);
              group.preview_fingerprint=null;selected.delete(key);
              group.previewError='提交结果待核对，请重新扫描后预览。';
            }
            mergeStatus.setAttribute('data-state','partial');
            mergeStatus.textContent='无法确认提交结果。请刷新或重新扫描受影响的组，再预览后重试。'+error.message;
          } else report(error.message);
        }
      } finally {
        stopClock?.();
        if (currentRound(ticket)) settleWriting();
      }
      if (merged.length) {
        // The commit response controls the receipt. The ledger/graph may refresh independently.
        const refresh=async()=>window.KnowledgeChanges
          ? window.KnowledgeChanges.notify({projectId,records:merged.flatMap(item=>item.records || []),kind:'merge',preserveGovernance:true})
          : project()===projectId&&reloadRecords ? (await reloadRecords())===false : false;
        const reportRefreshFailure=()=>{
          if (currentRound(ticket)&&attempt===mergeAttempt&&mergeReceipt)
            mergeStatus.append(el('p','duplicate-muted','合并结果已保存。台账或图谱刷新失败，请重新加载查看。'));
        };
        void refresh().then(failed=>{if(failed===true)reportRefreshFailure();},reportRefreshFailure);
      }
    });

    scan.addEventListener('click',()=>read(true));
    more.addEventListener('click',()=>read(false));
    ack.addEventListener('change',sync);
    onlyDifferences.addEventListener('change',render);
    selectPreview.addEventListener('click',()=>{
      const eligible=groups.filter(group=>!needsMemberReview(group) && !isPreviewed(group) && !deferred.has(group.key));
      for (const group of eligible.slice(0,20)) {
        if (previewKeys.size>=20) break;
        previewKeys.add(group.key);
      }
      sync();
    });
    batch.addEventListener('click',()=>loadPreviews(groups.filter(group=>previewKeys.has(group.key)).map(group=>group.key),true));
    section.append(el('h4',null,'扫描已有重复实体'),scanToolbar,feedback,candidateActions,mergeGuide,list,deferredSection,plan);
    if (scannerScope.projectId!==projectId) scannerScope={projectId,mode:'all',document:null};
    applyScannerScope=changeScope;
    changeScope(scannerScope,true);
    return section;
  }

  async function refresh() {
    if (!host) return;
    const request=++serial,p=project();applyScannerScope=null;
    dismissStalePartition?.();
    if (!p) {host.replaceChildren(el('p',null,'请先选择项目。'));return;}
    const queue=el('section','entity-resolution-queue');
    queue.append(el('p',null,'正在读取入库消歧复核…'));
    host.replaceChildren(duplicateScanner(p,request),queue);
    try {
      const data=await api(endpoint('/resolution-reviews'),undefined,'GET');
      if (serial!==request||project()!==p) return;
      if(!Array.isArray(data?.reviews))throw Error('服务未返回复核清单，请重新读取。');
      const pending=data.reviews.filter(item=>item.status==='pending');
      const title=el('h4',null,`入库消歧复核 · ${pending.length} 条待处理`);
      queue.replaceChildren(title,el('p',null,'仅统计入库时产生的候选。'));
      if (pending.length) queue.append(el('p',null,'评分仅作候选线索。合并、保持独立或驳回候选均记录原因，拒绝不删除来源。'));
      const decisions={merged:'合并',separate:'保持独立',rejected:'驳回候选'};
      for (const review of pending) {
        const card=el('article','entity-review-card');
        card.append(el('b',null,`${review.source_entity_id} ↔ ${review.candidate_entity_id}`),el('small',null,`候选分数 ${review.score} · 决策修订 ${review.decision_version}`));
        const evidence=el('details'),summary=el('summary',null,'候选依据与来源差异');
        evidence.append(summary,el('pre',null,JSON.stringify(review.payload||{},null,2)));card.append(evidence);
        const reason=el('textarea');reason.placeholder='必填：复核原因';reason.rows=2;
        const feedback=el('p','entity-governance-feedback');feedback.setAttribute('role','status');
        const actions=el('div','entity-governance-actions');
        for (const [decision,label] of Object.entries(decisions)) {
          const button=el('button','secondary',label);button.type='button';
          button.addEventListener('click',async()=>{
            try {
              if (!reason.value.trim()) throw new Error('请填写复核原因。');
              actions.querySelectorAll('button').forEach(item=>item.disabled=true);
              if (await decide(review,decision,reason.value)) {await refresh();if(reload)await reload();}
            } catch(error) {feedback.textContent=error.message;}
            finally {actions.querySelectorAll('button').forEach(item=>item.disabled=false);}
          });actions.append(button);
        }
        card.append(reason,actions,feedback);queue.append(card);
      }
      const history=data.reviews.filter(item=>item.status!=='pending');
      if(history.length){const details=el('details'),summary=el('summary',null,`复核历史 · ${history.length}`);details.append(summary);
        for(const item of history)details.append(el('p',null,`${decisions[item.status]||item.status} · ${item.reason||'原因未提供'} · ${item.actor||''} · ${window.DisplayTime.format(item.decided_at,'')}`));queue.append(details);}
    } catch(error){if(serial===request&&project()===p)queue.replaceChildren(el('p',null,'消歧复核读取失败：'+error.message));}
  }
  const apiObject={createWinnerSelection,reviewDecisionPayload,duplicateConfirmationPayload,conflictDecisionPayload,prepareMerge,decide,refresh,
    setDocumentScope,mount(target,options={}){host=target;reload=options.reload||null;return refresh();}};
  if (typeof module !== 'undefined' && module.exports) module.exports=apiObject;
  if (typeof window !== 'undefined') window.EntityGovernance=apiObject;
})();
