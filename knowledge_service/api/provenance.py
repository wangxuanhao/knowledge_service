"""Read the immutable provenance captured for one offered answer citation."""
import re

from fastapi.responses import JSONResponse

from ..services.provenance import ProvenanceService


_CITATION = re.compile(r'E[1-9][0-9]*\Z')


def _not_found(subject):
    detail = ('未找到该项目的答案。' if subject == 'answer' else '未找到该答案的证据引用。')
    return JSONResponse(status_code=404, content={
        'code': f'provenance_{subject}_not_found', 'detail': detail})


def install(app, service):
    @app.get('/api/projects/{p}/answers/{answer_id}/evidence/{citation}/provenance')
    def answer_evidence_provenance(p: str, answer_id: str, citation: str):
        try:
            answer = service.repository.get_provenance_activity(p, answer_id)
        except KeyError:
            return _not_found('answer')
        if answer['kind'] != 'answer':
            return _not_found('answer')
        if not _CITATION.fullmatch(citation):
            return _not_found('citation')
        try:
            return ProvenanceService(service.repository).trace_answer_evidence(p, answer_id, citation)
        except KeyError:
            return _not_found('citation')
