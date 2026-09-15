"""Scoped graph exploration, project snapshots and deterministic evaluation."""
from collections import Counter,defaultdict,deque
from uuid import uuid4
from .service import public
from .governance import writable
from .time import utc_now


class Explorer:
    def __init__(self,service):
        self.service=service
        self.repo=service.repository

    def graph(self,p,scope,node_id=None,hops=1):
        rows=self.service.scoped(p,scope)
        nodes={r['id']:r for r in rows if r['kind']=='entity'}
        edges=[r for r in rows if r['kind']=='relation' and r['subject_id'] in nodes and r['object_id'] in nodes]
        if node_id:
            if node_id not in nodes: raise KeyError(node_id)
            selected={node_id};frontier={node_id}
            for _ in range(max(0,min(5,hops))):
                following={r[k] for r in edges if r['subject_id'] in frontier or r['object_id'] in frontier for k in ('subject_id','object_id')}
                frontier=following-selected;selected|=frontier
            nodes={i:r for i,r in nodes.items() if i in selected}
            edges=[r for r in edges if r['subject_id'] in selected and r['object_id'] in selected]
        return {'nodes':[public(r) for r in nodes.values()],'edges':[public(r) for r in edges]}

    def dashboard(self,p,scope):
        rows=self.service.scoped(p,scope)

        # 计算每个文档的派生知识数量
        derived_counts=Counter(r.get('source_id') for r in rows if r['kind'] in ('entity','relation','chunk'))

        # 只统计成功解析的文档（有派生知识的文档）
        documents=[r for r in rows if r['kind']=='document']
        successful_docs=[d for d in documents if derived_counts.get(d['id'],0)>0]

        # 统计成功解析的实体和关系（排除 chunk）
        entity_rows=[r for r in rows if r['kind']=='entity']
        relation_rows=[r for r in rows if r['kind']=='relation']

        return {
            'counts':{
                'document':len(successful_docs),
                'total_documents':len(documents),
                'chunk':sum(1 for r in rows if r['kind']=='chunk'),
                'entity':len(entity_rows),
                'relation':len(relation_rows)
            },
            'types':dict(Counter(r.get('type','') for r in entity_rows)),
            'predicates':dict(Counter(r.get('type','') for r in relation_rows)),
            'unknown_validity':sum(r.get('valid_from') is None for r in rows)
        }

    def sources(self,p,scope):
        rows=self.service.scoped(p,scope)
        return {'documents':[{**public(r),'derived_count':sum(x.get('source_id')==r['id'] for x in rows)}
                             for r in rows if r['kind']=='document']}

    def mindmap(self,p,scope,root_id,depth=3):
        graph=self.graph(p,scope,root_id,depth)
        nodes={r['id']:r for r in graph['nodes']};adj=defaultdict(list)
        for edge in graph['edges']:
            adj[edge['subject_id']].append((edge['object_id'],edge['type']))
            adj[edge['object_id']].append((edge['subject_id'],edge['type']))
        visited=set();count=[0]
        def branch(node,level,path):
            count[0]+=1
            item={'id':node,'text':nodes[node]['text'],'type':nodes[node]['type'],'children':[]}
            if level==0 or node in path or count[0]>=500:return item
            for target,predicate in adj[node][:40]:
                if count[0]>=500:break
                if target not in path|{node}:
                    child=branch(target,level-1,path|{node});child['predicate']=predicate;item['children'].append(child)
            return item
        return {'tree':branch(root_id,min(depth,5),set()),'bounded':True}

    def snapshot(self,p,name,kind='manual'):
        with self.service.lock:
            records=self.repo.current_records(p)
            item={'id':str(uuid4()),'project_id':p,'name':name,'kind':kind,'created_at':utc_now(),
                  'record_count':len(records),'records':records,'ontologies':self.repo.list_ontologies(p)}
            self.repo.save_artifact('snapshot',item)
            return {k:v for k,v in item.items() if k not in ('records','ontologies')}

    def restore_snapshot(self,p,snapshot_id):
        with self.service.lock:
            snapshot=self.repo.get_artifact('snapshot',snapshot_id)
            if snapshot['project_id']!=p:raise KeyError(snapshot_id)
            current={r['id']:r for r in self.repo.current_records(p)}
            target={r['id']:writable(r) for r in snapshot['records']}
            for i,row in current.items():
                if i not in target and not row.get('metadata',{}).get('_audit'):
                    target[i]={**writable(row),'metadata':{**row.get('metadata',{}),'_deleted':True}}
            # Save recovery point first; restores create new versions, never erase history.
            backup=self.snapshot(p,'恢复前自动备份 · '+snapshot['name'],kind='auto')
            for row in target.values():row['metadata']={**row.get('metadata',{}),'restored_from_snapshot':snapshot_id}
            saved=self.service.write(p,list(target.values()),expected_versions={i:current[i]['version'] if i in current else 0 for i in target})
            return {'restored':len(saved),'recovery_snapshot_id':backup['id'],'recovery_snapshot_name':backup['name']}

    def evaluate(self,p,scope,entities,relations):
        graph=self.graph(p,scope)
        names={r['id']:r['text'] for r in graph['nodes']}
        actual_entities=set(names.values())
        actual_relations={(names[r['subject_id']],r['type'].rsplit('#',1)[-1].rsplit('/',1)[-1],names[r['object_id']]) for r in graph['edges']}
        def score(actual,gold):
            tp=len(actual&gold);precision=tp/len(actual) if actual else int(not gold);recall=tp/len(gold) if gold else int(not actual)
            return {'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0,
                    'true_positive':tp,'predicted':len(actual),'gold':len(gold),'missing':list(gold-actual),'extra':list(actual-gold)}
        return {'entities':score(actual_entities,set(entities)),'relations':score(actual_relations,{tuple(x) for x in relations})}
