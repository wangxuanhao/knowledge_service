"""Run with python -m knowledge_service; one process owns write orchestration."""
import argparse
import os


def main():
    from .environment import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description='Knowledge Service — 双时态、本体与过滤检索')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8100)
    parser.add_argument('--database', help='SQLite path; default data/service/knowledge.sqlite')
    parser.add_argument('--demo', action='store_true', help='Use deterministic non-semantic hashing instead of a model')
    args = parser.parse_args()
    if args.demo:
        os.environ['KG_EMBEDDING_BACKEND'] = 'demo'
    from .api import create_app
    import uvicorn
    uvicorn.run(create_app(args.database), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
