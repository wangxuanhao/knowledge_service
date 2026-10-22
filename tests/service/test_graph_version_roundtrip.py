from ontology_seed import seed_ontology
import pytest

from knowledge_service.core.security import hash_password
from knowledge_service.repository.users_store import UserStore
from test_frontend_retrieval_flow import browser, workbench  # live isolated app + Edge


TTL = '''
@prefix ex: <https://test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Person a owl:Class .
ex:knows a owl:ObjectProperty .
'''


@pytest.mark.parametrize('record_id', ['edit-a', 'edit-rel'])
def test_graph_edit_revision_and_ontology_match_ledger_and_history(workbench, record_id):
    page = workbench.page
    # The shared backend fixture disables authentication, but the editor still
    # checks Auth.isAdmin(). Establish a real session instead of only changing
    # the CSS visibility of write controls.
    username, password = 'graph-roundtrip-editor', 'graph-roundtrip-password'
    editor = UserStore(workbench.service.repository).create(
        username, hash_password(password), role='admin')
    page.evaluate('''async credentials => {
      const session=await api('/api/auth/login',credentials);
      window.Auth.adoptSession(session);
    }''', {'username': username, 'password': password})
    assert page.evaluate('window.Auth.isAdmin()')
    assert page.evaluate('window.Auth.user().id') == editor['id']
    project = page.evaluate('''async () => {
      const created=await api('/api/projects',{name:'双版本编辑验证',use_default_ontology:false});
      await projects();return created.id;
    }''')
    page.select_option('#project', project)
    page.wait_for_function('id => current===id', arg=project)
    first = page.evaluate(r'''async turtle => {
      const first=await api(endpoint('/ontologies'),{turtle});
      await api(endpoint('/records'),{records:[
        {id:'edit-a',kind:'entity',text:'甲',type:'https://test/Person'},
        {id:'edit-b',kind:'entity',text:'乙',type:'https://test/Person'},
        {id:'edit-rel',kind:'relation',text:'甲认识乙',type:'https://test/knows',
          subject_id:'edit-a',object_id:'edit-b'}]});
      return first;
    }''', TTL)
    seed_ontology(workbench.service.repository, project, TTL + '\n<https://test/New> a <http://www.w3.org/2002/07/owl#Class> .', {})
    page.evaluate('async()=>{await loadOntologyTerms();await window.drawGraph();}')
    page.evaluate('''async id => {
      const graph=await api(endpoint('/subgraph'),{});
      const row=[...graph.nodes,...graph.edges].find(item=>item.id===id);
      await window.renderEvidenceInspector(row,graph.nodes);
    }''', record_id)
    page.wait_for_function("() => !document.getElementById('inspector-version').textContent.includes('读取中')", timeout=5000)
    assert '依据本体 v1（历史）' in page.inner_text('#inspector-version')
    page.click('[data-action="edit"]')
    page.wait_for_selector('#structured-record-text')
    page.fill('#structured-record-text', '图谱编辑后的内容')
    page.click('#save-structured-record')
    page.wait_for_function("() => document.getElementById('record-dialog-message').textContent.includes('已保存修订 r2')")
    assert '依据本体 v1（历史）' in page.inner_text('#record-dialog-message')
    page.click('#close-record-dialog')
    page.wait_for_function("() => document.getElementById('inspector-version').textContent.includes('修订 r2')")
    page.click('[data-action="ledger"]')
    row = page.locator(f'tr[data-record-id="{record_id}"]')
    page.wait_for_function("""id => {
      const text=document.querySelector('tr[data-record-id="'+id+'"]')?.textContent||'';
      return text.includes('修订 r2')&&text.includes('依据本体 v1（历史）');
    }""", arg=record_id)
    assert '依据本体 v1（历史）' in row.inner_text()
    state = page.evaluate('''async id => {
      const history=await api(endpoint('/records/'+id+'/history'),undefined,'GET');
      const graph=await api(endpoint('/subgraph'),{});
      const row=[...graph.nodes,...graph.edges].find(item=>item.id===id);
      await window.renderEvidenceInspector(row,graph.nodes);
      return {history:history.versions,row};
    }''', record_id)
    assert [item['version'] for item in state['history']] == [1, 2]
    assert all(item['ontology_id'] == first['id'] for item in state['history'])
    assert (state['row']['version'], state['row']['ontology_id']) == (2, first['id'])
    assert not workbench.errors
