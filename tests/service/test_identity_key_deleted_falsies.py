import pytest
from knowledge_service.repository import Repository


@pytest.mark.parametrize('flag',[None,False,0,'',[],{}])
def test_legacy_false_deletion_flags_keep_identity_key_registration(tmp_path,flag):
    repo=Repository(tmp_path/'identity-falsies')
    project=repo.create_project('identity falsies')['id']
    record=repo.put_record(project,dict(id='e',kind='entity',type='Platform',text='平台',
        metadata={'_deleted':flag,'identity_policy':'reference','identity_key_fields':['code'],
                  'identity_keys':{'code':'platform-code'},'identity_namespace':'scope'}))
    rows=repo._db.execute('SELECT record_id,record_version_id,key_value FROM entity_identity_keys WHERE project_id=?',
        (project,)).fetchall()
    assert len(rows)==1 and rows[0]['record_id']=='e' and rows[0]['record_version_id']==record['version_id']
    assert rows[0]['key_value']=='"platform-code"'
