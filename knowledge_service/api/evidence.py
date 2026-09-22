"""证据定位的路由层（POST /api/projects/{p}/records/{record_id}/evidence）。

业务逻辑在 services.evidence；本文件只把 HTTP 请求转成对业务函数的调用。
"""
from ..models import Scope
from ..services.evidence import evidence


def install(app, service):
    @app.post('/api/projects/{p}/records/{record_id}/evidence')
    def source_evidence(p: str, record_id: str, request: Scope):
        return evidence(service, p, record_id, request.model_dump())
