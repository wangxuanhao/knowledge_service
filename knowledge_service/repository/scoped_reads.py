"""Bounded, authoritative scoped reads shared by retrieval and graph sessions."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from contextlib import nullcontext
from copy import copy
from datetime import datetime
from decimal import Decimal
from functools import wraps
from inspect import isgeneratorfunction

from ..core.time import normalize_time
from ..domain.applicability import normalize_applicability
from ..utils.filters import encode_filter_json, validate_filter


KINDS = {'document', 'entity', 'relation', 'attribute', 'chunk'}
FIELDS = 'project_id,id,version,version_id,recorded_at,superseded_at,payload'


def _snapshot_read(operation):
    """Use one isolated repository view for an entire bounded operation."""
    def context(reads):
        factory = getattr(reads.repo, 'independent_read_view', None)
        return factory() if factory is not None else nullcontext(reads.repo)

    def on_repository(reads, repository):
        if repository is reads.repo:
            return reads
        isolated = copy(reads)
        isolated.repo = repository
        return isolated

    if isgeneratorfunction(operation):
        @wraps(operation)
        def stream(self, *args, **kwargs):
            with context(self) as repository:
                yield from operation(on_repository(self, repository), *args, **kwargs)
        return stream

    @wraps(operation)
    def read(self, *args, **kwargs):
        with context(self) as repository:
            return operation(on_repository(self, repository), *args, **kwargs)
    return read


def _json(value):
    return encode_filter_json(value)


def _filter_sql(filters, alias):
    """Compile the public typed predicate language, including missing vs null."""
    validate_filter(filters)
    if filters is None:
        return 'TRUE', []
    for group in ('and', 'or'):
        if group in filters:
            children = [_filter_sql(child, alias) for child in filters[group]]
            return '(' + f' {group.upper()} '.join(sql for sql, _ in children) + ')', [
                value for _, params in children for value in params]
    field, op, target = (filters[key] for key in ('field', 'op', 'value'))
    times = {'valid_from', 'valid_until', 'recorded_at'}
    if field == 'recorded_at':
        expr = f'''to_jsonb(to_char({alias}.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))'''
    elif field == 'id':
        expr = f'to_jsonb({alias}.{field})'
    elif field in {'kind', 'type', 'source_id', 'valid_from', 'valid_until'}:
        expr = f"{alias}.payload::jsonb->'{field}'"
    else:
        parts = field.split('.')
        if parts[0] != 'metadata':
            parts.insert(0, 'metadata')
        # validate_filter restricts each path part to identifiers (no quotes).
        expr = f"{alias}.payload::jsonb#>'{{{','.join(parts)}}}'"
    if op == 'exists':
        return f'({expr} IS {"NOT " if target else ""}NULL)', []
    if field in times:
        target = ([normalize_time(value) for value in target] if op == 'in'
                  else normalize_time(target))
    if op in {'eq', 'ne'}:
        return f'({expr} IS NOT NULL AND {expr} {"=" if op == "eq" else "<>"} ?::jsonb)', [_json(target)]
    if op == 'in':
        if not target:
            return 'FALSE', []
        return '(' + ' OR '.join(f'{expr} = ?::jsonb' for _ in target) + ')', [_json(value) for value in target]
    if op == 'contains':
        sql = (f"(CASE jsonb_typeof({expr}) WHEN 'array' THEN EXISTS ("
               f'SELECT 1 FROM jsonb_array_elements({expr}) value WHERE value = ?::jsonb) '
               f"WHEN 'string' THEN ")
        if isinstance(target, str):
            return sql + f"strpos({expr}#>>'{{}}', ?) > 0 ELSE FALSE END)", [_json(target), target]
        return sql + 'FALSE ELSE FALSE END)', [_json(target)]
    comparator = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
    category = 'number' if isinstance(target, (int, float, Decimal)) and not isinstance(target, bool) else 'string'
    scalar = f"({expr}#>>'{{}}')"
    placeholder, parameter = '?', target
    if category == 'number':
        scalar += '::numeric'
        # Binding float8 here would coerce the exact jsonb number to double.
        placeholder, parameter = '?::numeric', _json(target)
    return (f"(CASE WHEN jsonb_typeof({expr})='{category}' THEN {scalar} {comparator} {placeholder} ELSE FALSE END)", [parameter])


class ScopedReads:
    def __init__(self, repository):
        self.repo = repository

    def _base(self, project, scope, alias='r'):
        scope = scope or {}
        clauses, params = [f'{alias}.project_id=?'], [project]
        known = normalize_time(scope.get('known_at'))
        if scope.get('_source_artifact'):
            if known is not None:
                clauses.append(f'{alias}.recorded_at<=?')
                params.append(known)
        elif known is None:
            clauses.append(f'{alias}.superseded_at IS NULL')
        else:
            clauses.extend([f'{alias}.recorded_at<=?', f'({alias}.superseded_at IS NULL OR {alias}.superseded_at>?)'])
            params.extend([known, known])
        valid = normalize_time(scope.get('valid_at'))
        if valid and not scope.get('_reference_mode'):
            epoch = int(datetime.fromisoformat(valid.replace('Z', '+00:00')).timestamp() * 1000)
            clauses.extend([
                (f'(NOT {alias}.valid_from_known OR {alias}.valid_from_epoch_ms<=?)'
                 if scope.get('include_unknown', True) else
                 f'({alias}.valid_from_known AND {alias}.valid_from_epoch_ms<=?)'),
                f'(NOT {alias}.valid_until_known OR {alias}.valid_until_epoch_ms>?)'])
            params.extend([epoch, epoch])
        if not scope.get('_identity_read'):
            clauses.append(f'{alias}.read_visible')
        selected = normalize_applicability(scope.get('applicability'))
        for dimension, prefix in (('platforms', 'platform'), ('businesses', 'business'), ('audiences', 'audience')):
            selection = selected[dimension]
            if selection['mode'] == 'specific':
                clauses.append(f"({alias}.{prefix}_mode='all' OR ({alias}.{prefix}_mode='specific' AND {alias}.{prefix}_values && ?::text[]))")
                params.append(selection['values'])
        match = scope.get('_platform_match')
        if match:
            modes = {'all_only': 'all', 'unknown_only': 'unknown', 'specific_only': 'specific'}
            if match not in modes:
                raise ValueError('无效的平台组范围')
            clauses.append(f'{alias}.platform_mode=?')
            params.append(modes[match])
        ontology = scope.get('ontology_scope') or 'all'
        if ontology == 'ids':
            clauses.append(f"{alias}.payload::jsonb->>'ontology_id' = ANY(?::text[])")
            params.append(list(scope.get('ontology_ids') or []))
        elif ontology == 'unknown':
            clauses.append(f"NOT EXISTS (SELECT 1 FROM ontologies o WHERE o.project_id={alias}.project_id AND o.id={alias}.payload::jsonb->>'ontology_id')")
        elif ontology != 'all':
            raise ValueError('无效的本体范围')
        filtered, filter_params = _filter_sql(scope.get('filters'), alias)
        clauses.append(filtered)
        params.extend(filter_params)
        return ' AND '.join(clauses), params

    def _qualified(self, project, scope, *, kinds=None, query='', keyset=False, entity_source=None):
        kinds = scope.get('kinds') if kinds is None else kinds
        if kinds is not None and (not isinstance(kinds, (list, tuple, set)) or any(kind not in KINDS for kind in kinds)):
            raise ValueError('无效的记录类型')
        sql, params = self._base(project, scope)
        if kinds is not None and len(kinds) == 1:
            # Scalar equality lets the (project,kind,id,version) keyset index
            # supply ordering directly. ANY(singleton) caused a full sort of
            # the remaining corpus on every graph page at 150k scale.
            sql += " AND r.read_kind = ?"
            params.append(next(iter(kinds)))
        elif kinds is not None:
            sql += " AND r.read_kind = ANY(?::text[])"
            params.append(list(kinds))
        if scope.get('_type_iris') is not None:
            types = scope['_type_iris']
            if not isinstance(types, (list, tuple)) or any(not isinstance(value, str) or not value for value in types):
                raise ValueError('类型通道需要非空类型 IRI 字符串')
            sql += ' AND r.read_type=ANY(?::text[])'
            params.append(list(types))
        # Endpoint qualification is independent of the requested output kinds.
        if not scope.get('_identity_read') and (kinds is None or set(kinds) & {'relation', 'attribute'}):
            endpoints = []
            only_kind = next(iter(kinds)) if kinds is not None and len(kinds) == 1 else None
            columns = [('s', 'subject_id')] if only_kind == 'attribute' else [('s', 'subject_id'), ('t', 'object_id')]
            for alias, field in columns:
                if entity_source is not None:
                    # Full aggregations share a scope-qualified entity-ID CTE.
                    # It has no per-row index lookup, allowing hash semi-joins
                    # for both endpoints instead of hundreds of thousands of probes.
                    endpoints.append(f'EXISTS (SELECT 1 FROM {entity_source} {alias} WHERE {alias}.id=r.read_{field})')
                    continue
                eligible, values = self._base(project, scope, alias)
                # A page must stop on its ordered outer keyset. Flattening an
                # endpoint EXISTS into a semi-join can scan/sort the entire
                # relation corpus before LIMIT, even with accurate statistics.
                # OFFSET 0 keeps this exact existence check correlated and lets
                # the current visible entity index answer each bounded lookup.
                fence = ' OFFSET 0' if keyset else ''
                endpoints.append(f"EXISTS (SELECT 1 FROM record_versions {alias} WHERE {alias}.id=r.read_{field} AND {alias}.read_kind='entity' AND {eligible}{fence})")
                params.extend(values)
            if only_kind in {'relation', 'attribute'}:
                # A known kind needs no OR guard. Direct EXISTS predicates let
                # PostgreSQL plan semi-joins for complete relation counts.
                sql += ' AND ' + ' AND '.join(endpoints)
            else:
                sql += (f" AND (r.read_kind NOT IN ('relation','attribute') OR ({endpoints[0]} AND "
                        f"(r.read_kind='attribute' OR {endpoints[1]})))")
        if query:
            if not isinstance(query, str):
                raise ValueError('搜索词必须是字符串')
            sql += (" AND (strpos(lower(COALESCE(r.payload::jsonb->>'text','')),lower(?))>0 "
                    "OR strpos(lower(r.id),lower(?))>0 OR EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                    "CASE WHEN jsonb_typeof(r.payload::jsonb#>'{metadata,aliases}')='array' "
                    "THEN r.payload::jsonb#>'{metadata,aliases}' ELSE '[]'::jsonb END) alias "
                    "WHERE strpos(lower(alias),lower(?))>0))")
            params.extend([query, query, query])
        return sql, params

    @staticmethod
    def _identity(project, scope, kinds, query):
        return hashlib.sha256(_json([project, scope, sorted(kinds) if kinds is not None else None, query]).encode()).hexdigest()

    @_snapshot_read
    def count(self, project, scope, *, kinds=None, query=''):
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope or {}, kinds=kinds, query=query)
        with self.repo._lock:
            return self.repo._db.execute('SELECT count(*) FROM record_versions r WHERE ' + sql, params).fetchone()[0]

    @_snapshot_read
    def mentioned_entities(self, project, scope, query, *, limit=8):
        """Find full entity names/aliases inside a question before bounded decode.

        Reverse containment is separate from ordinary record search: a question
        must contain the name, rather than be contained in an entity's text.
        ASCII word edges prevent ``Acme`` from matching ``AcmeCorp``. Chinese
        names retain substring matching, with deterministic longest preference.
        """
        if not isinstance(query, str):
            raise ValueError('实体命中查询必须是字符串')
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('实体命中上限必须在1到8之间')
        needle = query.strip().lower()
        if not needle:
            return []
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope or {}, kinds=['entity'])
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        statement = f"""WITH question AS (SELECT ?::text AS text), candidates AS (
            SELECT r.id,r.version,r.version_id,names.matched_name,names.match_kind,pos AS start_pos
            FROM record_versions r CROSS JOIN question q
            CROSS JOIN LATERAL (
              SELECT btrim(COALESCE(r.payload::jsonb->>'text','')) AS matched_name, 'name' AS match_kind
              UNION ALL
              SELECT btrim(alias.value#>>'{{}}'), 'alias'
              FROM jsonb_array_elements(CASE
                  WHEN jsonb_typeof(r.payload::jsonb#>'{{metadata,aliases}}')='array'
                  THEN r.payload::jsonb#>'{{metadata,aliases}}' ELSE '[]'::jsonb END) alias(value)
              WHERE jsonb_typeof(alias.value)='string'
            ) names
            CROSS JOIN LATERAL generate_series(1,char_length(q.text)-char_length(names.matched_name)+1) pos
            WHERE {sql} AND char_length(names.matched_name)>=2
              AND strpos(q.text,lower(names.matched_name))>0
              AND substr(q.text,pos,char_length(names.matched_name))=lower(names.matched_name)
              AND (left(names.matched_name,1) !~ '[A-Za-z0-9_]'
                   OR pos=1 OR substr(q.text,pos-1,1) !~ '[A-Za-z0-9_]')
              AND (right(names.matched_name,1) !~ '[A-Za-z0-9_]'
                   OR substr(q.text,pos+char_length(names.matched_name),1) !~ '[A-Za-z0-9_]')
          ), mentions AS (
            SELECT DISTINCT ON (m.version_id) m.* FROM candidates m
            WHERE NOT EXISTS (
              SELECT 1 FROM candidates longer
              WHERE char_length(longer.matched_name)>char_length(m.matched_name)
                AND longer.start_pos<=m.start_pos
                AND longer.start_pos+char_length(longer.matched_name)>=m.start_pos+char_length(m.matched_name))
            ORDER BY m.version_id,char_length(m.matched_name) DESC,m.matched_name,
                     (m.match_kind='name') DESC,m.start_pos
          ), selected AS (
            SELECT * FROM mentions ORDER BY char_length(matched_name) DESC,matched_name,id,version LIMIT ?
          ) SELECT {fields},s.matched_name,s.match_kind FROM selected s
          JOIN record_versions r ON r.project_id=? AND r.version_id=s.version_id
          ORDER BY char_length(s.matched_name) DESC,s.matched_name,s.id,s.version"""
        with self.repo._lock:
            rows = self.repo._db.execute(statement, [needle, *params, limit, project]).fetchall()
        return [{**self.repo._record(row), 'matched_name': row['matched_name'],
                 'match_kind': row['match_kind']} for row in rows]

    @_snapshot_read
    def version_manifest(self, project, scope):
        """Compact identities of fixed inputs, without decoding corpus payloads."""
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope or {})
        with self.repo._lock:
            rows = self.repo._db.execute("SELECT r.read_kind AS kind, count(*) AS total, "
                "md5(string_agg(r.version_id, ',' ORDER BY r.id,r.version)) AS version_digest "
                'FROM record_versions r WHERE ' + sql + ' GROUP BY kind ORDER BY kind', params).fetchall()
        return [dict(row) for row in rows]

    @_snapshot_read
    def version_id_batches(self, project, scope, *, kinds=None, ids=None, batch_size=1000):
        """Stream exact qualified vector identities without loading corpus text.

        Every page is ranked by Milvus; page boundaries never limit recall.
        The caller retains only its small per-channel ranking budget.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 5000:
            raise ValueError('向量候选批次必须在 1 到 5000 之间')
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope or {}, kinds=kinds, keyset=True)
        if ids is not None:
            if not isinstance(ids, (list, tuple, set)) or any(not isinstance(id, str) or not id for id in ids):
                raise ValueError('候选 ID 必须是非空字符串列表')
            if not ids:
                return
            sql += ' AND r.id=ANY(?::text[])'
            params.append(list(ids))
        after = None
        while True:
            page_sql, page_params = sql, list(params)
            if after is not None:
                page_sql += ' AND (r.id,r.version)>(?,?)'
                page_params.extend(after)
            with self.repo._lock:
                rows = self.repo._db.execute('SELECT r.id,r.version,r.version_id FROM record_versions r WHERE '
                    + page_sql + ' ORDER BY r.id,r.version LIMIT ?', [*page_params, batch_size]).fetchall()
            if not rows:
                return
            yield [row['version_id'] for row in rows]
            after = (rows[-1]['id'], rows[-1]['version'])
            if len(rows) < batch_size:
                return

    @_snapshot_read
    def page(self, project, scope, *, kinds=None, after=None, limit=1000, query='', include_total=True, compact_graph=False):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('分页上限必须在 1 到 10000 之间')
        scope = dict(scope or {})
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope, kinds=kinds, query=query, keyset=True)
        identity = self._identity(project, scope, kinds, query)
        total = self.count(project, scope, kinds=kinds, query=query) if include_total else None
        if after is not None:
            try:
                if not isinstance(after, str) or len(after) > 10000:
                    raise ValueError()
                cursor = json.loads(base64.urlsafe_b64decode(after.encode()))
                if not isinstance(cursor, list) or len(cursor) != 3 or cursor[0] != identity or not isinstance(cursor[1], str) or type(cursor[2]) is not int:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
                raise ValueError('无效或范围不匹配的分页 cursor 游标') from exc
            sql += ' AND (r.id,r.version)>(?,?)'
            params.extend(cursor[1:])
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        if compact_graph:
            fields = ("r.id,r.version,r.version_id,r.read_kind AS kind,"
                      "r.read_type AS type,r.read_text AS text,"
                      "r.read_subject_id AS subject_id,r.read_object_id AS object_id")
        with self.repo._lock:
            rows = self.repo._db.execute(f'SELECT {fields} FROM record_versions r WHERE {sql} ORDER BY r.id,r.version LIMIT ?', [*params, limit + 1]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = (base64.urlsafe_b64encode(_json([identity, rows[-1]['id'], rows[-1]['version']]).encode()).decode() if has_more else None)
        return {'records': [dict(row) if compact_graph else self.repo._record(row) for row in rows], 'next_cursor': cursor, 'total': total}

    @_snapshot_read
    def ranked_keyword(self, project, scope, query, *, kinds, limit):
        """Historical keywords are filtered/ranked/limited in SQL before decode."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError('历史关键词不能为空')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('历史关键词结果上限必须在1到100之间')
        self.repo.get_project(project)
        needle = query.casefold().strip()
        tokens = list(dict.fromkeys(re.findall(r'\w+', needle)))[:32]
        fragments = sorted({part[index:index + 2]
            for part in re.findall(r'[\u4e00-\u9fff]+', needle)
            for index in range(len(part) - 1)} - {'什么', '哪些', '如何', '是否', '怎么', '多少'})[:64]
        weights = [(needle, 10), *((token, 1) for token in tokens),
                   *((fragment, 1 / (len(fragments) + 1)) for fragment in fragments)]
        predicates, params = self._qualified(project, scope, kinds=kinds)
        score_terms, score_params = [], []
        for term, weight in weights:
            score_terms.append('(CASE WHEN strpos(haystack,?)>0 THEN ?::double precision ELSE 0 END)')
            score_params.extend((term, weight))
        score = ' + '.join(score_terms)
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        statement = f'''WITH qualified AS (
            SELECT {fields}, lower(COALESCE(r.payload::jsonb->>'text','') || ' ' ||
                COALESCE(r.payload::jsonb#>>'{{metadata,aliases}}','')) AS haystack
            FROM record_versions r WHERE {predicates}
          ), ranked AS (SELECT qualified.*, ({score}) AS keyword_score FROM qualified)
          SELECT * FROM ranked WHERE keyword_score>0
          ORDER BY keyword_score DESC,id,version LIMIT ?'''
        with self.repo._lock:
            rows = self.repo._db.execute(statement, [*params, *score_params, limit]).fetchall()
        return [{**self.repo._record(row), 'keyword_score': float(row['keyword_score']),
                 'score': float(row['keyword_score'])} for row in rows]

    @_snapshot_read
    def by_ids(self, project, scope, ids, *, kinds=None):
        if not isinstance(ids, (list, tuple, set)) or len(ids) > 5000 or any(not isinstance(id, str) or not id for id in ids):
            raise ValueError('精确 ID 读取需要最多 5000 个字符串 ID')
        self.repo.get_project(project)
        if not ids:
            return []
        sql, params = self._qualified(project, scope or {}, kinds=kinds)
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        with self.repo._lock:
            rows = self.repo._db.execute(f'SELECT {fields} FROM record_versions r WHERE {sql} AND r.id = ANY(?::text[]) ORDER BY r.id,r.version', [*params, list(ids)]).fetchall()
        return [self.repo._record(row) for row in rows]

    @_snapshot_read
    def entity_attributes(self, project, scope, entity_ids, *, limit=200):
        """Read a bounded attribute slice for already-qualified entity IDs."""
        if not isinstance(entity_ids, (list, tuple, set)) or len(entity_ids) > 10000 or any(not isinstance(id, str) or not id for id in entity_ids):
            raise ValueError('属性读取需要最多 10000 个实体 ID')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('属性读取上限必须在 1 到 1000 之间')
        if not entity_ids:
            return {'records': [], 'truncated': False}
        self.repo.get_project(project)
        sql, params = self._qualified(project, scope or {}, kinds=['attribute'])
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        with self.repo._lock:
            rows = self.repo._db.execute(f'SELECT {fields} FROM record_versions r WHERE {sql}'
                " AND r.payload::jsonb->>'subject_id'=ANY(?::text[])"
                " ORDER BY array_position(?::text[],r.payload::jsonb->>'subject_id'),r.id,r.version LIMIT ?",
                [*params, list(entity_ids), list(entity_ids), limit + 1]).fetchall()
        return {'records': [self.repo._record(row) for row in rows[:limit]], 'truncated': len(rows) > limit}

    @_snapshot_read
    def neighbors(self, project, scope, seeds, *, hops=1, max_nodes=500, max_edges=1000, direction='forward'):
        if type(hops) is not int or not 0 <= hops <= 10:
            raise ValueError('邻域跳数必须是 0 到 10 之间的整数')
        if type(max_nodes) is not int or not 1 <= max_nodes <= 10000 or type(max_edges) is not int or not 1 <= max_edges <= 10000:
            raise ValueError('无效的邻域预算')
        if direction not in {'forward', 'backward', 'both'}:
            raise ValueError('无效的邻域方向')
        def entities(ids):
            ids = list(ids)
            return [row for offset in range(0, len(ids), 5000)
                    for row in self.by_ids(project, scope, ids[offset:offset + 5000], kinds=['entity'])]
        nodes = {row['id']: row for row in entities(seeds)}
        truncated = len(nodes) > max_nodes
        nodes = dict(list(nodes.items())[:max_nodes])
        frontier, edges, paths = set(nodes), {}, {}
        starts = set(nodes)
        fields = ','.join('r.' + field for field in FIELDS.split(','))
        for _ in range(hops):
            if not frontier:
                break
            sql, params = self._qualified(project, scope or {}, kinds=['relation'], keyset=True)
            columns = (['subject_id', 'object_id'] if direction == 'both' else
                       ['subject_id' if direction == 'forward' else 'object_id'])
            predicate = ' OR '.join(f"r.payload::jsonb->>'{column}'=ANY(?::text[])" for column in columns)
            # Keep the ordered LIMIT outside adjacency selection: a generic
            # prepared plan otherwise scans every relation in key order rather
            # than using the subject/object indexes for this small frontier.
            # OFFSET 0 preserves that boundary without a materialized payload CTE.
            with self.repo._lock:
                rows = self.repo._db.execute(f"SELECT * FROM (SELECT {fields} FROM record_versions r WHERE {sql} AND ({predicate}) OFFSET 0) adjacent ORDER BY id,version LIMIT ?", [*params, *[sorted(frontier) for _ in columns], max_edges - len(edges) + 1]).fetchall()
            if len(rows) > max_edges - len(edges):
                truncated = True
            found = [self.repo._record(row) for row in rows[:max_edges - len(edges)]]
            endpoints = {row[key] for row in found for key in ('subject_id', 'object_id')} - set(nodes)
            new = entities(sorted(endpoints))
            if len(new) > max_nodes - len(nodes):
                truncated = True
            nodes.update({row['id']: row for row in new[:max_nodes - len(nodes)]})
            next_nodes = set()
            for edge in found:
                start, end = edge['subject_id'], edge['object_id']
                backward = end in frontier and start not in frontier
                if direction == 'backward' or (direction == 'both' and backward):
                    start, end = end, start
                if start not in nodes or end not in nodes:
                    truncated = True
                    continue
                edges[edge['id']] = edge
                if end not in starts and end not in paths:
                    step = {'from': start, 'to': end, 'relation_id': edge['id'], 'relation': edge.get('type') or edge['text'],
                            'direction': 'forward' if start == edge['subject_id'] else 'backward',
                            'subject_id': edge['subject_id'], 'object_id': edge['object_id'],
                            'relation_version_id': edge['version_id'], 'from_version_id': nodes[start]['version_id'], 'to_version_id': nodes[end]['version_id']}
                    paths[end] = [*paths.get(start, []), step]
                    next_nodes.add(end)
            frontier = next_nodes
        reasoning = [{'start': steps[0]['from'], 'end': steps[-1]['to'], 'steps': steps,
                      'start_label': nodes[steps[0]['from']]['text'], 'end_label': nodes[steps[-1]['to']]['text']}
                     for steps in paths.values()]
        return {'records': [*nodes.values(), *edges.values()], 'reasoning_paths': reasoning,
                'truncated': truncated,
                'continuation': ({'seeds': sorted(starts), 'hops': hops, 'direction': direction,
                                  'max_nodes': max_nodes, 'max_edges': max_edges,
                                  'reason': 'budget_exhausted'} if truncated else None)}

    @_snapshot_read
    def source_context(self, project, scope, hits, *, radius=1, max_total=12):
        from ..domain.evidence_scope import source_scope
        groups = {}
        for row in hits:
            selected = source_scope(scope, row)
            key = _json(selected.get('filters'))
            groups.setdefault(key, (selected, []))[1].append(row)
        sources, neighbors, seen = [], [], set()
        for selected, selected_hits in groups.values():
            result = self._source_context(project, selected, selected_hits,
                                          radius=radius, max_total=max_total)
            for row in result['source_chunks']:
                if row['version_id'] not in seen and len(sources) < 200:
                    sources.append(row)
                    seen.add(row['version_id'])
            for row in result['context_chunks']:
                if row['version_id'] not in seen and len(neighbors) < max_total:
                    neighbors.append(row)
                    seen.add(row['version_id'])
        return {'source_chunks': sources, 'context_chunks': neighbors}

    def _source_context(self, project, scope, hits, *, radius=1, max_total=12):
        """Batch source anchors and same-document-revision nearest neighbors."""
        if not hits:
            return {'source_chunks': [], 'context_chunks': []}
        version_ids = [row['version_id'] for row in hits if row.get('version_id')]
        anchor_ids = {row['id'] for row in hits if row.get('kind') == 'chunk'}
        anchors = self.by_ids(project, scope, sorted(anchor_ids), kinds=['chunk'])
        anchor_versions = {row['version_id']: {row['version_id']} for row in anchors}
        artifact_scope = {**scope, '_source_artifact': True}
        qualified, params = self._base(project, artifact_scope, 'r')
        # Formal facts retain source identity in assertion mappings, rather
        # than a mutable top-level source_id or a guessed ID prefix.
        statement = f"""SELECT DISTINCT {','.join('r.' + field for field in FIELDS.split(','))},
                m.record_version_id AS anchor_record_version_id
            FROM record_versions r JOIN assertions a
              ON a.project_id=r.project_id AND a.chunk_id=r.id
              AND a.document_id=r.payload::jsonb->>'source_id'
              AND a.document_version_id=r.payload::jsonb#>>'{{metadata,source_version_id}}'
            JOIN record_version_assertions m ON m.project_id=a.project_id AND m.assertion_id=a.id
            JOIN assertion_events e ON e.id=m.assertion_event_id AND e.assertion_id=a.id
            WHERE {qualified} AND r.payload::jsonb->>'kind'='chunk'
              AND m.record_version_id=ANY(?::text[])"""
        values = [*params, version_ids]
        if scope.get('known_at'):
            statement += ' AND m.created_at<=? AND a.created_at<=? AND e.created_at<=?'
            values.extend([normalize_time(scope['known_at'])] * 3)
        with self.repo._lock:
            mapped = self.repo._db.execute(statement + ' LIMIT 200', values).fetchall()
        for row in mapped:
            anchor_versions.setdefault(row['version_id'], set()).add(row['anchor_record_version_id'])
        anchors = list({row['version_id']: row for row in [*anchors, *[self.repo._record(row) for row in mapped]]}.values())
        # The source authority must still qualify; frozen old source text alone
        # cannot authorize a withdrawn or out-of-scope document.
        document_ids = {row.get('source_id') for row in anchors} - {None, ''}
        documents = {row['id'] for row in self.by_ids(project, scope, sorted(document_ids), kinds=['document'])}
        anchors = [row for row in anchors if row.get('source_id') in documents
                   and (row.get('metadata') or {}).get('source_version_id')]
        if not anchors:
            return {'source_chunks': [], 'context_chunks': []}
        direct_versions = {row.get('version_id') for row in hits}
        source_chunks = [{**row, '_source_anchor_versions': sorted(anchor_versions.get(row['version_id'], set()))}
                         for row in anchors if row['version_id'] not in direct_versions]
        fields = ','.join('c.' + field for field in FIELDS.split(','))
        eligible, eligible_params = self._base(project, artifact_scope, 'c')
        same = (f"{eligible} AND c.payload::jsonb->>'kind'='chunk' AND "
                "c.payload::jsonb->>'source_id'=a.payload::jsonb->>'source_id' AND "
                "c.payload::jsonb#>>'{metadata,source_version_id}'=a.payload::jsonb#>>'{metadata,source_version_id}' AND "
                'c.scope_identity=a.scope_identity')
        order_c = "(COALESCE((c.payload::jsonb#>>'{metadata,start_char}')::bigint,0),c.id)"
        order_a = "(COALESCE((a.payload::jsonb#>>'{metadata,start_char}')::bigint,0),a.id)"
        statement = f"""SELECT neighbors.*, a.id AS anchor_chunk_id, a.version_id AS anchor_chunk_version_id
            FROM record_versions a CROSS JOIN LATERAL (
              (SELECT {fields} FROM record_versions c WHERE {same} AND {order_c}<{order_a}
               ORDER BY {order_c} DESC LIMIT ?)
              UNION ALL
              (SELECT {fields} FROM record_versions c WHERE {same} AND {order_c}>{order_a}
               ORDER BY {order_c} LIMIT ?)
            ) neighbors WHERE a.project_id=? AND a.version_id=ANY(?::text[])
            ORDER BY a.id,neighbors.id LIMIT ?"""
        with self.repo._lock:
            rows = self.repo._db.execute(statement, [*eligible_params, radius, *eligible_params, radius,
                project, [row['version_id'] for row in anchors], max_total + len(hits)]).fetchall()
        seen = direct_versions | {row['version_id'] for row in source_chunks}
        versions_by_id = {}
        for row in anchors:
            versions_by_id.setdefault(row['id'], []).append(row['version_id'])
        context = []
        for row in rows:
            if row['version_id'] not in seen:
                anchor_version = row.get('anchor_chunk_version_id')
                # Older read adapters may omit the version column. Only a
                # unique exact anchor version can supply the same binding.
                if anchor_version is None and len(versions_by_id.get(row['anchor_chunk_id'], [])) == 1:
                    anchor_version = versions_by_id[row['anchor_chunk_id']][0]
                context.append({**self.repo._record(row), 'context_reason': 'adjacent_chunk',
                                'anchor_chunk_id': row['anchor_chunk_id'],
                                '_source_anchor_versions': sorted(anchor_versions.get(anchor_version, set()))})
                seen.add(row['version_id'])
            if len(context) >= max_total:
                break
        return {'source_chunks': source_chunks, 'context_chunks': context}
