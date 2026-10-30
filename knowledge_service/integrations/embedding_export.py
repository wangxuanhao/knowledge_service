"""Offline preparation of the supported dense pipeline; imports stay lightweight.

The serving process never imports this exporter's Torch/SentenceTransformers
dependencies. An export is activated only after token and FP32 vector parity.
"""
import gc
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .embedding_artifacts import UNSUPPORTED_SOURCE_FILES


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def inspect_pipeline(source):
    root = Path(source).resolve()
    expected = [
        ('sentence_transformers.models.Transformer', ''),
        ('sentence_transformers.models.Pooling', '1_Pooling'),
        ('sentence_transformers.models.Normalize', '2_Normalize'),
    ]
    modules = _json(root / 'modules.json')
    if not isinstance(modules, list) or len(modules) != len(expected) or any(
            not isinstance(module, dict) or (module.get('type'), module.get('path')) != wanted
            or module.get('idx') != index or module.get('name') != str(index)
            for index, (module, wanted) in enumerate(zip(modules, expected))):
        raise ValueError('仅支持已验证的 Transformer/CLS/Normalize pipeline 流水线')
    model = _json(root / 'config.json')
    if (model.get('model_type') != 'xlm-roberta' or
            model.get('architectures') != ['XLMRobertaModel'] or model.get('auto_map') or
            model.get('quantization_config') or model.get('torch_dtype', 'float32') != 'float32'):
        raise ValueError('仅支持 FP32 XLMRobertaModel，无自定义模型或量化')
    dimension = model.get('hidden_size')
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise ValueError('模型维度无效')
    pooling = _json(root / '1_Pooling/config.json')
    if (pooling.get('word_embedding_dimension') != dimension or
            pooling.get('pooling_mode_cls_token') is not True or any(
                value for key, value in pooling.items()
                if key.startswith('pooling_mode_') and key != 'pooling_mode_cls_token')):
        raise ValueError('仅支持 CLS pooling 池化，不能合并其他池化输出')
    sentence = _json(root / 'sentence_bert_config.json')
    if set(sentence) - {'max_seq_length', 'do_lower_case'}:
        raise ValueError('不支持 custom 自定义 Transformer 选项')
    maximum = sentence.get('max_seq_length')
    if (isinstance(maximum, bool) or not isinstance(maximum, int) or not 2 <= maximum <= 8192
            or sentence.get('do_lower_case', False) is not False):
        raise ValueError('不支持的分词长度或大小写配置')
    tokenizer = _json(root / 'tokenizer_config.json')
    if (tokenizer.get('tokenizer_class') not in {'XLMRobertaTokenizer', 'XLMRobertaTokenizerFast'}
            or tokenizer.get('padding_side', 'right') != 'right'
            or tokenizer.get('truncation_side', 'right') != 'right'
            or tokenizer.get('pad_token') != '<pad>' or model.get('pad_token_id') != 1):
        raise ValueError('不支持的 XLM-R 分词/padding 配置')
    configuration = root / 'config_sentence_transformers.json'
    if configuration.exists():
        config = _json(configuration)
        if (config.get('prompts') or config.get('default_prompt_name') or
                config.get('truncate_dim') or config.get('model_kwargs')):
            raise ValueError('不支持 prompt 提示或自定义输出配置')
    if not (root / 'pytorch_model.bin').is_file() or any(
            (root / name).exists() or (root / name).is_symlink() for name in UNSUPPORTED_SOURCE_FILES):
        raise ValueError('仅支持单一 pytorch_model.bin 权重，拒绝选择不同权重')
    if not (root / 'tokenizer.json').is_file():
        raise ValueError('缺少 fast tokenizer.json')
    return {'dimension': dimension, 'max_length': maximum, 'pad_id': 1,
            'pad_token': '<pad>', 'do_lower_case': False}


def _activate_export(destination, staging, version, manifest):
    staging.rename(destination / version)
    temporary = destination / ('.active-' + version + '.json')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination / 'active.json')


