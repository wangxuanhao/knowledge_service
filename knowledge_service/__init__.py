"""版本化知识、本体与检索服务。

FastAPI 标准分层：api（路由）→ services（业务）→ repository（存储）
→ core/utils（基础设施）→ integrations（外部集成）。

统一出口：外部使用 ``from knowledge_service import create_app, Repository`` 等，
不必关心内部模块路径；公开对象首次访问时才加载对应层。
"""
from importlib import import_module

__version__ = "1.1.0"

__all__ = ['create_app', 'Repository', 'OntologyNotPublished', 'KnowledgeService', '__version__']

_EXPORTS = {
    'create_app': ('.api', 'create_app'),
    'Repository': ('.repository', 'Repository'),
    'OntologyNotPublished': ('.repository', 'OntologyNotPublished'),
    'KnowledgeService': ('.services.service', 'KnowledgeService'),
}


def __getattr__(name):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}') from None
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
