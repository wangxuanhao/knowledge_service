"""Compare every authority window/context with receipts before native auditing."""
from itertools import islice


def audit_milvus_authority(repository,project,state,catalog,adapter,lease_is_current,*,after=None,previous_count=0,max_records=None,max_vectors=4096):
    from ..repository.projection_inputs import ProjectionInputs
    from ..domain.search_projection import build_projections,projection_fingerprint
    generation=state['index_version'];count=previous_count;visited=0;last=after
    with repository.independent_read_view() as reader:
        for batch in ProjectionInputs(reader).batches(project,batch_size=32,after=after):
            if not lease_is_current():return None
            for value in batch:
                rows=iter(build_projections(value,catalog,index_version=generation))
                while True:
                    page=list(islice(rows,256))
                    if not page:break
                    expected={row['vector_id']:{'version_id':row['version_id'],'fingerprint':projection_fingerprint(row)} for row in page}
                    actual=reader.projections.receipts.for_vector_ids(project,generation,list(expected))
                    if expected!=actual:
                        if not lease_is_current():return None
                        raise RuntimeError('来源上下文与属性窗口 authority 对账不一致，需要重新向量化')
                    if max_records is not None:
                        result=adapter.validate_subset(project,state,catalog,expected=expected)
                        if not lease_is_current():return None
                        if not isinstance(result,dict) or result.get('ok') is not True:
                            raise RuntimeError('来源上下文原生索引分页对账失败，需要重新向量化')
                    count+=len(page)
                visited+=1;last=value.record['id']
                if max_records is not None and (visited>=max_records or count-previous_count>=max_vectors):
                    return {'ok':True,'complete':False,'after':last,'authority_vectors':count}
        receipts=reader.projections.receipts.manifest(project,generation)
        if len(receipts)!=count:
            if not lease_is_current():return None
            raise RuntimeError('来源上下文投影对账数量不一致，需要重新向量化')
        if not lease_is_current():return None
        # Bounded background passes have already compared every expected native
        # payload. Only check for extra/missing rows at the terminal boundary.
        result=(adapter.validate_count(project,state,catalog,expected_count=count)
                if max_records is not None else adapter.validate(project,state,catalog,expected=receipts))
        if not lease_is_current():return None
        if not isinstance(result,dict) or result.get('ok') is not True:
            raise RuntimeError('来源上下文原生索引对账失败，需要重新向量化')
        return {**result,'complete':True,'after':None,'authority_vectors':count}
