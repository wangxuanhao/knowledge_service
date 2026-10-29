"""Batch limits and application operations; HTTP stays transport-only."""
import hashlib
import os
from pathlib import Path
from ..models import IngestCapabilities, UploadOptions
from ..integrations.document_parser import SUPPORTED_EXTENSIONS, MAX_TEXT_CHARS


def _positive(name,default):
    value=int(os.getenv(name,str(default)))
    if value<1:raise ValueError(name+' must be positive')
    return value

def _flag(name):
    value=os.getenv(name,'1').strip().lower()
    if value not in ('0','1','false','true','no','yes','off','on'):raise ValueError(name+' must be boolean')
    return value in ('1','true','yes','on')

def capabilities():
    package_enabled=_flag('KNOWLEDGE_INGEST_MARKDOWN_PACKAGE')
    return IngestCapabilities(max_batch_items=min(1000,_positive('KNOWLEDGE_INGEST_MAX_ITEMS',1000)),
        max_file_bytes=_positive('KNOWLEDGE_INGEST_MAX_FILE_BYTES',25_000_000),
        max_batch_bytes=_positive('KNOWLEDGE_INGEST_MAX_BATCH_BYTES',1_000_000_000),
        concurrency_choices=['auto',1,2,4,8],server_concurrency_limit=_positive('KNOWLEDGE_INGEST_DOCUMENT_CONCURRENCY',4),
        llm_concurrency_limit=_positive('KNOWLEDGE_LLM_CONCURRENCY',2),ocr_concurrency_limit=_positive('KNOWLEDGE_OCR_CONCURRENCY',1),
        ocr_available=_ocr_available(),ocr_languages=['zh','en'],ocr_image_extensions=['.png','.jpg','.jpeg','.webp','.bmp','.gif','.tif','.tiff'],
        ocr_max_pixels=_positive('KNOWLEDGE_OCR_MAX_PIXELS',40_000_000),
        supported_extensions=sorted(SUPPORTED_EXTENSIONS|({'.zip'} if package_enabled else set())),
        markdown_package=package_enabled,source_reader=_flag('KNOWLEDGE_INGEST_SOURCE_READER'))

def _ocr_available():
    from ..integrations.image_ocr import RapidOCRAdapter
    return RapidOCRAdapter().available()

def public_item(row):
    row={k:v for k,v in row.items() if k not in ('content','upload_object_id','storage_key','upload_storage_key','lease_owner','lease_token','lease_until')}
    row['status']={'pending':'queued','succeeded':'completed','failed':'needs_attention'}.get(row['status'],row['status'])
    return row

def public_batch(row):
    row=dict(row)
    counts=row.get('counts',{})
    row['counts']={ {'pending':'queued','succeeded':'completed','failed':'needs_attention'}.get(k,k):v for k,v in counts.items()}
    if row['status']=='completed' and counts.get('failed'):
        row['status']='completed_with_errors'
    row['actual_concurrency']=row.get('effective_concurrency',1)
    row['total_items']=row.get('total',0)
    return row

class IngestBatches:
    def __init__(self,service,store,limits=None,sources=None):
        self.service=service;self.store=store;self.limits=limits or capabilities();self.sources=sources
    def create(self,project_id,settings,actor=None):
        self.service.data.get_project(project_id)
        return self.store.create_batch(project_id,settings,actor)
    def get(self,project_id,batch_id):
        self.service.data.get_project(project_id)
        row=self.store.get_batch(project_id,batch_id)
        if row is None:raise KeyError(batch_id)
        return row
    def upload(self,project_id,batch_id,upload,key,options=None):
        self.get(project_id,batch_id)
        name=Path(upload.filename or '').name
        if not name or Path(name).suffix.lower() not in self.limits.supported_extensions:raise ValueError('不支持的文件格式')
        data=bytearray()
        while True:
            part=upload.file.read(min(1024*1024,self.limits.max_file_bytes+1-len(data)))
            if not part:break
            data.extend(part)
            if len(data)>self.limits.max_file_bytes:raise ValueError('文件超过服务端大小上限')
        digest=hashlib.sha256(data).hexdigest()
        if Path(name).suffix.lower()=='.zip':
            from ..integrations.markdown_package import MarkdownPackageReader
            package=MarkdownPackageReader(max_documents=self.limits.max_batch_items).read(bytes(data),name)
            prepared=[]
            # Validate every member before publishing any source metadata or bytes.
            for entry in package['documents']:
                encoded=entry['text'].encode('utf-8')
                if len(encoded)>self.limits.max_file_bytes or len(entry['text'])>MAX_TEXT_CHARS:
                    raise ValueError('知识包内文档超过服务端单文件或正文长度上限')
                item_options={**(options or {}),'title':entry.get('title') or entry['path'],
                    'metadata':{**(options or {}).get('metadata',{}),**entry.get('metadata',{}),
                        'original_zip_name':name,'package_sha256':digest,'package_entry_path':entry['path']}}
                UploadOptions.model_validate(item_options)
                prepared.append((entry,encoded,item_options))
            # Remote imports and image decoding must not hold a database UOW;
            # the publication guard below protects only object writes/references.
            prepared_sources={entry['path']:self.sources.prepare(bytes(data),entry['path'],
                text=entry['text'],assets=entry['assets']) for entry,_,_ in prepared}
            result=[]
            with self.sources.store.publication_guard():
                obj=self.sources.store.register_object(self.sources.object_store.put(bytes(data),'application/zip',digest))
                for index,(entry,encoded,item_options) in enumerate(prepared):
                    row=self.store.add_item(project_id,batch_id,entry['path'],None,
                        digest,(key or digest)+':'+entry['path'],
                        self.limits.max_batch_items,self.limits.max_batch_bytes,
                        options=item_options,
                        accounted_size=len(data) if index==0 else 0,upload_object_id=obj['id'])
                    if not row.get('source_version_id'):
                        source=self.sources.publish(project_id,row['document_id'],prepared_sources[entry['path']])
                        self.store.set_source(project_id,batch_id,row['id'],source['id'])
                        row['source_version_id']=source['id']
                    result.append(public_item(row))
            return {'items':result,'total':len(result),'id':result[0]['id'] if result else None}
        import mimetypes
        with self.sources.store.publication_guard():
            obj=self.sources.store.register_object(self.sources.object_store.put(bytes(data),
                mimetypes.guess_type(name)[0] or 'application/octet-stream',digest))
            row=self.store.add_item(project_id,batch_id,name,None,digest,key or digest,
                self.limits.max_batch_items,self.limits.max_batch_bytes,options=options,upload_object_id=obj['id'])
        return public_item(row)
    def control(self,project_id,batch_id,action):
        batch=self.get(project_id,batch_id)
        if action=='retry-failed':return self.store.retry(project_id,batch_id)
        if action in ('start','resume'):
            requested=batch['settings'].get('requested_concurrency','auto')
            actual=self.limits.server_concurrency_limit if requested=='auto' else min(requested,self.limits.server_concurrency_limit)
            self.store.set_concurrency(batch_id,actual)
        return self.store.transition(project_id,batch_id,action)
    def retry(self,project_id,batch_id,item_id,mode):
        self.get(project_id,batch_id)
        return self.store.retry(project_id,batch_id,item_id,mode)
