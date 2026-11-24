"""Durable, scope-specific graph communities with browseable snapshots."""
from collections import Counter
import json
import logging
import re
from threading import Event, Thread
from uuid import uuid4

from ..core.time import utc_now
from ..domain.applicability import normalize_dimension
from ..repository.community_store import ACTIVE_JOB_STATES, CommunityStore
from ..utils.diagnostics import redact
from . import community_reports
from .community_partition import ALGORITHM
from .community_search import qualify_sources, report_manifest, vector_reports
from .community_snapshot import assemble, capture_evidence, digest
from .retrieval import cached_query_vector


LOG = logging.getLogger(__name__)
JOB_KIND = 'community_build'
_STOPPED = {'failed', 'cancelled', 'interrupted'}
_QUESTION_COMMUNITY_LIMIT = 8
_GENERATED_EVIDENCE_LIMIT = 24
_EXCERPT_EVIDENCE_LIMIT = 8


class CommunitySnapshotStale(RuntimeError):
    pass


def _require_revision(status, expected_revision):
    if expected_revision is not None and status['revision'] != expected_revision:
        raise CommunitySnapshotStale('社区快照版本已更新，请重新选择社区。')


def platform_name(value):
    return normalize_dimension({'mode': 'specific', 'values': [value]}, 'platforms')['values'][0]


def scope_key(scope):
    return 'project' if scope['kind'] == 'project' else 'platform:' + scope['platform']


def read_scope(scope):
    if scope['kind'] == 'project':
        return {}
    return {'applicability': {'platforms': {'mode': 'specific', 'values': [scope['platform']]}}}


def revision(scope, graph):
    return digest([ALGORITHM, scope, graph['records_digest'], graph['supports_digest']])


def _question_terms(question):
    words = re.findall(r'[\u3400-\u9fff]+|[^\W_]+', question.casefold())
    terms = [question.casefold()]
    for word in words:
        if len(word) > 1:
            if '\u3400' <= word[0] <= '\u9fff' and len(word) > 2:
                terms.extend(word[i:i + 2] for i in range(len(word) - 1))
            else:
                terms.append(word)
    return list(dict.fromkeys(terms))[:48]


def _public_job(job):
    return ({key: job.get(key) for key in ('id', 'kind', 'status', 'progress', 'stage', 'error')}
            if job else None)


def _empty_answer(status, selected_count, answer):
    return {'answer': answer, 'communities': [], 'evidence': [], 'citations': [],
            'generated': False, 'generation_error': None, 'status': status,
            'coverage': {'mode': 'selected' if selected_count else 'scope',
                         'retrieval': None,
                         'scope_communities': status['counts']['communities'],
                         'selected_communities': selected_count,
                         'matched_communities': 0, 'included_communities': 0,
                         'reports_ready': 0, 'reports_included': 0,
                         'evidence_available': 0, 'evidence_returned': 0,
                         'complete': False}}


