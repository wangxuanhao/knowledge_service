"""Review and apply verified source-scope inheritance through normal writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge_service.core.config import load_environment
from knowledge_service.repository import Repository
from knowledge_service.repository.connection import resolve_dsn
from knowledge_service.services.source_scope import build_scope_repair, prepare_scope_repair, apply_scope_repair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_environment()
    repository = Repository(resolve_dsn())
    try:
        if args.apply:
            report = json.loads(args.plan.read_text(encoding='utf-8'))
            if report['project_id'] != args.project:
                raise ValueError('repair plan belongs to another project')
            from knowledge_service.api import create_app
            app = create_app(dsn=resolve_dsn())
            rows = apply_scope_repair(app.state.service, report)
            print(json.dumps({'applied': len(rows), 'projection_sync': 'durable_outbox_and_drain'}, ensure_ascii=True))
        else:
            report = build_scope_repair(repository, args.project)
            prepared = prepare_scope_repair(repository, report)
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'repair_count':len(report['repairs']), 'conflict_count':len(report['conflicts']),
                'preserved_count':len(report['preserved']), 'normal_write_preflight':prepared is not None,
                'source_versions':sorted({r['document_version_id'] for p in report['repairs'] for r in p['sources']}),
                'missing_business_dates':sum(r['valid_from'] is None for r in report['repairs'])}, ensure_ascii=True))
    finally:
        repository.close()

if __name__ == '__main__':
    main()
