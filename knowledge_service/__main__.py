"""通过 python -m knowledge_service 运行；单个进程负责写入编排。"""
import argparse
import os


def main():
    from .environment import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description='Knowledge Service — 双时态、本体与过滤检索')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8100)
    parser.add_argument('--database', help='SQLite 路径；默认 data/service/knowledge.sqlite')
    parser.add_argument('--demo', action='store_true', help='使用确定性的非语义哈希代替模型')
    args = parser.parse_args()
    if args.demo:
        os.environ['KG_EMBEDDING_BACKEND'] = 'demo'
    # 读取路径的阶段耗时（repository.query / service.scoped / explorer.graph …）
    # 输出到本终端。默认静默，仅当提高 KG_LOG_LEVEL 时输出；使用
    # KG_SLOW_MS=200 可只查看慢阶段。
    from .diagnostics import configure_logging
    configure_logging()
    from .api import create_app
    import uvicorn
    uvicorn.run(create_app(args.database), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
