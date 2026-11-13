/* Authoritative fusion preview and resolution decisions shared by the ledger. */
(() => {
  function duplicateConfirmationPayload(groups, selected) {
    const chosen = groups.filter(group => selected.has(group.key));
    if (!chosen.length) throw new Error('请先选择已预览的重复实体组。');
    if (chosen.some(group => !/^[a-f0-9]{64}$/.test(group.preview_fingerprint || '')))
      throw new Error('请先预览每个选中的重复实体组。');
    return {groups:chosen.map(({key,preview_fingerprint})=>({key,preview_fingerprint}))};
  }
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
  let host = null, reload = null, serial = 0;
  const assertProject = expected => { if (project() !== expected) throw new Error('项目已切换，请重新打开治理流程。'); };

  async function preview(keepId, dropId) {
    const p = project(), base = endpoint('');
    const [keep, drop] = await Promise.all([keepId, dropId].map(id => api(base + '/records/' + encodeURIComponent(id), undefined, 'GET')));
    assertProject(p);
    const data = await api(base + '/merge/preview', {keep_id:keepId, drop_id:dropId,
      expected_versions:{[keepId]:keep.version,[dropId]:drop.version}});
    assertProject(p);
    return {p, base, data};
  }
  function chooseConflicts(data) {
    return new Promise(resolve => {
      const dialog = el('dialog','entity-governance-dialog');
      const selection = createWinnerSelection(data.attribute_conflicts || []);
      dialog.append(el('h3',null,'确认实体融合'), el('p',null,`${data.drop.text} → ${data.keep.text}`),
        el('p','entity-governance-impact',`服务端影响：${data.affected_records.relations} 条关系，${data.affected_records.attributes} 条属性参与检查。旧修订和来源断言保留。`));
      const compare = el('div','entity-governance-compare');
      for (const [label, row] of [['保留',data.keep],['并入',data.drop]]) {
        const item = el('div');
        item.append(el('b',null,`${label}：${row.text}`), el('small',null,`类型 ${row.type} · 修订 ${row.version} · 本体 ${row.ontology_id || '未指定'}`),
          el('small',null,`业务时间 ${row.valid_from || '不限'} — ${row.valid_until || '不限'}`),
          el('small',null,`适用范围 ${valueText(row.metadata?.applicability || {})}`));
        compare.append(item);
      }
      dialog.append(compare);
      const feedback = el('p','entity-governance-feedback'); feedback.setAttribute('role','status');
      const accept = el('button','primary','确认使用这些选择'); accept.type = 'button'; accept.disabled = true;
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
            el('small',null,`${value.id} · 修订 ${value.version ?? '未提供'} · ${value.valid_from || '不限'} — ${value.valid_until || '不限'}`)); field.append(label);
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
  function duplicateScanner() {
    const section=el('section','duplicate-scanner'),heading=el('h4',null,'扫描已有重复实体');
    const scan=el('button','secondary','扫描已有重复实体'),list=el('div'),feedback=el('p','entity-governance-feedback');
    const confirm=el('button','primary','确认选中的已预览组'),more=el('button','secondary','继续扫描');
    const ack=el('input');ack.type='checkbox';
    const ackLabel=el('label','entity-governance-choice');ackLabel.append(ack,el('span',null,'已核对每组身份与来源；冲突值隔离后在知识审核处理。'));
    scan.type=more.type=confirm.type='button';more.hidden=true;confirm.disabled=true;feedback.setAttribute('role','status');
    let groups=[],cursor=null,scanProject='',busy=false;const selected=new Set();
    const sync=()=>{const writable=Boolean(window.Auth?.isAdmin?.());confirm.hidden=ackLabel.hidden=!writable;confirm.disabled=busy||!writable||!ack.checked||!selected.size;};ack.addEventListener('change',sync);
    const render=()=>{
      list.replaceChildren();
      for(const group of groups){
        const card=el('article','entity-review-card'),choose=el('input');choose.type='checkbox';choose.checked=selected.has(group.key);choose.disabled=!group.preview_fingerprint||group.blocked;
        choose.addEventListener('change',()=>{choose.checked?selected.add(group.key):selected.delete(group.key);sync();});
        const label=el('label','entity-governance-choice');label.append(choose,el('b',null,`${group.name} · ${group.type} · ${group.member_count||group.members.length} 个实体`));card.append(label);
        card.append(el('small',null,(group.reasons||[]).join('；')||'规范名称/身份键相同，仍须核对身份。'));
        for(const row of group.members||[]){
          card.append(el('p',null,`${row.text} · ${row.id} · 修订 ${row.version}`));
          if(Array.isArray(row.source_contexts)){
            card.append(el('small',null,`来源：${row.document_count} 份文档 · ${row.support_count} 条支撑；原始时间分别保留。`));
            const contexts=el('div','duplicate-source-contexts');card.append(contexts);
            const appendContexts=items=>{for(const source of items){
              const item=el('details'),summary=el('summary',null,`${source.document_title||source.document_id||'人工声明'} · ${source.document_version_id||'无文档版本'}`);
              item.append(summary,el('p',null,`来源有效期 ${source.valid_from||'未知起点'} — ${source.valid_until||'未知终点'}`),
                el('p',null,'适用范围 '+valueText(source.applicability||{})),el('pre',null,source.quote||'未提供原文引文'));
              if(source.quote_truncated)item.append(el('small',null,'引文仅显示前2000字，完整内容请定位冻结原文。'));
              if(source.document_version_id){const open=el('button','secondary','查看冻结原文');open.type='button';open.addEventListener('click',()=>{
                try{assertProject(scanProject);if(!window.SourceReader)throw new Error('原文阅读器尚未就绪，请刷新页面。');
                  window.SourceReader.open({projectId:scanProject,versionId:source.document_version_id,location:source,assertionId:source.assertion_id,focusText:source.quote});
                }catch(error){feedback.textContent=error.message;}});item.append(open);}contexts.append(item);
            }};appendContexts(row.source_contexts);
            let sourceCursor=row.source_next_cursor||null;
            if(sourceCursor){const moreSources=el('button','secondary','更多来源时间与证据');moreSources.type='button';
              moreSources.addEventListener('click',async()=>{moreSources.disabled=true;try{
                assertProject(scanProject);const page=await api(endpoint('/duplicates/sources'),{key:group.key,preview_fingerprint:group.preview_fingerprint,
                  entity_id:row.id,expected_version_id:row.version_id,limit:10,cursor:sourceCursor});assertProject(scanProject);
                if(page.entity_id!==row.id||page.version_id!==row.version_id)throw new Error('来源响应不属于当前实体修订，请重新预览。');
                appendContexts(page.source_contexts||[]);sourceCursor=page.next_cursor||null;moreSources.hidden=!sourceCursor;
              }catch(error){feedback.textContent=error.message;}finally{moreSources.disabled=false;}});
              card.append(el('small',null,'当前仅显示前10条来源支撑，可继续分页核对。'),moreSources);
            }
          }
        }
        const previewButton=el('button','secondary',group.preview_fingerprint?'重新预览':'预览本组影响');previewButton.type='button';
        const impact=el('div');
        if(group.preview_fingerprint){impact.append(el('p',null,`保留 ${group.keep_id} · 影响 ${group.affected_records.relations} 条关系、${group.affected_records.attributes} 条属性 · ${group.attribute_conflicts.length} 组冲突`));
          for(const conflict of group.attribute_conflicts)impact.append(el('p','entity-governance-feedback',`${conflict.predicate}：${conflict.values.map(value=>valueText(value.value)+(value.value_truncated?'…（预览仅显示前240字）':'')).join(' / ')}；全部隔离，后续逐值审核。`));
          if(group.affected_record_ids){const details=el('details');details.append(el('summary',null,'受影响事实与重复支撑归并'));
            for(const kind of ['relations','attributes'])details.append(el('p',null,`${kind==='relations'?'关系':'属性'}：${group.affected_record_ids[kind].join('、')||'无'}${group.affected_records_truncated?.[kind]?'（仅显示前100条）':''}`));
            for(const fact of group.fact_deduplication||[])details.append(el('p',null,`${fact.predicate}：${fact.record_count} 条同事实归到 ${fact.survivor_id}；保留 ${fact.support_count} 条支撑、${fact.document_count} 份文档。`));
            if(group.fact_deduplication_truncated)details.append(el('p',null,`共有 ${group.fact_deduplication_total} 组重复事实，当前显示前20组。`));impact.append(details);
          }
        }
        previewButton.addEventListener('click',async()=>{previewButton.disabled=true;try{
          assertProject(scanProject);const result=await api(endpoint('/duplicates/preview'),{key:group.key});assertProject(scanProject);
          Object.assign(group,result);selected.delete(group.key);render();sync();
        }catch(error){feedback.textContent=error.message;}finally{previewButton.disabled=false;}});
        card.append(previewButton,impact);if(group.blocked)card.append(el('p','entity-governance-feedback','需要复核：'+(group.blocked_reason||'不能批量确认')));list.append(card);
      }
      if(!groups.length)list.append(el('p',null,'本页没有重复实体组。'));
    };
    const read=async(reset)=>{
      if(busy)return;busy=true;scan.disabled=more.disabled=true;sync();feedback.textContent='正在按身份范围分页扫描…';
      if(reset){groups=[];selected.clear();cursor=null;scanProject=project();ack.checked=false;}
      try{assertProject(scanProject);const page=await api(endpoint('/duplicates/scan'),{cursor,limit:20});assertProject(scanProject);
        groups.push(...page.groups);cursor=page.next_cursor;more.hidden=!cursor;render();feedback.textContent=cursor?'本页已返回，继续扫描可读取后续组。':'扫描完成；请选择组预览后确认。';
      }catch(error){feedback.textContent='扫描失败：'+error.message;}finally{busy=false;scan.disabled=more.disabled=false;sync();}
    };
    scan.addEventListener('click',()=>read(true));more.addEventListener('click',()=>read(false));
    confirm.addEventListener('click',async()=>{if(!ack.checked||busy)return;busy=true;sync();try{
      if(!window.Auth?.isAdmin?.())throw new Error('当前身份只读，无法确认融合。');
      assertProject(scanProject);const payload=duplicateConfirmationPayload(groups,selected);
      const result=await api(endpoint('/duplicates/confirm'),payload);assertProject(scanProject);
      feedback.replaceChildren();for(const item of result.results){feedback.append(el('p',null,`${item.status==='merged'?'已融合':'失败，可重新预览后重试'}：${item.name||item.key} ${item.error||''}`));
        if(item.status==='merged'){selected.delete(item.key);groups=groups.filter(g=>g.key!==item.key);}
        else{const group=groups.find(g=>g.key===item.key);if(group){group.preview_fingerprint=null;selected.delete(item.key);}}}
      render();ack.checked=false;
      const merged=result.results.filter(item=>item.status==='merged');
      if(merged.length&&window.KnowledgeChanges)await window.KnowledgeChanges.notify({projectId:scanProject,
        records:merged.flatMap(item=>item.records||[]),kind:'merge',preserveGovernance:true});
      else if(merged.length&&reload)await reload();
    }catch(error){feedback.textContent=error.message;}finally{busy=false;sync();}});
    section.append(heading,el('p',null,'按已发布身份规则、规范名称/别名或唯一键寻找候选。扫描由按钮启动；每组预览冻结成员和版本。'),scan,list,more,ackLabel,confirm,feedback);sync();return section;
  }
  async function refresh() {
    if (!host) return;
    const request=++serial,p=project();host.replaceChildren(el('p',null,'正在读取消歧复核…'));
    if (!p) {host.replaceChildren(el('p',null,'请先选择项目。'));return;}
    try {
      const data=await api(endpoint('/resolution-reviews'),undefined,'GET');
      if (serial!==request||project()!==p) return;
      const pending=(data.reviews||[]).filter(item=>item.status==='pending');
      const title=el('h4',null,`消歧复核 · ${pending.length} 条待处理`);
      host.replaceChildren(title,el('p',null,'评分仅作候选线索。合并、保持独立或驳回候选均记录原因，拒绝不删除来源。'));
      host.append(duplicateScanner());
      if (!pending.length) host.append(el('p',null,'当前没有待复核候选。'));
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
        card.append(reason,actions,feedback);host.append(card);
      }
      const history=(data.reviews||[]).filter(item=>item.status!=='pending');
      if(history.length){const details=el('details'),summary=el('summary',null,`复核历史 · ${history.length}`);details.append(summary);
        for(const item of history)details.append(el('p',null,`${decisions[item.status]||item.status} · ${item.reason||'原因未提供'} · ${item.actor||''} · ${item.decided_at||''}`));host.append(details);}
    } catch(error){if(serial===request&&project()===p)host.replaceChildren(el('p',null,'消歧复核读取失败：'+error.message));}
  }
  const apiObject={createWinnerSelection,reviewDecisionPayload,duplicateConfirmationPayload,conflictDecisionPayload,prepareMerge,decide,refresh,
    mount(target,options={}){host=target;reload=options.reload||null;return refresh();}};
  if (typeof module !== 'undefined' && module.exports) module.exports=apiObject;
  if (typeof window !== 'undefined') window.EntityGovernance=apiObject;
})();
