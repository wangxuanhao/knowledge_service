"""Platform LLM management, authenticated secrets and safe connection probes."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import tempfile

from cryptography.fernet import Fernet, InvalidToken
import httpx

from ..core.llm_config import LLMConfig, bound_llm_config, environment_llm, runtime_options, validate_base_url
from ..core.net import external_client
from ..repository.llm_models import LLMModelStore


class LLMKeyError(RuntimeError):
    """A missing or invalid encryption key must never discard stored credentials."""


_KEY_ERROR = '模型加密密钥不可用或不匹配；请恢复原始 KG_LLM_ENCRYPTION_KEY 或密钥文件'
_KEY_MARKER = b'knowledge-service:llm-key:v1'


def _protect(path):
    if os.name != 'nt':
        os.chmod(path, 0o600)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    token = wintypes.HANDLE()
    string_sid = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError('Cannot read process identity')
        required = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        user = ctypes.create_string_buffer(required.value)
        if not security.GetTokenInformation(token, 1, user, required, ctypes.byref(required)):
            raise OSError('Cannot read process identity')
        sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(string_sid)):
            raise OSError('Cannot read process identity')
        # A protected DACL grants only this process user, removing inherited and explicit grants.
        acl = 'D:P(A;;FA;;;' + string_sid.value + ')'
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                acl, 1, ctypes.byref(descriptor), None):
            raise OSError('Cannot construct private file permissions')
        if not security.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError('Cannot protect key file')
    except OSError:
        raise LLMKeyError('无法限制模型密钥文件权限') from None
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if string_sid:
            kernel.LocalFree(ctypes.cast(string_sid, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def _local_key(path, *, allow_create):
    if not path.exists():
        if not allow_create:
            raise LLMKeyError(_KEY_ERROR)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.llm-key-', dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                _protect(temporary)
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Atomic publication: competitors see a complete, protected key.
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if os.name == 'nt':
        _protect(path)
    if os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LLMKeyError('模型密钥文件权限过宽；请限制为仅当前用户可读写')
    return path.read_bytes().strip()


class LLMModels:
    def __init__(self, repository):
        self.repository = repository
        self.store = LLMModelStore(repository)

    def _fernet(self):
        try:
            # Row lock serializes first-use initialization across app instances.
            with self.repository._transaction():
                row = self.repository._db.execute(
                    'SELECT key_check FROM llm_model_settings FOR UPDATE').fetchone()
                marker = row['key_check']
                if not marker and self.repository._db.execute(
                        '''SELECT EXISTS(SELECT 1 FROM llm_models)
                            OR EXISTS(SELECT 1 FROM llm_task_snapshots)''').fetchone()[0]:
                    raise LLMKeyError(_KEY_ERROR)
                explicit = (os.getenv('KG_LLM_ENCRYPTION_KEY') or '').strip()
                if explicit:
                    key = explicit.encode('ascii')
                else:
                    default = Path(__file__).resolve().parents[2] / 'data/runtime/llm.key'
                    path = Path(os.getenv('KG_LLM_KEY_FILE') or default)
                    key = _local_key(path, allow_create=not marker)
                cipher = Fernet(key)
                if marker:
                    if cipher.decrypt(marker.encode()) != _KEY_MARKER:
                        raise LLMKeyError(_KEY_ERROR)
                else:
                    self.repository._db.execute('UPDATE llm_model_settings SET key_check=?',
                        (cipher.encrypt(_KEY_MARKER).decode(),))
                return cipher
        except (OSError, ValueError, InvalidToken, UnicodeError):
            raise LLMKeyError(_KEY_ERROR) from None

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ('id', 'name', 'base_url', 'model', 'is_active')} | {
            'has_api_key': bool(row['api_key_encrypted'])}

    def _config(self, row):
        try:
            key = self._fernet().decrypt(row['api_key_encrypted'].encode()).decode()
        except (InvalidToken, UnicodeError):
            raise LLMKeyError(_KEY_ERROR) from None
        return LLMConfig(profile_id=row['id'], name=row['name'],
                         base_url=row['base_url'], model=row['model'], api_key=key,
                         **runtime_options())

    def resolve(self):
        row = self.store.active()
        return self._config(row) if row else environment_llm()

    def list(self):
        items = [self._public(row) for row in self.store.list()]
        active = next((item for item in items if item['is_active']), None)
        try:
            environment = environment_llm()
        except ValueError:
            environment = LLMConfig()
        if active is None and environment.configured:
            active = {'id': 'environment', 'name': environment.name,
                      'base_url': environment.public_identity()['base_url'], 'model': environment.model,
                      'has_api_key': bool(environment.api_key), 'is_active': True}
        return {'items': items, 'active': active,
                'environment_available': environment.configured}

    @staticmethod
    def _text(value, field, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f'{field}不能为空且不能超过 {maximum} 个字符')
        if any(ord(char) < 32 for char in value):
            raise ValueError(f'{field}不能包含控制字符')
        return value.strip()

    def create(self, *, name, base_url, model, api_key):
        name = self._text(name, '名称', 128)
        model = self._text(model, '模型名', 256)
        base_url = validate_base_url(base_url)
        key = self._text(api_key, 'API key', 8192)
        encrypted = self._fernet().encrypt(key.encode()).decode()
        return self._public(self.store.create(name=name, base_url=base_url,
                            model=model, api_key_encrypted=encrypted))

    def update(self, ident, *, name=None, base_url=None, model=None, api_key=None):
        if not self.store.get(ident):
            raise KeyError(ident)
        # Verify the original key even when the edit keeps or replaces its secret.
        self._fernet()
        encrypted = None
        if api_key is not None and api_key.strip():
            key = self._text(api_key, 'API key', 8192)
            encrypted = self._fernet().encrypt(key.encode()).decode()
        return self._public(self.store.update(ident,
            name=self._text(name, '名称', 128) if name is not None else None,
            model=self._text(model, '模型名', 256) if model is not None else None,
            base_url=validate_base_url(base_url) if base_url is not None else None,
            api_key_encrypted=encrypted))

    def activate(self, ident):
        row = self.store.get(ident)
        if row is None:
            raise KeyError(ident)
        self._config(row)
        return self._public(self.store.activate(ident))

    def seal(self, config: LLMConfig) -> str:
        return self._fernet().encrypt(json.dumps(asdict(config), ensure_ascii=False).encode()).decode()

    def unseal(self, sealed: str) -> LLMConfig:
        try:
            data = json.loads(self._fernet().decrypt(sealed.encode()))
            return LLMConfig(**data)
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise LLMKeyError(_KEY_ERROR) from None

    def pin(self, task_id: str) -> LLMConfig:
        existing = self.store.snapshot(task_id)
        if existing:
            return self.unseal(existing)
        config = bound_llm_config() or self.resolve()
        sealed = self.store.pin(task_id, self.seal(config), config.public_identity())
        return self.unseal(sealed)

    def test_connection(self, ident):
        row = self.store.get(ident)
        if row is None:
            raise KeyError(ident)
        config = self._config(row)
        try:
            with external_client(timeout=20, proxy=config.proxy) as client:
                response = client.post(config.base_url + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + config.api_key}, json={
                        'model': config.model, 'messages': [{'role': 'user', 'content': 'Reply OK'}],
                        'max_tokens': 16, 'stream': False})
                if not response.is_success:
                    return {'ok': False, 'detail': f'模型服务连接失败（HTTP {response.status_code}）'}
                data = response.json()
                choices = data.get('choices') if isinstance(data, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) \
                        or not isinstance(choices[0].get('message'), dict):
                    return {'ok': False, 'detail': '模型服务返回了无效的 chat completions 响应'}
                message = choices[0]['message']
                if not any(isinstance(message.get(field), str) and message[field].strip()
                           for field in ('content', 'reasoning_content', 'refusal')):
                    return {'ok': False, 'detail': '模型服务未返回有效内容，请检查模型配置'}
                return {'ok': True, 'detail': '连接测试成功'}
        except (httpx.HTTPError, ValueError):
            return {'ok': False, 'detail': '模型服务连接失败，请检查地址、密钥和网络'}
