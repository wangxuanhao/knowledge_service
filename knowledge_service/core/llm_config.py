"""Immutable per-operation LLM settings; credentials never enter public metadata."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


def validate_base_url(value: str) -> str:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('模型 base URL 必须是不含空白的 HTTP(S) 地址')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment
                 and '?' not in value and '#' not in value and '\\' not in value)
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('模型 base URL 必须是 HTTP(S) 地址，且不能包含凭据、查询或片段')
    return value.rstrip('/')


@dataclass(frozen=True)
class LLMConfig:
    profile_id: str = 'environment'
    name: str = '环境配置'
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    max_tokens: int = 65536
    proxy: str | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def public_identity(self) -> dict:
        try:
            public_url = validate_base_url(self.base_url) if self.base_url else ''
        except ValueError:
            public_url = ''
        return {'profile_id': self.profile_id, 'name': self.name,
                'base_url': public_url, 'model': self.model}


_bound: ContextVar[LLMConfig | None] = ContextVar('llm_config', default=None)


def runtime_options() -> dict:
    raw = os.getenv('KG_LLM_MAX_TOKENS', '').strip()
    try:
        maximum = int(raw)
        if maximum <= 0:
            maximum = 65536
    except ValueError:
        maximum = 65536
    return {'max_tokens': maximum, 'proxy': os.getenv('KG_LLM_PROXY', '').strip() or None}


def environment_llm() -> LLMConfig:
    base_url = os.getenv('KG_LLM_BASE_URL', '').strip()
    # Existing environment integrations validate their endpoint at HTTP dispatch.
    # Public metadata validates separately so URL credentials cannot be exposed.
    base_url = base_url.rstrip('/')
    return LLMConfig(base_url=base_url, model=os.getenv('KG_LLM_MODEL', '').strip(),
                     api_key=os.getenv('KG_LLM_API_KEY', '').strip(), **runtime_options())


def bound_llm_config() -> LLMConfig | None:
    return _bound.get()


def current_llm() -> LLMConfig:
    return _bound.get() or environment_llm()


@contextmanager
def bound_llm(config: LLMConfig):
    token = _bound.set(config)
    try:
        yield config
    finally:
        _bound.reset(token)