def prepare_onnx_runtime(source, *, rebuild=False, progress=None):
    """Build a new immutable version and atomically activate a verified export."""
    from .embedding_artifacts import file_sha256, read_export_manifest

    root = Path(source).resolve()
    settings = inspect_pipeline(root)
    if not rebuild and (existing := read_export_manifest(root)) is not None:
        return existing[1]
    report = progress or (lambda event: None)
    started = time.perf_counter()
    from sentence_transformers import SentenceTransformer
    import torch
    import numpy as np
    report({'phase': 'preparation_dependencies', 'seconds': round(time.perf_counter()-started, 3)})
    torch.set_num_threads(4)
    names = ['config.json', 'modules.json', 'sentence_bert_config.json',
             '1_Pooling/config.json', 'tokenizer.json', 'tokenizer_config.json',
             'pytorch_model.bin', 'special_tokens_map.json', 'config_sentence_transformers.json']
    source_files = {name: file_sha256(root / name) for name in names if (root / name).is_file()}
    version = uuid.uuid4().hex
    destination = root / 'onnx-runtime'
    staging = destination / (version + '.staging')
    staging.mkdir(parents=True)
    started = time.perf_counter()
    model = SentenceTransformer(str(root), local_files_only=True, device='cpu').float().eval()
    samples = ['未成年', '美团', '直播限制时段是什么时间', '', '   ', '  前后空白测试  ',
               'Mixed English 中文 123 😊', '美团', 'a', '直播规则未成年人保护和夜间直播限制。' * 24]
    reference = model.encode(samples, batch_size=2, normalize_embeddings=True)
    token_samples = samples + ['a ' * (settings['max_length'] - 2),
                               'a ' * (settings['max_length'] + 3)]
    token_reference = []
    for text in token_samples:
        tokens = model.tokenize([text])
        token_reference.append({name: tokens[name][0].tolist() for name in ('input_ids', 'attention_mask')})
    features = model.tokenize(samples[:2])
    report({'phase': 'reference', 'seconds': round(time.perf_counter()-started, 3)})

    class Dense(torch.nn.Module):
        def __init__(self, transformer):
            super().__init__()
            self.transformer = transformer

        def forward(self, input_ids, attention_mask):
            cls = self.transformer(input_ids=input_ids, attention_mask=attention_mask,
                                   return_dict=False)[0][:, 0]
            # The saved Normalize module and encode(normalize_embeddings=True).
            return torch.nn.functional.normalize(
                torch.nn.functional.normalize(cls, p=2, dim=1), p=2, dim=1)

    started = time.perf_counter()
    with torch.inference_mode():
        torch.onnx.export(Dense(model[0].auto_model).eval(),
            (features['input_ids'], features['attention_mask']), str(staging / 'model.onnx'),
            input_names=['input_ids', 'attention_mask'], output_names=['sentence_embedding'],
            dynamic_axes={'input_ids': {0: 'batch', 1: 'sequence'},
                          'attention_mask': {0: 'batch', 1: 'sequence'},
                          'sentence_embedding': {0: 'batch'}}, opset_version=17, dynamo=False)
    shutil.copyfile(root / 'tokenizer.json', staging / 'tokenizer.json')
    del features, tokens, model
    gc.collect()  # Release the large Torch weights before creating an ORT session.
    report({'phase': 'export', 'seconds': round(time.perf_counter()-started, 3)})

    import onnxruntime as ort
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(staging / 'tokenizer.json'))
    tokenizer.enable_truncation(max_length=settings['max_length'], strategy='longest_first')
    tokenizer.enable_padding(pad_id=settings['pad_id'], pad_token=settings['pad_token'])
    for text, expected in zip(token_samples, token_reference):
        actual = tokenizer.encode(text.strip())
        if actual.ids != expected['input_ids'] or actual.attention_mask != expected['attention_mask']:
            raise ValueError('分词不一致，拒绝激活导出模型')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(staging / 'model.onnx'), sess_options=options,
                                   providers=['CPUExecutionProvider'])

    def infer(texts):
        encoded = tokenizer.encode_batch([text.strip() for text in texts])
        return session.run(['sentence_embedding'], {
            'input_ids': np.asarray([row.ids for row in encoded], dtype=np.int64),
            'attention_mask': np.asarray([row.attention_mask for row in encoded], dtype=np.int64)})[0]

    max_error, min_cosine, maximum_norm_error = 0., 1., 0.
    for batch_size in (1, 2, 3):
        output = np.concatenate([infer(samples[offset:offset+batch_size])
                                 for offset in range(0, len(samples), batch_size)])
        if output.shape != reference.shape or output.dtype != np.float32 or not np.isfinite(output).all():
            raise ValueError('导出模型返回无效向量，拒绝激活')
        norms = np.linalg.norm(output.astype(np.float64), axis=1)
        ref64 = reference.astype(np.float64)
        cosine = np.sum(ref64 * output, axis=1) / (np.linalg.norm(ref64, axis=1) * norms)
        max_error = max(max_error, float(np.max(np.abs(reference-output))))
        min_cosine = min(min_cosine, float(np.min(cosine)))
        maximum_norm_error = max(maximum_norm_error, float(np.max(np.abs(norms-1))))
    if max_error > 1e-4 or min_cosine < .99999 or maximum_norm_error > 1e-5:
        raise ValueError('向量等价性验证失败，拒绝激活导出模型')
    del session
    gc.collect()
    if any(file_sha256(root / name) != checksum for name, checksum in source_files.items()):
        raise ValueError('准备期间源模型发生变化，拒绝激活')
    artifact_files = {file.relative_to(staging).as_posix(): file_sha256(file)
                      for file in staging.rglob('*') if file.is_file()}
    manifest = {'schema_version': 1, 'contract': 'xlm-roberta-cls-normalized-v1',
                'artifact_dir': version, 'dtype': 'float32', 'provider': 'CPUExecutionProvider',
                'opset': 17, **settings, 'source_files': source_files,
                'artifact_files': artifact_files, 'validation': {
                    'passed': True, 'max_abs_error': max_error, 'min_cosine': min_cosine,
                    'max_norm_error': maximum_norm_error, 'tokenization_equal': True,
                    'vector_samples': len(samples), 'batch_sizes': [1, 2, 3],
                    'max_vector_tokens': max(sum(row['attention_mask']) for row in token_reference[:len(samples)]),
                    'max_tokenization_tokens': max(len(row['input_ids']) for row in token_reference)}}
    _activate_export(destination, staging, version, manifest)
    report({'phase': 'activated', 'validation': manifest['validation'], 'artifact_dir': version})
    return manifest
