"""Project-scoped preview of the verified original (including ZIP primaries)."""
from ..integrations.pdf_preview import PDFPreviewError, render_pdf_page


class SourcePDFPreviewService:
    def __init__(self, sources):
        self.sources = sources

    def page(self, project_id, version_id, page_number=1):
        source = self.sources.source(project_id, version_id)
        if (source['mime_type'].split(';')[0].lower() != 'application/pdf'
                and not source['original_name'].lower().endswith('.pdf')):
            raise PDFPreviewError('source_not_pdf', '该资料原件不是 PDF', 415)
        return render_pdf_page(source['data'], page_number)
