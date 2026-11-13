from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projections, projection_fingerprint
from knowledge_service.repository.core import Repository


def test_multiple_attribute_windows_keep_individual_delivery_receipts(tmp_path):
    repo=Repository(tmp_path/'attribute-windows')
    project=repo.create_project('attribute windows')['id']
    value={'project_id':project,'id':'a','version_id':'av1','kind':'attribute',
           'subject_id':'e','type':'description','datatype':'http://www.w3.org/2001/XMLSchema#string',
           'value':'正文属性。'*2400+'尾部条件','text':'简短标签','metadata':{}}
    rows=list(build_projections(ProjectionInput(value),default_catalog(),index_version=1))
    assert len(rows)>1
    with repo._transaction():repo.projections.receipts._upsert(project,1,rows)
    receipts=repo.projections.receipts
    assert callable(getattr(receipts,'for_vector_ids',None)), 'receipts must distinguish a fact\'s windows'
    actual=receipts.for_vector_ids(project,1,[row['vector_id'] for row in rows])
    assert len(actual)==len(rows)
    assert all(actual[row['vector_id']]=={'version_id':'av1','fingerprint':projection_fingerprint(row)} for row in rows)
    assert len(receipts.manifest(project,1))==len(rows)


def test_projection_collects_owner_and_published_property_labels(tmp_path):
    from knowledge_service.repository.projection_inputs import ProjectionInputs
    repo=Repository(tmp_path/'attribute-labels')
    project=repo.create_project('labels')['id']
    entity=repo.put_record(project,{'id':'e','kind':'entity','type':'Platform','text':'美团平台'})
    attribute=repo.put_record(project,{'id':'a','kind':'attribute','type':'description','subject_id':entity['id'],
        'text':'显示文本','value':'退款依据正文','datatype':'http://www.w3.org/2001/XMLSchema#string'})
    values=ProjectionInputs(repo).collect(project,[attribute['id']])
    assert len(values)==1,'attribute facts must join projection builds'
    assert values[0].owner_label=='美团平台'
    assert '退款依据正文' in list(build_projections(values[0],default_catalog(),index_version=1))[0]['text']
