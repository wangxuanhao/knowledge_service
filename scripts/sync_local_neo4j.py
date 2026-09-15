"""Load legacy project graphs without vectors, then sync their Neo4j projections."""
import argparse
from pathlib import Path
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from knowledge_service.environment import load_environment
from knowledge_service.repository import Repository
from knowledge_service.service import KnowledgeService
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.legacy_import import LegacyImporter
from knowledge_service.neo4j_store import Neo4jProjection, digest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--include-legacy',action='store_true',help='Read-only import of data/projects before sync')
    parser.add_argument('--sync-all-legacy',action='store_true',help='Sync all imported legacy projects; otherwise check connection only')
    args=parser.parse_args()
    load_environment(ROOT)
    import os
    repo=Repository(os.environ.get('KG_DATABASE',str(ROOT/'data/service/knowledge.sqlite')))
    projection=Neo4jProjection(repo)
    try:
        print('CONNECTION',projection.check(),flush=True)
        if args.include_legacy:
            importer=LegacyImporter(KnowledgeService(repo,HashingEncoder()),ROOT)
            for local in importer.list_projects():
                if local['kind']!='project':continue
                start=perf_counter()
                result=importer.import_project(local['name'],build_vectors=False)
                print('LOADED',result['project']['name'],result['counts'],'seconds',round(perf_counter()-start,3),flush=True)
        if args.sync_all_legacy:
            for project in repo.list_projects():
                if not project['metadata'].get('legacy_import_key'):continue
                start=perf_counter()
                result=projection.sync(project['id'])
                snapshot=repo.export_projection(project['id'])
                key=digest([snapshot['namespace'],project['id']])
                with projection.driver.session(database=projection.database) as session:
                    count=session.run('MATCH (p:KSProject {key:$key})-[:KS_CONTAINS]->(:KSRecord)-[:KS_VERSION]->(v:KSVersion) RETURN count(v) AS count',key=key).single()['count']
                assert count==len(snapshot['records']), 'Remote version count differs'
                assert not projection.project_status(project['id'])['local_changes_pending']
                print('SYNCED',project['name'],'versions',count,'seconds',round(perf_counter()-start,3),flush=True)
    finally:
        projection.close();repo.close()


if __name__=='__main__':main()
