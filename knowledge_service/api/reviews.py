"""知识审核的路由层（/api/projects/{p}/reviews）。

业务逻辑与请求模型在 services.reviews；本文件只负责挂载审核列表与决策端点。
"""
from fastapi import APIRouter

from ..services.reviews import Decision, decide, list_reviews


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}')

    @router.get('/reviews')
    def listing(p: str):
        return list_reviews(service, p)

    @router.post('/reviews/{doc_id}/{candidate_id}')
    def decision(p: str, doc_id: str, candidate_id: str, request: Decision):
        return decide(service, p, doc_id, candidate_id, request)

    app.include_router(router)
