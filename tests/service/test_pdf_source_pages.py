"""PDF evidence keeps its actual page after Markdown/media normalization."""
from knowledge_service.repository import Repository
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.services.source_documents import SourceDocumentService
from knowledge_service.services.chunking import split_source_blocks
from knowledge_service.models import UploadOptions
from pg_support import target


def test_native_pdf_text_page_is_carried_into_source_spans(tmp_path):
    repo=Repository(target(tmp_path/'pdf-pages'))
    try:
        project=repo.create_project('PDF pages')['id']
        service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
        provenance={'items':[{'text':'Second page rule','provenance':[{'page_no':2,'bbox':{'l':10,'t':10,'r':200,'b':30}}]}]}
        source=service.persist(project,'doc',b'%PDF-fixture','rules.pdf',text='Second page rule',provenance=provenance)
        assert source['blocks'][0]['payload']['source_locations'][0]['page_no']==2
        chunks=split_source_blocks(source['blocks'],UploadOptions(title='PDF').model_dump())
        assert chunks[0]['source_spans'][0]['payload']['page_no']==2
        chunk=repo.put_record(project,{'id':'pdf-chunk','kind':'chunk','text':chunks[0]['text']})
        service.store.add_spans(project,source['id'],chunk['version_id'],chunks[0]['source_spans'])
        location=service.locate(project,source['id'],chunk['version_id'])
        assert location['spans'][0]['payload']['page_no']==2
    finally:
        repo.close()


def test_native_pdf_table_keeps_page_provenance(tmp_path):
    from docling_core.types.doc import DoclingDocument,TableData,TableCell,Size,ProvenanceItem,BoundingBox,CoordOrigin
    from knowledge_service.integrations.document_media import normalize_docling_document
    document=DoclingDocument(name='Table source')
    document.add_page(page_no=3,size=Size(width=600,height=800))
    box=BoundingBox(l=20,t=20,r=400,b=100,coord_origin=CoordOrigin.TOPLEFT)
    document.add_table(data=TableData(num_rows=2,num_cols=1,table_cells=[
        TableCell(text='Rule',start_row_offset_idx=0,end_row_offset_idx=1,start_col_offset_idx=0,end_col_offset_idx=1,column_header=True),
        TableCell(text='Keep the evidence',start_row_offset_idx=1,end_row_offset_idx=2,start_col_offset_idx=0,end_col_offset_idx=1)]),
        prov=ProvenanceItem(page_no=3,bbox=box,charspan=(0,0)))
    text,assets,provenance,_=normalize_docling_document(document)
    repo=Repository(target(tmp_path/'pdf-table-pages'))
    try:
        project=repo.create_project('PDF table')['id']
        service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
        source=service.persist(project,'table',b'%PDF-table-fixture','rules.pdf',text=text,assets=assets,provenance=provenance)
        table=next(block for block in source['blocks'] if block['type']=='table')
        assert table['payload']['source_locations'][0]['page_no']==3
    finally:
        repo.close()
