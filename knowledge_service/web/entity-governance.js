/* Authoritative fusion preview and resolution decisions shared by the ledger. */
(() => {
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
    await api(base + '/resolution-reviews/' + encodeURIComponent(review.id), reviewDecisionPayload(review,decision,reason,expected,selection));
    assertProject(p); return true;
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
  const apiObject={createWinnerSelection,reviewDecisionPayload,prepareMerge,decide,refresh,
    mount(target,options={}){host=target;reload=options.reload||null;return refresh();}};
  if (typeof module !== 'undefined' && module.exports) module.exports=apiObject;
  if (typeof window !== 'undefined') window.EntityGovernance=apiObject;
})();
