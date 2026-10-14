"""Authorized fixed-version source reads and audited OCR decisions."""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .audit_identity import audit_actor
from ..services.source_documents import SourceDocumentService, SourceOCRError


class IgnoreImage(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def install(app, service):
    sources = getattr(app.state, 'source_documents', None)
    if sources is None:
        sources = SourceDocumentService.for_service(service)
        app.state.source_documents = sources
    router = APIRouter(prefix='/api/projects/{project_id}/source-documents')

    @router.get('/{version_id}')
    def metadata(project_id: str, version_id: str):
        result = sources.get(project_id, version_id)
        return {key: value for key, value in result.items() if key != 'storage_key'}

    @router.get('/{version_id}/rendered', response_class=HTMLResponse)
    def rendered(project_id: str, version_id: str):
        result = sources.rendered(project_id, version_id)
        return HTMLResponse(result['html'], headers={
            'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'none'",
            'X-Content-Type-Options': 'nosniff'})

    @router.get('/{version_id}/source')
    def source(project_id: str, version_id: str):
        result = sources.source(project_id, version_id)
        return Response(result['data'], media_type=result['mime_type'], headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(result['original_name'], safe=''),
            'X-Content-Type-Options': 'nosniff'})

    @router.get('/{version_id}/package')
    def package(project_id: str, version_id: str):
        result = sources.source(project_id, version_id)
        if not result.get('package_data'):
            raise HTTPException(status_code=404, detail='该来源没有 ZIP 知识包')
        return Response(result['package_data'], media_type='application/zip', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote('knowledge-package.zip', safe=''),
            'X-Content-Type-Options': 'nosniff'})

    @router.get('/{version_id}/assets/{asset_id}')
    def asset(project_id: str, version_id: str, asset_id: str):
        result = sources.asset(project_id, version_id, asset_id)
        return Response(result['data'], media_type=result['mime_type'], headers={
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=86400'})

    @router.get('/{version_id}/assets/{asset_id}/ocr')
    def ocr(project_id: str, version_id: str, asset_id: str):
        result = sources.ocr(project_id, version_id, asset_id)
        if 'runs' in result:
            runs = result['runs']
            return {'run': runs[-1] if runs else {'status': 'pending'},
                    'blocks': runs[-1].get('blocks', []) if runs else [], 'runs': runs}
        return result

    @router.post('/{version_id}/assets/{asset_id}/ocr/retry')
    def retry_ocr(project_id: str, version_id: str, asset_id: str):
        try:
            return sources.image_ocr.process_asset(project_id, version_id, asset_id, retry=True)
        except SourceOCRError as exc:
            raise HTTPException(status_code=422, detail={'code': exc.code, 'message': str(exc)}) from exc

    @router.post('/{version_id}/assets/{asset_id}/ocr/ignore')
    def ignore_ocr(project_id: str, version_id: str, asset_id: str, body: IgnoreImage, request: Request):
        return sources.image_ocr.ignore(project_id, version_id, asset_id, audit_actor(request), body.reason)

    @router.get('/{version_id}/locate')
    def locate(project_id: str, version_id: str, chunk_version_id: str | None = None,
               assertion_id: str | None = None):
        return sources.locate(project_id, version_id, chunk_version_id, assertion_id)

    app.include_router(router)
