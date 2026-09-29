"""证据定位路由（GET assertion evidence 与 POST formal-record evidence）。

业务逻辑在 services.evidence；本文件只把 HTTP 请求转成对业务函数的调用。
"""
from ..models import Scope
from ..services.evidence import evidence, resolve_assertion_evidence


def install(app, service):
    @app.get('/api/projects/{p}/assertions/{assertion_id}/evidence')
    def assertion_evidence(p: str, assertion_id: str):
        return resolve_assertion_evidence(service.repository, p, assertion_id)

    @app.post('/api/projects/{p}/records/{record_id}/evidence')
    def source_evidence(p: str, record_id: str, request: Scope):
        return evidence(service, p, record_id, request.model_dump())
