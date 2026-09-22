"""知识审核的路由层（/api/projects/{p}/reviews）。

业务逻辑与请求模型在 services.reviews；本文件只负责挂载审核列表与决策端点。
"""
import hashlib

from fastapi import APIRouter

from ..services.reviews import Decision, decide


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}')

    @router.get('/reviews')
    def listing(p: str):
        result = []
        rows = service.repository.current_records(p)
        by_id = {r['id']: r for r in rows}
        for doc in rows:
            if doc['kind'] != 'document' or doc.get('metadata', {}).get('_deleted'):
                continue
            for c in doc.get('metadata', {}).get('review_candidates', []):
                changed = hashlib.sha256(doc['text'].encode('utf-8')).hexdigest() != c['source_hash']
                dependencies = []
                for key in ('entity_id', 'subject_id', 'object_id'):
                    if key not in c:
                        continue
                    rid = c[key]; seen = set()
                    while rid not in seen:
                        seen.add(rid); row = by_id.get(rid)
                        if row and row.get('metadata', {}).get('_deleted') and row['metadata'].get('merged_into'):
                            rid = row['metadata']['merged_into']; continue
                        break
                    row = by_id.get(rid)
                    dependencies.append(row if row and not row.get('metadata', {}).get('_deleted') else None)
                entity = dependencies[0] if c.get('kind') == 'attribute' and dependencies else None
                result.append({**c, 'kind': c.get('kind', 'relation'),
                    'blocked': any(r is None for r in dependencies),
                    'entity_version': entity['version'] if entity else None,
                    'entity_type': entity.get('type') if entity else None,
                    'subject_type': dependencies[0].get('type') if c.get('kind') == 'relation' and len(dependencies) > 0 and dependencies[0] else c.get('subject_type'),
                    'object_type': dependencies[1].get('type') if c.get('kind') == 'relation' and len(dependencies) > 1 and dependencies[1] else c.get('object_type'),
                    'current_properties': entity.get('properties', {}) if entity else {},
                    'document_id': doc['id'], 'document_title': doc['metadata'].get('title', doc['id']),
                    'document_version': doc['version'], 'source_changed': changed,
                    'evidence': c.get('evidence', '' if changed else doc['text'][c['start_char']:c['end_char']])})
        return {'reviews': result}

    @router.post('/reviews/{doc_id}/{candidate_id}')
    def decision(p: str, doc_id: str, candidate_id: str, request: Decision):
        return decide(service, p, doc_id, candidate_id, request)

    app.include_router(router)
