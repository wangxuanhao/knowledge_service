"""Exercise real, authenticated validation APIs and capture current UI screens."""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TURTLE = '''@prefix ex: <https://redesign.example/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Organization a owl:Class ; rdfs:label "组织"@zh .
ex:Merchant a owl:Class ; rdfs:label "商户"@zh ; rdfs:subClassOf ex:Organization .
ex:Platform a owl:Class ; rdfs:label "平台"@zh ; rdfs:subClassOf ex:Organization .
ex:onboards a owl:ObjectProperty ; rdfs:label "入驻"@zh ; rdfs:domain ex:Merchant ; rdfs:range ex:Platform .
ex:status a owl:DatatypeProperty ; rdfs:label "入驻状态"@zh ; rdfs:domain ex:Merchant ; rdfs:range xsd:string .
'''


def main():
    parser=ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8101')
    parser.add_argument('--credentials',required=True)
    parser.add_argument('--upload-fixture', action='store_true',
                        help='Upload the tiny TXT fixture even when earlier validation sources exist')
    args=parser.parse_args()
    import httpx
    from playwright.sync_api import sync_playwright
    client=httpx.Client(base_url=args.url,trust_env=False,timeout=240)
    login=client.post('/api/auth/login',json=json.loads(Path(args.credentials).read_text(encoding='utf-8')))
    login.raise_for_status()
    token=login.json()['access_token']
    client.headers['Authorization']='Bearer '+token
    def call(method,path,body=None):
        response=client.request(method,path,json=body)
        if response.is_error:
            raise RuntimeError(f'{method} {path}: {response.status_code} {response.text[:1200]}')
        return response.json()
    name='商户入驻 · 改造验收'
    projects=call('GET','/api/projects')['projects']
    project=next((p['id'] for p in projects if p['name']==name),None)
    if not project:
        project=call('POST','/api/projects',{'name':name,'ontology_mode':'discovery','use_default_ontology':False})['id']
    prefix=f'/api/projects/{project}'
    history=call('GET',prefix+'/ontologies')['versions']
    from ontology_validation_helpers import publish_validation_ontology
    ontology=history[0] if history else publish_validation_ontology(lambda method,path,body: call(method,path,body),prefix,TURTLE)
    if call('POST',prefix+'/records/page',{'limit':1})['total']==0:
        records=[{'id':'merchant-a','kind':'entity','text':'青禾商户','type':'https://redesign.example/Merchant'},
                 {'id':'platform-a','kind':'entity','text':'示例平台','type':'https://redesign.example/Platform'},
                 {'id':'merchant-b','kind':'entity','text':'青禾商户分店','type':'https://redesign.example/Merchant'},
                 {'id':'relation-a','kind':'relation','text':'青禾商户入驻示例平台','type':'https://redesign.example/onboards','subject_id':'merchant-a','object_id':'platform-a'},
                 {'id':'attribute-a','kind':'attribute','text':'入驻状态：已开通','type':'https://redesign.example/status','subject_id':'merchant-a','value':'已开通','datatype':'http://www.w3.org/2001/XMLSchema#string'}]
        for record in records:
            record.update(ontology_id=ontology['id'],metadata={'region':'华东'})
        call('POST',prefix+'/records',{'records':records})
    print('Real validation corpus ready',flush=True)
    report={'base_url':args.url,'project_id':project,'page_errors':[],'screens':[],'api':{}}
    report['api']['records_total']=call('POST',prefix+'/records/page',{'limit':1})['total']
    manifest=call('POST',prefix+'/graph-sessions',{})
    report['api']['graph_counts']={key:manifest[key] for key in ('entity_count','relation_count')}
    report['api']['graph_page_count']=len(call('POST',prefix+f"/graph-sessions/{manifest['id']}/page",{'kind':'entities','limit':10})['records'])
    target=ROOT/'docs'/'manual-redesign'
    target.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        page.add_init_script('localStorage.setItem("kg_token",'+json.dumps(token)+');')
        page.goto(args.url,wait_until='domcontentloaded')
        page.wait_for_function('window.Auth?.isSuperAdmin() && document.querySelector("#project").options.length > 1',timeout=60000)
        page.select_option('#project',project)
        page.wait_for_function('(p)=>document.querySelector("#project").value===p',arg=project)
        if args.upload_fixture or not call('POST',prefix+'/sources',{}).get('documents'):
            page.locator('[data-tab="sources"]').click()
            page.locator('#upload-source').click()
            page.wait_for_selector('#tab-ingest .document-upload-panel')
            page.set_input_files('#doc-file',{'name':'商户入驻验收.txt','mimeType':'text/plain',
                'buffer':'青禾商户于2020年入驻示例平台，入驻状态为已开通。此文件用于说明资料解析、原文追溯与知识维护。'.encode('utf-8')})
            page.locator('#extraction-mode').select_option('documents')
            with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/ingest-batches'),timeout=30000) as created:
                page.locator('#ingest').click()
            assert created.value.ok,created.value.status
            batch=created.value.json()
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                state=call('GET',prefix+'/ingest-batches/'+batch['id'])
                if state.get('status') in {'completed','completed_with_errors','failed','cancelled','paused'}:
                    break
                time.sleep(1)
            assert state.get('status')=='completed',state.get('status')
            report['api']['browser_file_batch_status']=state['status']
        for tab,label in [('projects','projects'),('useradmin','people'),('ontology-model','ontology'),('records','records'),('sources','sources'),('search','search'),('quality','evaluation'),('runtime','runtime')]:
            page.locator(f'[data-tab="{tab}"]').click()
            page.wait_for_timeout(1500)
            if tab=='sources' and page.locator('.source-item').count():
                page.locator('.source-item').first.click()
                page.wait_for_timeout(500)
            page.screenshot(path=str(target/f'2026-10-19-{label}.png'),full_page=True)
            report['screens'].append({'page':tab,'file':f'manual-redesign/2026-10-19-{label}.png'})
            print('Captured',tab,flush=True)
        page.locator('[data-tab="records"]').click()
        page.wait_for_timeout(600)
        page.get_by_role('button',name='新增实体',exact=True).click()
        page.wait_for_selector('#create-record-type option',state='attached',timeout=15000)
        page.locator('#create-record-text').fill('浏览器创建验收商户')
        page.locator('#create-record-type').select_option('https://redesign.example/Merchant')
        page.screenshot(path=str(target/'2026-10-19-create-entity.png'),full_page=True)
        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/records'),timeout=240000) as result:
            page.locator('#save-created-record').click()
        assert result.value.status==201,result.value.status
        page.wait_for_timeout(1500)
        report['api']['browser_entity_create']=result.value.status
        assert not report['page_errors'],report['page_errors']
        browser.close()
    (ROOT/'docs/research/2026-10-19-live-ui-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Live API and browser validation passed',flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        from knowledge_service.utils.diagnostics import redact
        print(redact(exc),file=sys.stderr)
        raise SystemExit(1)
