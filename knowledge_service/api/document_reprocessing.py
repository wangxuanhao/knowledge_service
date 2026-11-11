"""Write-authorized document processing operations; execution uses the batch runner."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator

from .audit_identity import audit_actor
from ..models import Request as RequestModel
from ..services.document_reprocessing import DocumentReprocessing, DocumentReprocessingError
from ..services.ingest_batches import public_batch, public_item


class ReprocessDocument(RequestModel):
    extraction_mode: Literal['documents', 'discovery', 'ontology']
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator('idempotency_key')
    @classmethod
    def nonempty_key(cls, value):
        if not value.strip():
            raise ValueError('处理请求编号不能为空')
        return value


def install(app, service):
    operations = DocumentReprocessing(service, app.state.ingest_batches, app.state.source_documents)
    app.state.document_reprocessing = operations
    router = APIRouter(prefix='/api/projects/{project_id}/documents')

    @router.post('/{document_id}/reprocess', status_code=202)
    def reprocess(project_id: str, document_id: str, body: ReprocessDocument, request: Request):
        try:
            result = operations.create(project_id, document_id, body.extraction_mode,
                body.idempotency_key, audit_actor(request))
        except DocumentReprocessingError as exc:
            raise HTTPException(status_code=exc.status_code, detail={
                'code': exc.code, 'message': str(exc), **exc.details}) from exc
        return {'batch': public_batch(result['batch']), 'item': public_item(result['item'])}

    app.include_router(router)
