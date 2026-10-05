"""只读结构投影：层级树、搜索、邻域与关系矩阵。
"""

from __future__ import annotations

from .ontology_drafts_core import (
    NEIGHBORHOOD_LINK_LIMIT, _text,
)
from .ontology import Ontology
from .ontology import local_name
from rdflib import RDFS
from rdflib import URIRef
import base64



class DraftReadModelsMixin:
    """只读结构投影：层级树、搜索、邻域与关系矩阵。"""

    # ------------------------------------------------------------ read models
    def _read_ontology(self, project_id, ontology_id=None, draft_id=None):
        # 读「草案叠加层」时，基线只有一个正确来源：草案自己的 base_ontology_id。
        #
        # 这里以前会拿调用方顺手带上的 ontology_id 跟草案基线比对，不一致就抛
        # ValueError('draft overlay must use its own base ontology')。但调用方带的
        # 那个 id 几乎总是「项目当前版本」，而草案往往建立在更早的版本上——甚至
        # 建立在还没有本体的空项目上（base_ontology_id=None）。于是只要草案发布过
        # 一次，当前版本就必然前移，之后每次读层级树/关系矩阵都 422，画布直接空白。
        # 现在：草案请求一律以草案基线为准，ontology_id 只用于「看历史版本」。
        if draft_id is not None:
            draft = self.store.get(project_id, draft_id)
            turtle, _ = self._ontology_for_draft(project_id, draft)
            return Ontology(turtle), draft['base_ontology_id']
        if ontology_id is None:
            ontology_id = self._latest_id(project_id)
        return Ontology(self._base_turtle(project_id, ontology_id)), ontology_id

    @staticmethod
    def _decode_cursor(cursor):
        if cursor in (None, ''):
            return 0
        try:
            return int(base64.urlsafe_b64decode(
                str(cursor).encode('ascii') + b'===').decode('ascii'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc

    def _page_slice(self, items, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        offset = self._decode_cursor(cursor)
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(items):
            next_cursor = base64.urlsafe_b64encode(
                str(next_offset).encode('ascii')).decode('ascii').rstrip('=')
        return page, {'next_cursor': next_cursor,
                      'total': len(items), 'limit': limit}

    def _page(self, items, cursor, limit):
        page, metadata = self._page_slice(items, cursor, limit)
        return {'items': page, **metadata}

    @staticmethod
    def _class_maps(ontology):
        active = {node for node in ontology.classes if ontology.is_active_term(node)}
        parents = {
            node: {parent for parent in ontology.graph.objects(node, RDFS.subClassOf)
                   if parent in active}
            for node in active}
        children = {node: set() for node in active}
        for child, values in parents.items():
            for parent in values:
                children[parent].add(child)
        return active, parents, children

    @staticmethod
    def _term_item(ontology, node):
        labels = list(ontology.graph.objects(node, RDFS.label))
        zh = en = plain = ''
        for label in labels:
            language = (label.language or '').lower()
            if not zh and language.startswith('zh'):
                zh = str(label)
            elif not en and language.startswith('en'):
                en = str(label)
            elif not plain and not language:
                plain = str(label)
        item = {
            'id': str(node), 'name': local_name(node),
            'label': plain or en or local_name(node),
            'label_zh': zh, 'label_en': en,
            'description': str(ontology.graph.value(node, RDFS.comment) or ''),
            'active': ontology.is_active_term(node),
        }
        if node in ontology.classes:
            item['parents'] = [
                str(value) for value in ontology.graph.objects(
                    node, RDFS.subClassOf)]
        elif node in ontology.relations:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.range)]
        elif node in ontology.attributes:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.graph.objects(node, RDFS.range)]
        return item

    def _display_path(self, node, parents, ontology):
        memo = {}
        visiting = set()

        def best_path(current):
            if current in memo:
                return memo[current]
            if current in visiting:
                return (current,)
            visiting.add(current)
            direct = sorted(parents.get(current, set()), key=str)
            candidates = [best_path(parent) + (current,) for parent in direct]
            visiting.remove(current)
            best = min(
                candidates or [(current,)],
                key=lambda path: (len(path), tuple(map(str, path))))
            memo[current] = best
            return best

        best = best_path(node)
        return [{'iri': str(item), 'label': self._term_item(
            ontology, item)['label']} for item in best]

    def _class_item(self, node, parents, children, ontology, *, parent=None):
        item = self._term_item(ontology, node)
        return {
            **item, 'iri': str(node), 'canonical_iri': str(node),
            'is_reference': len(parents.get(node, set())) > 1 and parent is not None,
            'child_count': len(children.get(node, set())),
            'other_parent_count': max(0, len(parents.get(node, set())) -
                                      (1 if parent is not None else 0)),
            'display_path': self._display_path(node, parents, ontology),
        }

    def roots(self, project_id, *, ontology_id=None, draft_id=None,
              cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        nodes = [node for node in sorted(active, key=str) if not parents[node]]
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology) for node in page], **metadata}

    def children(self, project_id, iri, *, ontology_id=None, draft_id=None,
                 cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        _, parents, children = self._class_maps(ontology)
        parent = URIRef(iri)
        nodes = sorted(children.get(parent, set()), key=str)
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology, parent=parent)
            for node in page], **metadata}

    def search(self, project_id, query, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        needle = _text(query, 'query').casefold()
        active, parents, children = self._class_maps(ontology)
        declared = sorted(
            (ontology.classes | ontology.relations | ontology.attributes), key=str)
        matches = []
        for node in declared:
            if not ontology.is_active_term(node):
                continue
            all_labels = [str(value) for value in ontology.graph.objects(
                node, RDFS.label)]
            description = str(ontology.graph.value(node, RDFS.comment) or '')
            haystack = ' '.join([
                str(node), local_name(node), description,
                *all_labels,
            ])
            if needle in haystack.casefold():
                matches.append(node)
        page, metadata = self._page_slice(matches, cursor, limit)
        items = []
        for node in page:
            iri = str(node)
            if node in active:
                items.append(self._class_item(
                    node, parents, children, ontology))
            else:
                items.append({**self._term_item(ontology, node),
                              'iri': iri, 'canonical_iri': iri})
        return {'items': items, **metadata}

    def neighborhood(self, project_id, iri, *, ontology_id=None, draft_id=None,
                     cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        node = URIRef(iri)
        if node not in active:
            # 不要抛裸 KeyError：全局 handler 会把它转成 404「未找到：<一长串 IRI>」，
            # 用户只能看到一个裸 IRI，既不知道发生了什么，也不知道能干什么。
            # 改成返回一个前端能优雅处理的结果，把原因说成人话：
            # 要么是「已停用」（在词汇表里但被 owl:deprecated 标停），
            # 要么是「幽灵引用」（已删除，或属于别的版本/别的项目）。
            known = node in (ontology.classes | ontology.relations | ontology.attributes)
            return {
                'term': None,
                'not_found': True,
                'reason': ('该对象已停用，不在当前本体的活动对象里。'
                           if known else
                           '该对象不在当前本体的对象里（可能已被删除，或属于其它版本）。'),
                'iri': iri,
            }
        adjacent = sorted(parents[node] | children[node], key=str)
        page, metadata = self._page_slice(adjacent, cursor, limit)
        result = {'items': [self._class_item(
            value, parents, children, ontology,
            parent=node if value in children[node] else None)
            for value in page], **metadata}
        result['term'] = self._class_item(node, parents, children, ontology)
        relation_nodes = sorted((
            value for value in ontology.relations
            if ontology.is_active_term(value)
            and node in (set(ontology.constraint_types(value, RDFS.domain))
                         | set(ontology.constraint_types(value, RDFS.range)))), key=str)
        attribute_nodes = sorted((
            value for value in ontology.attributes
            if ontology.is_active_term(value)
            and node in set(ontology.constraint_types(value, RDFS.domain))), key=str)
        projection_limit = min(limit, NEIGHBORHOOD_LINK_LIMIT)
        result['relations'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'relation',
        } for value in relation_nodes[:projection_limit]]
        result['attributes'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'attribute',
        } for value in attribute_nodes[:projection_limit]]
        result.update({
            'relation_count': len(relation_nodes),
            'attribute_count': len(attribute_nodes),
            'relations_truncated': len(relation_nodes) > projection_limit,
            'attributes_truncated': len(attribute_nodes) > projection_limit,
            'link_projection_limit': projection_limit,
        })
        return result

    def matrix(self, project_id, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        nodes = sorted([
            *((value, 'relation') for value in ontology.relations
              if ontology.is_active_term(value)),
            *((value, 'attribute') for value in ontology.attributes
              if ontology.is_active_term(value)),
        ], key=lambda item: str(item[0]))
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {
            'items': [{
                **self._term_item(ontology, node),
                'iri': str(node), 'kind': kind,
            } for node, kind in page],
            **metadata,
        }
