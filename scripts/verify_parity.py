"""Non-destructively import all saved disk projects into the running new service."""
import json
import time
from pathlib import Path
import httpx


def main():
    root=Path(__file__).resolve().parents[1]
    before={str(p):p.read_bytes() for folder in (root/'data/projects').iterdir() if folder.is_dir()
            for p in folder.glob('*.json')}
    results=[]
    with httpx.Client(base_url='http://127.0.0.1:8100',timeout=120) as client:
        health=client.get('/api/health').json()
        assert health['semantica_version']=='0.6.8',health
        assert 'llm_model' in health['python_executable'],health
        print('RUNTIME_OK',flush=True)
        projects=[p for p in client.get('/api/local-projects').json()['projects'] if p['kind']=='project']
        for legacy in projects:
            submitted=client.post('/api/local-projects/import',json={'name':legacy['name'],'kind':'project'})
            submitted.raise_for_status();job_id=submitted.json()['id'];last=None
            for _ in range(900):
                job=client.get('/api/jobs/'+job_id).json()
                if job.get('stage')!=last:
                    last=job.get('stage');print(legacy['name'],job['status'],last,flush=True)
                if job['status'] in ('completed','failed','interrupted'):break
                time.sleep(2)
            assert job['status']=='completed',job
            p=job['result']['project']['id'];base='/api/projects/'+p
            graph=client.post(base+'/graph',json={}).json()
            original=json.loads(before[str(root/'data/projects'/legacy['name']/'graph.json')])
            assert len(graph['edges'])==len(original['edges'])
            real=[n for n in graph['nodes'] if not n.get('metadata',{}).get('legacy',{}).get('raw',{}).get('unresolved_endpoint')]
            assert len(real)==len(original['nodes'])
            dashboard=client.post(base+'/dashboard',json={}).json()
            assert dashboard['counts']['chunk']==legacy['segments']
            sources=client.post(base+'/sources',json={}).json()['documents']
            manifest=json.loads(before[str(root/'data/projects'/legacy['name']/'manifest.json')])
            assert all(any(d['metadata'].get('title')==name and d['text']==body for d in sources)
                       for name,body in manifest.get('sources',{}).items() if isinstance(body,str))
            result={'name':legacy['name'],'project_id':p,'nodes':len(real),'unresolved_nodes':len(graph['nodes'])-len(real),
                    'edges':len(graph['edges']),'chunks':legacy['segments'],'sources':len(sources),'warnings':job['result']['warnings']}
            results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
            search=client.post(base+'/search',json={'query':'商户规则','k':3})
            search.raise_for_status();assert search.json()['semantic']
        assert all(Path(path).read_bytes()==value for path,value in before.items())
        print('PARITY_IMPORT_OK',json.dumps(results,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
