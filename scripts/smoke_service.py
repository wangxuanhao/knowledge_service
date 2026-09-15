"""Exercise a running service, creating one clearly labelled verification project."""
import argparse
import json
from datetime import datetime, timezone

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8100')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=180) as client:
        def post(path, body):
            response = client.post(path, json=body)
            response.raise_for_status()
            return response.json()
        project = post('/api/projects', {'name':'服务验收 · '+datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                         'metadata':{'purpose':'verification'}})
        path = '/api/projects/'+project['id']
        for city in ('北京','上海'):
            post(path+'/documents', {'title':city+'退款规则（演示）','text':city+'示例规则：未消费订单可申请退款。',
                                    'extract':False, 'valid_from':'2025-01-01', 'valid_until':'2027-01-01',
                                    'metadata':{'region':{'city':city}, 'tags':['退款','演示']}})
        query = {'query':'怎样退钱','valid_at':'2026-09-01',
                 'filters':{'field':'region.city','op':'eq','value':'北京'}}
        result = post(path+'/search', query)
        assert result['candidate_count'] == 1 and len(result['hits']) == 1, result
        assert result['hits'][0]['metadata']['region']['city'] == '北京'
        assert post(path+'/search', {**query, 'valid_at':'2027-01-01'})['hits'] == []
        post(path+'/records', {'records':[
            {'id':'demo-platform','kind':'entity','type':'Platform','text':'演示平台'},
            {'id':'demo-merchant','kind':'entity','type':'Merchant','text':'演示商户'},
            {'id':'demo-relation','kind':'relation','type':'onboards','text':'演示商户入驻演示平台',
             'subject_id':'demo-merchant','object_id':'demo-platform'}]})
        graph = post(path+'/graph/semantica', {})
        assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
        validation = post(path+'/ontology/validate', {})
        assert validation['conforms'], validation
        sparql = post(path+'/sparql', {'query':'ASK { ?s <http://meituan.com/kg#onboards> ?o }'})
        assert sparql['value'] is True
        answer = post(path+'/qa', {**query, 'generate':False})
        assert len(answer['evidence']) == 1 and '[E1]' in answer['answer']
        print(json.dumps({'status':'passed','project_id':project['id'], 'semantic':result['semantic'],
                          'candidate_count':result['candidate_count'], 'semantica_nodes':len(graph['nodes']),
                          'semantica_edges':len(graph['edges']), 'ontology_conforms':True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
