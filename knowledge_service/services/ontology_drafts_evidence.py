"""把操作上的机器证据引用还原成审核人看得懂的一条条来源。
"""

from __future__ import annotations

import json



class DraftEvidenceMixin:
    """把操作上的机器证据引用还原成审核人看得懂的一条条来源。"""

    def evidence_index(self, project_id, draft_id):
        """把操作上的证据引用翻译成审核人看得懂的一条条来源。

        草案操作里的 ``evidence`` 是一串机器引用（``document-version:<文档>:<版本>``、
        ``candidate:<候选 id>``）。原样展示等于没展示——审核人看到的是
        "来源快照 / 置信度 — / 未保存摘录"这种占位文案。这里用**发现运行时冻结的
        候选快照**（``candidate_snapshot``）把引用还原成：文档标题、片段范围、
        置信度与原文句子。

        只读，不推导、不补全：解析不到的引用如实标 ``resolved=False`` 并保留原始
        引用串，绝不用当前数据去"猜"历史证据。
        """
        draft = self.store.get(project_id, draft_id)
        candidates = {}
        document_titles = {}
        for run in self.repository.list_discovery_runs(project_id):
            for candidate in run.get('candidate_snapshot') or []:
                if not isinstance(candidate, dict):
                    continue
                candidate_id = candidate.get('id') or candidate.get('assertion_id')
                if candidate_id and candidate_id not in candidates:
                    candidates[candidate_id] = candidate
                document_id = candidate.get('document_id')
                if document_id and document_id not in document_titles:
                    document_titles[document_id] = candidate.get('document_title')
        resolved = unresolved = 0
        rows = {}
        for operation in self._active_operations(project_id, draft_id):
            entries = []
            for raw in operation.get('evidence') or []:
                entry = self._evidence_entry(raw, candidates, document_titles)
                if entry['resolved']:
                    resolved += 1
                else:
                    unresolved += 1
                entries.append(entry)
            rows[operation['id']] = entries
        return {
            'draft_id': draft_id,
            'counts': {'resolved': resolved, 'unresolved': unresolved},
            'explanation': (
                '每条变更下面的「来源」是发现阶段冻结的原文证据：文档、片段范围、'
                '置信度和命中的原句。解析不到的会标「仅引用」，表示该证据只留下了 '
                'ID，当前无法还原成原文。'),
            'items': rows,
        }

    @staticmethod
    def _evidence_entry(raw, candidates, document_titles):
        ref = raw.get('ref') if isinstance(raw, dict) else raw
        ref = ref if isinstance(ref, str) else json.dumps(
            raw, ensure_ascii=False, sort_keys=True)
        kind, _, rest = ref.partition(':')
        if kind == 'candidate':
            candidate = candidates.get(rest)
            if candidate:
                title = candidate.get('document_title') or document_titles.get(
                    candidate.get('document_id'))
                return {
                    'ref': ref, 'kind': 'candidate', 'resolved': True,
                    'document_title': title,
                    'document_id': candidate.get('document_id'),
                    'document_version_id': candidate.get('document_version_id'),
                    'chunk_id': candidate.get('chunk_id') or candidate.get(
                        'assertion_chunk_id'),
                    'start_char': candidate.get('start_char'),
                    'end_char': candidate.get('end_char'),
                    'confidence': candidate.get('confidence'),
                    'evidence': candidate.get('evidence'),
                    'evidence_status': candidate.get('evidence_status'),
                    'candidate_kind': candidate.get('kind'),
                    'proposed_type': candidate.get('proposed_type'),
                    'assertion_id': candidate.get('assertion_id'),
                }
        if kind == 'document-version':
            document_id, _, version_id = rest.partition(':')
            title = document_titles.get(document_id)
            return {
                'ref': ref, 'kind': 'document_version',
                'resolved': bool(document_id),
                'document_title': title, 'document_id': document_id or None,
                'document_version_id': version_id or None,
            }
        return {'ref': ref, 'kind': 'unknown', 'resolved': False}
