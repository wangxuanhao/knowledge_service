"""已保存项目与规则演示图谱输出的只读迁移。

旧数据事实使用已声明的、不受约束的词汇表。校验结果与
原始载荷保留在审计文档中；导入从不虚构日期。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from .ontology import Ontology


class LegacyImporter:
    def __init__(self, service, root=None):
        self.service = service
        self.root = Path(root or Path(__file__).resolve().parents[2]).resolve()

    def _folder(self, name, kind):
        if kind not in ('project', 'output'):
            raise ValueError('旧数据种类必须是 project 或 output')
        if not isinstance(name, str) or not name or name in ('.', '..') or any(c in name for c in '/\\:'):
            raise ValueError('无效的旧数据项目名称')
        base = self.root / 'data' / ('projects' if kind == 'project' else 'rule_demo_output')
        if not base.resolve().is_relative_to(self.root):
            raise ValueError('旧数据目录越界')
        folder = base / name
        if not folder.resolve().is_relative_to(base.resolve()):
            raise ValueError('旧数据目录越界')
        if not folder.is_dir():
            raise ValueError('旧数据项目不存在')
        return folder.resolve()

    @staticmethod
    def _read(folder, relative, default=None):
        path = folder / relative
        if not path.resolve().is_relative_to(folder):
            raise ValueError('旧数据文件越界')
        if not path.exists():
            return default
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('旧数据文件超过 100 MiB 限制')
        raw = path.read_text(encoding='utf-8-sig')
        return raw if path.suffix == '.graphml' else json.loads(raw)

    def _load(self, name, kind):
        folder = self._folder(name, kind)
        files = {}
        paths = ['manifest.json', 'graph.json', 'entities.json', 'segments.json'] if kind == 'project' else [
            'graph.json', 'graph.graphml', 'faiss/segments.json', 'faiss/corpus.json', 'run_info.json']
        if kind == 'output':
            paths += [p.name for p in sorted(folder.glob('*.json')) if p.name not in paths]
        for path in paths:
            value = self._read(folder, path)
            if value is not None:
                files[path] = value
        graph = files.get('graph.json')
        if graph is None and 'graph.graphml' in files:
            raw = files['graph.graphml']
            if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
                raise ValueError('不支持 GraphML 文档声明')
            xml = ElementTree.fromstring(raw)
            ns = '{http://graphml.graphdrawing.org/xmlns}'
            keys = {k.attrib['id']: (k.attrib.get('attr.name', k.attrib['id']), k.attrib.get('attr.type'))
                    for k in xml.findall(ns + 'key')}
            def attributes(element):
                result = dict(element.attrib)
                for data in element.findall(ns + 'data'):
                    key, typ = keys.get(data.attrib['key'], (data.attrib['key'], 'string'))
                    value = data.text or ''
                    if typ in ('double', 'float'):
                        value = float(value)
                    elif typ in ('int', 'long'):
                        value = int(value)
                    elif typ == 'boolean':
                        value = value.lower() == 'true'
                    result[key] = value
                return result
            graph = {'nodes': [attributes(n) for n in xml.iter(ns + 'node')],
                     'edges': [attributes(e) for e in xml.iter(ns + 'edge')]}
        if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list):
            raise ValueError('旧数据项目需要包含节点的图谱')
        edges = graph.get('edges', graph.get('links', []))
        segments = files.get('segments.json', files.get('faiss/segments.json', []))
        if not isinstance(edges, list) or not isinstance(segments, list):
            raise ValueError('旧数据边和片段必须是列表')
        return files, graph['nodes'], edges, segments

    def _key(self, name, kind):
        return hashlib.sha256(f'{self.root}\0{kind}\0{name}'.encode()).hexdigest()

    def _existing(self, key):
        for project in self.service.repository.list_projects():
            if project['metadata'].get('legacy_import_key') == key:
                records = self.service.repository.current_records(project['id'])
                audit = next((r for r in records if r['metadata'].get('legacy_import_complete')), None)
                return project, audit
        return None, None

    def list_projects(self):
        result = []
        for kind, directory in [('project', 'projects'), ('output', 'rule_demo_output')]:
            base = self.root / 'data' / directory
            if not base.resolve().is_relative_to(self.root):
                continue
            if not base.is_dir():
                continue
            for folder in sorted(base.iterdir()):
                if not folder.is_dir():
                    continue
                try:
                    _, nodes, edges, segments = self._load(folder.name, kind)
                    project, audit = self._existing(self._key(folder.name, kind))
                    result.append({'name': folder.name, 'kind': kind, 'nodes': len(nodes),
                                   'edges': len(edges), 'segments': len(segments),
                                   'imported_project_id': project['id'] if audit else None})
                except (ValueError, OSError, ElementTree.ParseError):
                    # 无效条目无法导入，也不会作为可选项提供。
                    continue
        return result

    def import_project(self, name, kind='project', progress=None, build_vectors=True):
        with self.service.lock:
            self._folder(name, kind)
            key = self._key(name, kind)
            project, audit = self._existing(key)
            if audit:
                return {'project': project, 'counts': audit['metadata']['counts'],
                        'warnings': audit['metadata']['warnings'], 'already_imported': True}
            files, nodes, edges, segments = self._load(name, kind)
            rows, turtle, validation, warnings = self._prepare(files, nodes, edges, segments)
            counts = {'nodes': len(nodes), 'edges': len(edges), 'segments': len(segments),
                      'documents': sum(r['kind'] == 'document' for r in rows)}
            rows.append({'id': 'legacy:audit', 'kind': 'document', 'text': f'旧数据导入审计：{name}',
                         'metadata': {'_audit': True, 'legacy_import_complete': True, 'legacy_files': files, 'counts': counts,
                                      'validation': validation, 'warnings': warnings}})
            # 使用当前配置的模型对所有可检索行重新编码。
            for offset in range(0, len(rows) if build_vectors else 0, 64):
                if progress: progress(f'正在编码旧数据记录 {offset}/{len(rows)}',10+int(80*offset/max(1,len(rows))))
                batch = rows[offset:offset + 64]
                vectors = self.service.encoder.encode([r['text'] for r in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError('embedding 输出数量不匹配')
                for row, vector in zip(batch, vectors):
                    row.update(embedding=vector, embedding_model=self.service.encoder.identity)
            repository = self.service.repository
            rows = [repository._validate_record(r) for r in rows]
            if project is None:
                project = repository.create_project(name, {'legacy_import_key': key, 'legacy_name': name,
                                                           'legacy_kind': kind, 'legacy_root': str(self.root)})
            versions = repository.list_ontologies(project['id'])
            ontology = versions[-1] if versions else repository.bootstrap_ontology(
                project['id'], turtle, Ontology(turtle).summary())
            for row in rows:
                if row['kind'] in ('entity', 'relation'):
                    row['ontology_id'] = ontology['id']
            repository.put_batch(project['id'], rows,
                expected_versions={r['id']: 0 for r in rows},formal_operation='legacy_import')
            return {'project': project, 'counts': counts, 'warnings': warnings, 'already_imported': False}

    def _prepare(self, files, nodes, edges, segments):
        warnings = ['旧数据的业务有效性未知；导入时间不是生效日期。',
                    '未复用旧数据向量；记录已用当前配置的模型重新编码。']
        rows, node_ids = [], {}
        vocabulary = Graph()
        def term(value, kind):
            value = str(value or ('Entity' if kind == 'entity' else 'relatedTo'))
            uri = URIRef('urn:legacy:' + ('class/' if kind == 'entity' else 'relation/') + quote(value, safe=''))
            vocabulary.add((uri, RDF.type, OWL.Class if kind == 'entity' else OWL.ObjectProperty))
            vocabulary.add((uri, RDFS.label, Literal(value)))
            return str(uri)
        manifest = files.get('manifest.json', {})
        sources = dict(manifest.get('sources', {})) if isinstance(manifest.get('sources'), dict) else {}
        source_names = set(sources)
        for raw in [*nodes, *edges, *segments]:
            if not isinstance(raw, dict):
                raise ValueError('旧数据图谱和片段必须包含对象')
            if isinstance(raw.get('source_file'), str) and raw['source_file']:
                source_names.add(raw['source_file'])
        source_ids = {}
        for index, source in enumerate(sorted(source_names)):
            source_ids[source] = f'legacy:document:{index}'
            original = sources.get(source)
            text = original if isinstance(original, str) else '\n\n'.join(
                str(s.get('text', '')) for s in segments if s.get('source_file') == source)
            rows.append({'id': source_ids[source], 'kind': 'document', 'text': text,
                         'metadata': {'title': source, 'source_file': source, 'status': 'ready',
                                      'legacy': {'source_content': 'original' if isinstance(original, str) else 'segments_only',
                                                 'raw': original}}})
            if not isinstance(original, str):
                warnings.append(f'来源 {source}：完整原文不可用；已保留可用的片段与证据。')
        def record(identifier, kind, text, raw, **extra):
            metadata = {**(raw.get('metadata') if isinstance(raw.get('metadata'), dict) else {}),
                        'legacy': {'raw': raw}, 'status': 'ready'}
            for field in ('source_file', 'aliases', 'confidence', 'passage'):
                if field in raw:
                    metadata[field] = raw[field]
            row = {'id': identifier, 'kind': kind, 'text': str(text), 'metadata': metadata,
                   'valid_from': None, 'valid_until': None, **extra}
            if raw.get('source_file') in source_ids:
                row['source_id'] = source_ids[raw['source_file']]
            rows.append(row)
        for index, raw in enumerate(nodes):
            original = str(raw.get('id', raw.get('text', index)))
            if original in node_ids:
                raise ValueError(f'重复的旧数据节点 ID：{original}')
            identifier = f'legacy:entity:{index}'
            node_ids[original] = identifier
            record(identifier, 'entity', raw.get('text', original), raw,
                   type=term(raw.get('label', raw.get('type')), 'entity'),
                   properties=raw.get('properties') if isinstance(raw.get('properties'), dict) else {})
            aliases=manifest.get('aliases',{}).get(original,[]) if isinstance(manifest.get('aliases'),dict) else []
            existing=rows[-1]['metadata'].get('aliases',[])
            rows[-1]['metadata']['aliases']=list(dict.fromkeys(x for x in [*(existing if isinstance(existing,list) else []),*(aliases if isinstance(aliases,list) else [])] if isinstance(x,str)))
        for index, raw in enumerate(edges):
            source = str(raw.get('source', raw.get('subject', raw.get('subject_id', ''))))
            target = str(raw.get('target', raw.get('object', raw.get('object_id', ''))))
            for endpoint in (source, target):
                if endpoint not in node_ids:
                    node_ids[endpoint] = f'legacy:entity:{len(node_ids)}'
                    record(node_ids[endpoint], 'entity', endpoint,
                           {'id': endpoint, 'unresolved_endpoint': True},
                           type=term('UnresolvedLegacyEndpoint', 'entity'))
                    warnings.append(f'旧数据端点 {endpoint} 缺失；已作为未解析实体保留。')
            predicate = raw.get('predicate', raw.get('type', raw.get('relation', 'relatedTo')))
            record(f'legacy:relation:{index}', 'relation', raw.get('text', f'{source} {predicate} {target}'), raw,
                   type=term(predicate, 'relation'), subject_id=node_ids[source], object_id=node_ids[target],
                   properties=raw.get('properties') if isinstance(raw.get('properties'), dict) else {})
        for index, raw in enumerate(segments):
            record(f'legacy:chunk:{index}', 'chunk', raw.get('text', ''), raw)
        turtle = vocabulary.serialize(format='turtle')
        validation = Ontology(turtle).validate(rows)
        validation['policy'] = '已声明的旧数据词汇表，不做追溯性的 domain/range 或 SHACL 约束'
        default_path = Path(__file__).resolve().parents[1] / 'resources/default_ontology.ttl'
        baseline = [{**r, 'type': unquote(r['type'].rsplit('/', 1)[-1])} if r.get('type') else r for r in rows]
        validation['default_ontology'] = Ontology(default_path.read_text(encoding='utf-8')).validate(baseline)
        if not validation['default_ontology']['conforms']:
            warnings.append('旧数据事实不符合默认本体；校验结果保留在审计中，不会丢弃事实。')
        return rows, turtle, validation, warnings
