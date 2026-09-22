"""限定来源的知识断言及其审核生命周期。"""
import hashlib
import json
import re
import unicodedata


ASSERTION_KINDS = frozenset({'entity', 'relation', 'attribute', 'validation'})
ASSERTION_STATUSES = frozenset({'pending', 'accepted', 'rejected', 'contradicting', 'superseded'})

ALLOWED_TRANSITIONS = {
    'pending': frozenset({'accepted', 'rejected', 'contradicting', 'superseded'}),
    'accepted': frozenset({'contradicting', 'rejected', 'superseded'}),
    'contradicting': frozenset({'accepted', 'rejected', 'superseded'}),
    'rejected': frozenset(),
    'superseded': frozenset(),
}


def _normalise_term(value):
    if not isinstance(value, str):
        raise ValueError('断言原始词条必须是字符串')
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', value)).strip()


def occurrence_id(storage_namespace, document_version_id, chunk_id, kind, ordinal, raw_terms):
    """返回单个抽取出现的稳定身份标识，而非规范事实的标识。"""
    if not all(isinstance(value, str) and value for value in
               (storage_namespace, document_version_id, chunk_id, kind)):
        raise ValueError('断言出现需要来源身份字段')
    if kind not in ASSERTION_KINDS:
        raise ValueError('不支持的断言类型')
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError('断言序号必须是非负整数')
    if not isinstance(raw_terms, (list, tuple)) or not raw_terms:
        raise ValueError('断言出现需要原始词条')
    identity = {
        'namespace': storage_namespace,
        'document_version_id': document_version_id,
        'chunk_id': chunk_id,
        'kind': kind,
        'ordinal': ordinal,
        'raw_terms': [_normalise_term(value) for value in raw_terms],
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'ast_' + hashlib.sha256(encoded).hexdigest()