class Communities:
    @classmethod
    def for_service(cls, service, jobs):
        return cls(service.repository, jobs, service.llm_models, search_service=service)

    def __init__(self, repository, jobs, llm_models, *, search_service=None):
        self.repo = repository
        self.jobs = jobs
        self.llm_models = llm_models
        self.search_service = search_service
        self.store = CommunityStore(repository)

    def _vectors(self):
        service = self.search_service
        if not service or not getattr(service.encoder, 'semantic', False):
            return None, None
        store = service.milvus_store
        return service.encoder, (getattr(store, 'community_vectors', None) if getattr(store, 'available', False) else None)

    def _vector_status(self, row, reports_ready):
        encoder, vectors = self._vectors()
        value = json.loads(row['vector_snapshot']) if row and row['vector_snapshot'] else {}
        available = encoder is not None and vectors is not None
        enabled = bool(row and row['vector_enabled'])
        current = (bool(value) and value.get('revision') == row['revision']
                   and row['revision'] == row['live_revision'] and row['observed_generation'] >= row['generation']
                   and value.get('model') == getattr(encoder, 'identity', None))
        state = ('unavailable' if not available else 'disabled' if not enabled else
                 'failed' if current and value.get('error') else
                 'ready' if current and value.get('count') == reports_ready and reports_ready else 'stale')
        return {'state': state, 'available': available, 'enabled': enabled,
                'indexed': value.get('count', 0) if current and not value.get('error') else 0,
                'model': value.get('model') if current else None,
                'error': value.get('error') if current else None}

    def _status(self, store, project, scope):
        key = scope_key(scope)
        row = store.scope(project, key)
        job = store.job(row)
        counts = dict.fromkeys(('communities', 'entities', 'covered_entities', 'isolated_entities',
                                'relationships', 'evidence',
                                'reports_ready', 'reports_failed', 'reports_pending'), 0)
        result = {
            'scope': scope, 'state': 'disabled', 'enabled': row is not None,
            'algorithm': ALGORITHM, 'revision': None, 'live_revision': None, 'built_at': None,
            'stale': False, 'change_pending': False, 'browseable': False,
            'counts': counts, 'job': _public_job(job),
            'levels': [], 'vector_index': self._vector_status(row, 0),
        }
        if row is None:
            return result
        result.update(revision=row['revision'], live_revision=row['live_revision'],
                      built_at=row['built_at'],
                      stale=row['revision'] is not None and row['revision'] != row['live_revision'],
                      change_pending=row['observed_generation'] < row['generation'])
        result['browseable'] = bool(row['revision']) and not (result['stale'] or result['change_pending'])
        if row['counts']:
            counts.update(json.loads(row['counts']))
            counts.update({f'reports_{state}': total for state, total in store.counts(project, key).items()})
            result['levels'] = store.levels(project, key)
            result['vector_index'] = self._vector_status(row, counts['reports_ready'])
        state = job['status'] if job else None
        if state in ACTIVE_JOB_STATES:
            result['state'] = 'building' if state == 'running' else 'queued'
        elif state in _STOPPED:
            result['state'] = 'failed'
        elif result['stale'] or (row['revision'] and result['change_pending']):
            result['state'] = 'stale'
        elif counts['reports_failed'] or counts['reports_pending']:
            result['state'] = 'partial'
        else:
            result['state'] = 'ready' if row['revision'] else 'failed'
        return result

    def status(self, project, scope):
        with self.repo.independent_read_view() as reader:
            return self._status(CommunityStore(reader), project, scope)

    def list(self, project, scope, *, keyword, anchor, limit, offset, mode='keyword'):
        if mode == 'semantic' and keyword:
            return self._semantic_list(project, scope, keyword, anchor, limit, offset)
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            status = self._status(store, project, scope)
            if not status['browseable']:
                return {'communities': [], 'total': 0, 'status': status}
            rows, total = store.list(project, scope_key(scope), keyword=keyword, anchor=anchor,
                                     limit=limit, offset=offset)
        return {'communities': rows, 'total': total, 'status': status}

    def _semantic_list(self, project, scope, query, anchor, limit, offset):
        key = scope_key(scope)
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            status = self._status(store, project, scope)
            if not status['browseable'] or status['vector_index']['state'] != 'ready':
                raise CommunitySnapshotStale('当前范围的报告向量未就绪，请在社区索引中构建或更新。')
            snapshot = store.scope(project, key)['vector_snapshot']
            manifest = report_manifest(vector_reports(store.ready_reports(project, key)))
            candidates = store.member_communities(project, key, anchor) if anchor else None

        scores = self._semantic_matches(project, key, status['revision'], json.loads(snapshot),
                                        manifest, query, candidates=candidates)
        ids = list(scores)[offset:offset + limit]
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            latest = self._status(store, project, scope)
            row = store.scope(project, key)
            if (not latest['browseable'] or latest['revision'] != status['revision']
                    or latest['vector_index']['state'] != 'ready' or row['vector_snapshot'] != snapshot):
                raise CommunitySnapshotStale('检索期间社区报告或向量已更新，请重新检索。')
            rows = store.summaries(project, key, ids)
        return {'communities': [{**rows[ident], 'semantic_score': scores[ident]} for ident in ids],
                'total': len(scores), 'status': latest}

    def detail(self, project, scope, community_id, *, limit, offset, expected_revision=None):
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            status = self._status(store, project, scope)
            _require_revision(status, expected_revision)
            if not status['browseable'] and status['revision']:
                raise CommunitySnapshotStale('图谱范围正在核对或刷新，请刷新社区列表后重试。')
            row = store.detail(project, scope_key(scope), community_id)
        content = row.pop('content')
        for field in ('members', 'relationships', 'evidence'):
            row[field] = content[field][offset:offset + limit]
        # Report citations remain usable even when the full evidence table is paginated.
        cited = {ref for finding in row['report'].get('findings', []) for ref in finding['citations']}
        return {**row, 'status': status, 'revision': status['revision'],
                'report_evidence': [item for item in content['evidence'] if item['id'] in cited],
                'detail_offset': offset, 'detail_limit': limit}

    def _schedule(self, project, key, scope, *, retry_failed, automatic):
        job = {
            'id': str(uuid4()), 'kind': JOB_KIND, 'project_id': project,
            'payload': {'scope': scope, 'retry_failed': retry_failed},
            'status': 'queued', 'progress': 0, 'attempts': 0,
            'logs': ['已排队等待自动刷新' if automatic else '已排队等待构建知识社区'],
            'created_at': utc_now(),
        }
        self.store.set_job(project, key, job)
        return job

    def build(self, project, scope, *, retry_failed, vectorize=False):
        key = scope_key(scope)
        if vectorize and self._vectors()[1] is None:
            raise CommunitySnapshotStale('社区报告向量化需要配置语义编码模型并连接 Milvus。')
        with self.repo.unit_of_work():
            self.store.enable(project, key, scope)
            row = self.store.scope(project, key, lock=True)
            job = self.store.job(row)
            if vectorize:
                if job and job['status'] in ACTIVE_JOB_STATES:
                    raise CommunitySnapshotStale('当前范围正在构建，请在完成后更新报告向量。')
                self.store.enable_vectors(project, key)
            if not job or job['status'] not in ACTIVE_JOB_STATES:
                pending_change = row['generation'] > row['observed_generation']
                reports = self.store.counts(project, key) if row['revision'] else {}
                needs_build = (not row['revision'] or pending_change or
                               row['revision'] != row['live_revision'] or reports.get('pending'))
                retry_needed = retry_failed and (reports.get('failed') or
                                                 (job and job['status'] in _STOPPED))
                if needs_build or retry_needed or vectorize:
                    job = self._schedule(project, key, scope, retry_failed=retry_failed, automatic=False)
        self.jobs.start()
        return {'status': self.status(project, scope), 'job': _public_job(job)}

    def refresh(self):
        with self.repo.independent_read_view() as reader:
            due = CommunityStore(reader).due()
        for candidate in due:
            project, key, scope = candidate['project_id'], candidate['scope_key'], json.loads(candidate['scope'])
            with self.repo.independent_read_view() as reader:
                graph = CommunityStore(reader).approved_graph(project, read_scope(scope))
            live = revision(scope, graph)
            with self.repo.unit_of_work():
                try:
                    row = self.store.scope(project, key, lock=True)
                except KeyError:
                    continue
                if row is None or graph['generation'] <= row['observed_generation']:
                    continue
                job = self.store.job(row)
                if job and job['status'] in ACTIVE_JOB_STATES:
                    continue
                self.store.mark_observed(project, key, graph['generation'], live)
                if row['revision'] != live:
                    self._schedule(project, key, scope, retry_failed=False, automatic=True)
        self.jobs.start()

    def _current_job(self, project, key, task_id):
        row = self.store.scope(project, key, lock=True)
        return row if row and row['job_id'] == task_id else None

    def run_job(self, payload, project, progress, task_id):
        scope = payload['scope']
        key = scope_key(scope)
        progress('正在读取已采纳的图谱与来源', 10)
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            current = store.scope(project, key)
            if not current or current['job_id'] != task_id:
                return {'superseded': True}
            snapshot = store.approved_graph(project, read_scope(scope), include_records=True)
            live = revision(scope, snapshot)
            if current['revision'] != live:
                evidence = capture_evidence(reader, project, snapshot['records'], snapshot['supports'])
                old = store.reusable_reports(project, key)
        if current['revision'] != live:
            progress('正在分组并保存可浏览的社区成员', 24)
            communities, counts = assemble(project, key, snapshot['records'], evidence, old)
        with self.repo.unit_of_work():
            if self._current_job(project, key, task_id) is None:
                return {'superseded': True}
            progress('已整理当前图谱快照', 35)
            self.store.mark_observed(project, key, snapshot['generation'], live)
            if current['revision'] != live:
                self.store.replace(project, key, live, ALGORITHM, counts, communities)
        result = self._write_reports(project, key, live, task_id, payload['retry_failed'], progress)
        if not result.get('superseded'):
            self._index_vectors(project, key, live, task_id, progress)
        return result

    def _index_vectors(self, project, key, live, task_id, progress):
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            row = store.scope(project, key)
            if not row or row['job_id'] != task_id or not row['vector_enabled']:
                return
            prepared = vector_reports(store.ready_reports(project, key))
        encoder, vectors = self._vectors()
        model = getattr(encoder, 'identity', None)
        fence = (live, model)
        with self.repo.unit_of_work():
            row = self._current_job(project, key, task_id)
            if row is None or row['revision'] != live:
                return
            prior = json.loads(row['vector_snapshot']) if row['vector_snapshot'] else {}
            attempts = {tuple(item) for item in prior.get('attempts', [])}
            if prior.get('revision') and prior.get('model'):
                attempts.add((prior['revision'], prior['model']))
            if model:
                attempts.add(fence)
            # Persist the fence before remote writes, including jobs that exit mid-batch.
            self.store.write_vector_snapshot(project, key, {**prior, 'attempts': sorted(attempts)})
        snapshot = {'revision': live, 'model': model, 'count': len(prepared),
                    'manifest_digest': digest(report_manifest(prepared)), 'attempts': sorted(attempts)}
        try:
            if vectors is None:
                raise RuntimeError('语义编码模型或 Milvus 不可用。')
            progress('正在向量化社区报告', 97)
            for start in range(0, len(prepared), 32):
                batch = prepared[start:start + 32]
                embeddings = encoder.encode([item['text'] for item in batch])
                rows = [{'id': item['id'], 'fingerprint': item['fingerprint'], 'embedding': vector}
                        for item, vector in zip(batch, embeddings, strict=True)]
                vectors.upsert(project, key, live, model, rows)
        except Exception as exc:
            snapshot.update(error=redact(exc)[:600], count=0)
        with self.repo.unit_of_work():
            row = self._current_job(project, key, task_id)
            if row is None or row['revision'] != live:
                return
            self.store.write_vector_snapshot(project, key, snapshot)
        if vectors is not None and not snapshot.get('error'):
            remaining = set()
            for old_revision, old_model in attempts - {fence}:
                try:
                    vectors.discard(project, key, old_revision, old_model)
                except Exception as exc:
                    remaining.add((old_revision, old_model))
                    LOG.warning('清理旧社区向量版本失败 %s', redact(exc))
            with self.repo.unit_of_work():
                row = self._current_job(project, key, task_id)
                if row is not None and row['revision'] == live:
                    self.store.write_vector_snapshot(project, key, {**snapshot, 'attempts': sorted(remaining)})

    def _write_reports(self, project, key, live, task_id, retry_failed, progress):
        with self.repo.independent_read_view() as reader:
            rows = CommunityStore(reader).reports_to_build(project, key, retry_failed)
        config = config_error = None
        if rows:
            try:
                config = self.llm_models.pin('job:' + task_id)
            except Exception as exc:
                config_error = redact(exc)
        outcomes = Counter()
        for index, (ident, title, content) in enumerate(rows):
            percent = 37 + (60 * index // len(rows))
            progress(f'正在生成社区报告 {index + 1}/{len(rows)}', percent)
            try:
                if config_error:
                    raise RuntimeError(config_error)
                report = community_reports.build_report(title, content, config)
            except Exception as exc:
                report = {'status': 'failed', 'error': redact(exc)[:600]}
            with self.repo.unit_of_work():
                if self._current_job(project, key, task_id) is None:
                    return {'superseded': True}
                progress(f'已处理社区报告 {index + 1}/{len(rows)}', percent)
                self.store.write_report(project, key, ident, report)
            outcomes[report['status']] += 1
        return {'revision': live, 'reports_ready': outcomes['ready'], 'reports_failed': outcomes['failed']}

    def _semantic_matches(self, project, key, revision_id, snapshot, manifest, question, *, candidates=None):
        encoder, vectors = self._vectors()
        if vectors is None or snapshot.get('model') != encoder.identity:
            raise CommunitySnapshotStale('社区向量索引与当前编码模型不一致，请更新报告向量。')
        if snapshot.get('manifest_digest') != digest(manifest):
            raise CommunitySnapshotStale('社区报告已更新，请更新报告向量。')
        expected = manifest if candidates is None else {
            ident: fingerprint for ident, fingerprint in manifest.items() if ident in candidates}
        if not expected:
            return {}
        try:
            hits = vectors.search(project, key, revision_id, encoder.identity,
                                  cached_query_vector(encoder, question), _QUESTION_COMMUNITY_LIMIT,
                                  expected=expected)
        except Exception as exc:
            raise CommunitySnapshotStale('社区语义检索失败：' + redact(exc)[:300]) from exc
        if not hits:
            raise CommunitySnapshotStale('Milvus 中缺少当前社区向量，请更新报告向量。')
        return {hit['id']: hit['score'] for hit in hits
                if expected.get(hit['id']) == hit['fingerprint'] and hit['score'] >= .35}

    def _advanced_ask(self, project, scope, question, *, mode, level, generate, expected_revision):
        key = scope_key(scope)
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            status = self._status(store, project, scope)
            _require_revision(status, expected_revision)
            if not status['browseable']:
                raise CommunitySnapshotStale('当前社区快照不可用，请等待构建或刷新。')
            if mode == 'global':
                selected = next((item for item in status['levels'] if item['level'] == level), None)
                if selected is None:
                    raise CommunitySnapshotStale('该社区层级已变化，请重新选择。')
                target = selected['communities']
            else:
                if status['vector_index']['state'] != 'ready':
                    raise CommunitySnapshotStale('当前范围的报告向量未就绪，请在社区构建中更新。')
                target = status['counts']['communities']
                snapshot = json.loads(store.scope(project, key)['vector_snapshot'])
            reports = store.ready_reports(project, key, level=level)
        fingerprints = report_manifest(vector_reports(reports))
        scores = self._semantic_matches(project, key, status['revision'], snapshot, fingerprints, question) if mode == 'semantic' else {}
        ids = list(scores) if mode == 'semantic' else list(fingerprints)
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            latest = self._status(store, project, scope)
            if not latest['browseable'] or latest['revision'] != status['revision']:
                raise CommunitySnapshotStale('检索期间社区快照已更新，请重新提问。')
            details = store.answer_details(project, key, ids, cited_only=True)
            if report_manifest(vector_reports(details)) != {ident: fingerprints[ident] for ident in ids}:
                raise CommunitySnapshotStale('检索期间社区报告已更新，请重新提问。')
            references = {item['id']: item for detail in details for item in detail['evidence']}
            qualified, rule, as_of = qualify_sources(reader, project, read_scope(scope), question, references.values())

        result = _empty_answer(status, 0, '没有找到可用于本次回答的已核验原文证据。')
        if mode == 'semantic' and not ids:
            result['answer'] = '当前范围没有达到语义相似度门槛的社区报告。'
        coverage = result['coverage']
        eligible_count = sum(any(all(ref in qualified for ref in finding['citations'])
                                 for finding in detail['report']['findings']) for detail in details)
        coverage.update(mode=mode, retrieval=mode, level=level, target_communities=target,
                        matched_communities=len(details), included_communities=len(details),
                        preview_communities=min(len(details), 24), reports_ready=len(reports),
                        reports_with_eligible_evidence=eligible_count, reports_mapped=0,
                        evidence_available=len(qualified))
        result.update(as_of=as_of, answer_purpose='rule_judgment' if rule else 'document_explanation',
                      communities=[{**{k: v for k, v in detail.items() if k not in ('report', 'evidence')},
                                    **({'semantic_score': scores[detail['id']]} if mode == 'semantic' else {})}
                                   for detail in details[:24]], evidence=list(qualified.values())[:_EXCERPT_EVIDENCE_LIMIT])
        if generate and eligible_count:
            try:
                config = self.llm_models.resolve()
                answer, citations, metrics = community_reports.generate_global_answer(
                    question, details, qualified, config, rule_judgment=rule)
                result.update(answer=answer, citations=citations, generated=True,
                              llm=config.public_identity(), evidence=[qualified[ref] for ref in citations])
                coverage.update(metrics, reports_included=metrics['reports_mapped'],
                                complete=mode == 'global' and metrics['reports_mapped'] == target)
            except Exception as exc:
                result['generation_error'] = redact(exc)[:600]
        coverage['evidence_returned'] = len(result['evidence'])
        latest = self.status(project, scope)
        if not latest['browseable'] or latest['revision'] != status['revision']:
            raise CommunitySnapshotStale('回答期间社区快照已更新，请重新提问。')
        return result

    def ask(self, project, scope, community_ids, question, *, generate, expected_revision=None,
            mode='keyword', level=None, match_only=False):
        if mode != 'keyword':
            return self._advanced_ask(project, scope, question, mode=mode, level=level,
                                      generate=generate, expected_revision=expected_revision)
        key = scope_key(scope)
        terms = _question_terms(question)
        selected = list(dict.fromkeys(community_ids))
        with self.repo.independent_read_view() as reader:
            store = CommunityStore(reader)
            status = self._status(store, project, scope)
            _require_revision(status, expected_revision)
            if not status['browseable']:
                blocked = ('图谱范围正在核对或刷新，请稍后重试。' if status['revision']
                           else '所选范围尚无可用的知识社区。')
                return _empty_answer(status, len(selected), blocked)
            ids, matched = ((selected, len(selected)) if selected else
                            store.candidates(project, key, terms, limit=_QUESTION_COMMUNITY_LIMIT))
            retrieval = 'selected' if selected else 'keyword'
            if not ids:
                if match_only:
                    result = _empty_answer(status, 0, '当前范围没有与问题匹配的社区报告。')
                    result['coverage']['retrieval'] = 'keyword'
                    return result
                retrieval = 'representative'
                ids = store.representative_communities(project, key, limit=_QUESTION_COMMUNITY_LIMIT)
            details = store.answer_details(project, key, ids, keyword_terms=terms if retrieval == 'keyword' else ())
        communities = []
        evidence_by_id = {}
        relevant_reports = []
        for row in details:
            report = row.pop('report')
            evidence_by_id.update((item['id'], item) for item in row.pop('evidence'))
            if report['status'] == 'ready':
                relevant_reports.append({**row, 'report': report})
            communities.append(row)

        def score(item):
            text = (item['title'] + ' ' + item['quote']).casefold()
            return sum(term in text for term in terms), item['kind'] == 'document'

        included_reports = relevant_reports[:_QUESTION_COMMUNITY_LIMIT]
        cited = {ref for row in included_reports for finding in row['report']['findings']
                 for ref in finding['citations']}
        ranked = sorted(evidence_by_id.values(), key=score, reverse=True)
        if generate and relevant_reports:
            ranked.sort(key=lambda item: item['id'] in cited, reverse=True)
        evidence = ranked[:_GENERATED_EVIDENCE_LIMIT if generate else _EXCERPT_EVIDENCE_LIMIT]
        result = _empty_answer(status, len(selected), '所选范围暂无与问题匹配的社区证据。')
        result.update(communities=communities, evidence=evidence)
        coverage = result['coverage']
        coverage.update(retrieval=retrieval, matched_communities=matched, included_communities=len(communities),
                        reports_ready=len(relevant_reports), evidence_available=len(ranked),
                        evidence_returned=len(evidence),
                        complete=(len(ids) == (len(selected) or coverage['scope_communities'])
                                  and len(ranked) == len(evidence)))
        if generate and evidence and included_reports:
            evidence_ids = {item['id'] for item in evidence}
            included_reports = [row for row in included_reports
                                if any(ref in evidence_ids for finding in row['report']['findings']
                                       for ref in finding['citations'])]
            coverage['reports_included'] = len(included_reports)
            coverage['complete'] &= len(included_reports) == len(communities)
            try:
                config = self.llm_models.resolve()
                answer, citations = community_reports.generate_answer(question, included_reports, evidence, config)
                result.update(answer=answer, citations=citations, generated=True, llm=config.public_identity())
            except Exception as exc:
                result['generation_error'] = redact(exc)[:600]
                coverage['complete'] = False
        elif generate:
            result['generation_error'] = '所选社区暂无可用于综合回答的社区报告。'
            coverage['complete'] = False
        if evidence and not result['generated']:
            snippets = evidence[:5]
            result['citations'] = [item['id'] for item in snippets]
            result['answer'] = '\n'.join(f"• {item['quote'][:260]} [{item['id']}]" for item in snippets)
            if retrieval == 'representative':
                result['answer'] = '所选范围的代表性社区证据摘录\n' + result['answer']
        latest = self.status(project, scope)
        _require_revision(latest, expected_revision)
        if not latest['browseable'] or latest['revision'] != status['revision']:
            return _empty_answer(latest, len(selected), '回答期间图谱范围有更新，请重新提问。')
        return result


class CommunityRefresh:
    def __init__(self, communities):
        self.communities = communities
        self.stop = Event()
        self.worker = None

    def start(self):
        if self.worker is None:
            self.worker = Thread(target=self._run, name='knowledge-community-refresh', daemon=True)
            self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker is not None:
            self.worker.join(timeout=1)

    def refresh(self):
        return self.communities.refresh()

    def _run(self):
        while not self.stop.wait(1):
            try:
                self.refresh()
            except Exception as exc:
                if not self.stop.is_set():
                    LOG.warning('知识社区刷新检查失败 %s', redact(exc))
