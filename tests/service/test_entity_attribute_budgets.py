"""Selected-entity reads stay bounded as facts and source text grow."""
from contextlib import contextmanager
import json
from pathlib import Path
from time import perf_counter

from knowledge_service.repository.core import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.entity_attributes import EntityAttributes
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.integrations.embeddings import HashingEncoder


def test_property_and_source_pages_bound_queries_and_transferred_text(tmp_path,monkeypatch):
    repo=Repository(tmp_path/'property-budgets');p=repo.create_project('property budgets')['id']
    repo.put_record(p,dict(id='e',kind='entity',type='Platform',text='平台'))
    paragraph='段落正文。\n'*3000
    facts=repo.put_batch(p,[dict(id=f'a{i:04}',kind='attribute',subject_id='e',type='description',text='属性',
        value=paragraph+str(i),datatype='http://www.w3.org/2001/XMLSchema#string') for i in range(2000)])
    documents=repo.put_batch(p,[dict(id=f'd{i}',kind='document',text=paragraph+'0',metadata={'title':'来源'+str(i)}) for i in range(10)])
    assertions=[dict(id=f's{i:03}',kind='attribute',canonical_record_id='a0000',document_id=documents[i%10]['id'],
        document_version_id=documents[i%10]['version_id'],quote=paragraph+'0',start_char=0,end_char=len(paragraph)+1,
        payload={key:facts[0][key] for key in ('kind','subject_id','type','value','datatype')}) for i in range(100)]
    FormalFactWriter(repo).apply('manual_write',p,assertions,{},dict(records=[]))
    calls=[];query_times=[];original=repo._db.independent_read_snapshot
    class Counted:
        def __init__(self,inner):self.inner=inner
        def __getattr__(self,name):return getattr(self.inner,name)
        def execute(self,sql,*args,**kwargs):
            calls.append(sql);started=perf_counter();result=self.inner.execute(sql,*args,**kwargs)
            query_times.append({'statement':sql.strip().splitlines()[0][:60],'ms':round((perf_counter()-started)*1000,2)})
            return result
    @contextmanager
    def snapshot():
        with original() as reader:yield Counted(reader)
    monkeypatch.setattr(repo._db,'independent_read_snapshot',snapshot)
    reader=EntityAttributes(KnowledgeService(repo,HashingEncoder()))
    started=perf_counter();page=reader.page(p,'e',{},limit=20);page_ms=(perf_counter()-started)*1000
    page_queries=len(calls);page_bytes=len(json.dumps(page,ensure_ascii=False,default=str).encode())
    assert page['value_count']==2000 and page['next_cursor']
    assert page_queries<=12 and page_bytes<48*1024
    first=next(v for group in page['groups'] for v in group['values'] if v['record_id']=='a0000')
    assert first['document_count']==10 and first['accepted_support_count']==100
    assert all(len(v['value_preview'])<=400 for g in page['groups'] for v in g['values'])
    page_timings=list(query_times);calls.clear();query_times.clear();started=perf_counter();sources=reader.sources(p,'e','a0000',{},limit=10)
    source_ms=(perf_counter()-started)*1000;source_bytes=len(json.dumps(sources,ensure_ascii=False,default=str).encode())
    assert len(sources['sources'])==10 and sources['next_cursor']
    assert len(calls)<=6 and source_bytes<80*1024
    assert all(len(row['quote'])<=2000 and row['quote_truncated'] for row in sources['sources'])
    artifact=Path('.benchmarks/entity-properties-performance.json');artifact.parent.mkdir(exist_ok=True)
    artifact.write_text(json.dumps(dict(facts=2000,paragraph_chars=len(paragraph),supports=100,documents=10,
        page_queries=page_queries,page_bytes=page_bytes,page_ms=round(page_ms,2),
        source_queries=len(calls),source_bytes=source_bytes,source_ms=round(source_ms,2),
        page_query_times=page_timings,source_query_times=query_times),indent=2),encoding='utf-8')
