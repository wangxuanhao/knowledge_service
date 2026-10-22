"""Run the real app against isolated PostgreSQL/Milvus validation resources."""
from argparse import ArgumentParser
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = ArgumentParser()
    parser.add_argument('--port', type=int, default=8101)
    parser.add_argument('--original', default='D:/workspace/knowledge_service_v2/knowledge_service')
    parser.add_argument('--credentials', required=True, help='Private local file; never include in reports')
    parser.add_argument('--database', default='knowledge_redesign_ui')
    parser.add_argument('--collection', default='knowledge_redesign_ui')
    args = parser.parse_args()
    import psycopg
    from psycopg import sql
    from knowledge_service.core.config import load_environment
    from knowledge_service.repository.migrate import apply_migrations
    load_environment(Path(args.original), include_admin=True)
    host = os.environ.get('KG_DATABASE_HOST', '127.0.0.1')
    port = os.environ.get('KG_DATABASE_PORT', '5432')
    owner = dict(host=host, port=port, user=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD'])
    database = args.database
    if not database.startswith('knowledge_redesign_') or not args.collection.startswith('knowledge_redesign_'):
        raise ValueError('Validation resources must use the knowledge_redesign_ namespace')
    with psycopg.connect(dbname='postgres', **owner, autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database,)).fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    apply_migrations(psycopg.conninfo.make_conninfo(dbname=database, **owner), verbose=False)
    credential_path = Path(args.credentials)
    if credential_path.exists():
        credentials = json.loads(credential_path.read_text(encoding='utf-8'))
    else:
        credentials = {'username': 'validation-admin', 'password': secrets.token_urlsafe(24)}
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        credential_path.write_text(json.dumps(credentials), encoding='utf-8')
    os.environ['KG_SEED_ADMIN_USERNAME'] = credentials['username']
    os.environ['KG_SEED_ADMIN_PASSWORD'] = credentials['password']
    os.environ['KG_DATABASE_URL'] = psycopg.conninfo.make_conninfo(host=host, port=port, dbname=database,
        user=os.environ.get('POSTGRES_APP_USER','knowledge_app'),password=os.environ['POSTGRES_APP_PASSWORD'])
    os.environ['KG_MILVUS_COLLECTION'] = args.collection
    os.environ['KG_VECTOR_BACKEND'] = 'milvus'
    os.environ['KG_AUTH_DISABLED'] = '0'
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    if os.environ.get('KG_EMBEDDING_BACKEND', 'local') == 'local':
        model = Path(os.environ.get('KG_EMBEDDING_PATH', 'data/model/embedding/bge-m3'))
        if not model.is_absolute():
            model = Path(args.original) / model
        os.environ['KG_EMBEDDING_PATH'] = str(model)
    for key in ('POSTGRES_USER','POSTGRES_PASSWORD'):
        os.environ.pop(key,None)
    from knowledge_service.api import create_app
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        from knowledge_service.utils.diagnostics import redact
        print(redact(exc), file=sys.stderr)
        raise SystemExit(1)
