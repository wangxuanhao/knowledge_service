"""Persisted, reproducible retrieval/QA ablations over real service execution."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import os
import threading
from time import perf_counter
from uuid import uuid4

from ..core.time import utc_now
from ..models import Question, Scope
from ..repository.scoped_reads import ScopedReads


ARTIFACT = 'retrieval-experiment'
VARIANTS = ('basic', 'document_only', 'no_graph', 'no_ontology', 'combined')
TERMINAL = {'completed', 'failed', 'cancelled', 'invalidated'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def variant_request(manifest, question, variant):
    """Toggle executed stages; governance qualification is always retained."""
    body = {**manifest['settings'], **manifest['scope'], 'query': question['query'],
            '_explicit_known_at': manifest['explicit_known_at'],
            '_explicit_valid_at': manifest['explicit_valid_at'],
            'ontology_expansion': True, 'ontology_context': True, 'graph_context': True}
    if not manifest['explicit_known_at']:
        pin = manifest.get('qa_index_snapshot')
        if not isinstance(pin, dict):
            raise ValueError('固定实验缺少冻结前的索引身份，已失效；请重新创建实验')
        body['_qa_index_snapshot'] = deepcopy(pin)
    omitted = []
    if variant in {'no_graph', 'document_only', 'combined'}:
        body.update(graph_context=False, hops=0)
        omitted.append('graph_neighbors_and_paths')
    if variant in {'no_ontology', 'document_only', 'combined'}:
        body.update(ontology_expansion=False, ontology_context=False)
        omitted.extend(['ontology_query_expansion', 'ontology_label_resolution'])
    if variant == 'document_only':
        body.update(kinds=['chunk'], k_entities=0, k_relations=0)
        omitted.append('structured_entity_relation_retrieval')
    else:
        body['kinds'] = ['entity', 'chunk']
    body['generate'] = manifest['generate'] and manifest['model']['configured']
    return body, omitted


def metrics(question, result):
    expected = set(question.get('expected_record_ids') or [])
    found = {row['id'] for row in result.get('evidence', [])}
    overlap = expected & found
    generated = result.get('mode') == 'llm'
    expected_answer = question.get('expected_answer')
    return {
        'expected_evidence_count': len(expected), 'retrieved_evidence_count': len(found),
        'matched_evidence_count': len(overlap),
        'evidence_recall': len(overlap) / len(expected) if expected else None,
        'evidence_precision': len(overlap) / len(found) if expected and found else (0. if expected else None),
        'answer_exact_match': (str(result.get('answer', '')).strip() == expected_answer.strip()
                               if generated and expected_answer else None),
        'answer_metric': 'exact_text_match_only',
    }


class RetrievalExperiments:
    def __init__(self, service, jobs=None, *, authorize=None, auth_disabled=False):
        self.service = service
        self.repo = service.repository
        self.jobs = jobs
        self.lock = threading.RLock()
        self.authorize = authorize or self._authorize
        self.auth_disabled = auth_disabled

    def _authorize(self, actor_id, *, write=False):
        if self.auth_disabled and actor_id == 'system:auth-disabled':
            return
        from ..repository.users_store import UserStore
        with self.repo._lock:
            user = UserStore(self.repo).get_by_id(actor_id)
        if user is None or not user['is_active']:
            raise PermissionError('实验账号已失效，请重新登录')
        if write and user['role'] not in {'admin', 'superadmin'}:
            raise PermissionError('当前账号没有运行实验的权限')

    def _snapshot(self, project, scope, *, versions=True):
        with self.repo.read_snapshot():
            self.repo.get_project(project)
            ontologies = self.repo.list_ontologies(project)
            publications = [{'id': row['id'], 'version': row.get('version'),
                'turtle_digest': digest(row.get('turtle', '')), 'metadata': row.get('metadata', {})}
                for row in ontologies]
            state = {'watermark': self.repo.projections.project_watermark(project),
                'knowledge_time': scope['known_at'],
                'ontology_publications': publications, 'ontology_digest': digest(publications),
                'indexes': {}}
            if versions:
                state['record_versions'] = ScopedReads(self.repo).version_manifest(project, scope)
            for target in ('milvus', 'neo4j'):
                cursor = self.repo.projections.cursor(project, target)
                state['indexes'][target] = ({key: cursor.get(key) for key in (
                    'schema_version', 'applied_seq', 'status', 'destination', 'generation')}
                    if cursor else None)
        return state

    def _runtime(self):
        from .answers import answer_messages
        configured = all(os.environ.get(key, '').strip() for key in
            ('KG_LLM_BASE_URL', 'KG_LLM_API_KEY', 'KG_LLM_MODEL'))
        return {'configured': configured, 'model': os.environ.get('KG_LLM_MODEL') or None,
                'endpoint_digest': digest(os.environ.get('KG_LLM_BASE_URL', '')),
                'prompt_digest': digest(inspect.getsource(answer_messages)),
                'sampling': {'temperature': None, 'seed': None, 'source': 'provider_default_unknown'}}

    def create(self, project, request, *, actor_id):
        self.authorize(actor_id, write=True)
        questions = request.get('questions')
        if not isinstance(questions, list) or not 1 <= len(questions) <= 100:
            raise ValueError('实验需要 1 到 100 道固定问题')
        variants = list(dict.fromkeys(request.get('variants') or VARIANTS))
        if any(variant not in VARIANTS for variant in variants):
            raise ValueError('实验包含未知变体')
        scope = Scope.model_validate(request.get('scope') or {}).model_dump()
        scope.pop('kinds', None)
        explicit_known, explicit_valid = scope.get('known_at') is not None, scope.get('valid_at') is not None
        from .answer_validity import freeze_qa_request
        frozen = freeze_qa_request(self.service, project, {
            **scope, '_explicit_known_at': explicit_known, '_explicit_valid_at': explicit_valid})
        scope['known_at'], scope['valid_at'] = frozen['known_at'], frozen['valid_at']
        defaults = Question(query='validation').model_dump()
        settings = {key: request.get(key, defaults[key]) for key in (
            'retrieval_mode', 'k', 'k_entities', 'k_chunks', 'k_relations',
            'k_per_platform', 'k_unknown', 'hops', 'include_references', 'reference_k')}
        frozen_questions = []
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError('每道问题必须包含 query')
            Question.model_validate({**scope, **settings, 'query': question.get('query')})
            expected = question.get('expected_record_ids') or []
            if not isinstance(expected, list) or len(expected) > 1000 or any(not isinstance(value, str) or not value for value in expected):
                raise ValueError('标准依据 ID 必须是最多 1000 个字符串')
            answer = question.get('expected_answer')
            if answer is not None and (not isinstance(answer, str) or len(answer) > 20000):
                raise ValueError('标准答案必须是最多 20000 字符的文本')
            frozen_questions.append({'id': str(question.get('id') or index + 1),
                'query': question['query'], 'expected_record_ids': list(dict.fromkeys(expected)),
                'expected_answer': answer})
        if len({q['id'] for q in frozen_questions}) != len(frozen_questions):
            raise ValueError('问题 ID 不能重复')
        model = self._runtime()
        manifest = {'schema_version': 1, 'scope': scope, 'settings': settings,
            'questions': frozen_questions, 'variants': variants,
            'explicit_known_at': explicit_known, 'explicit_valid_at': explicit_valid,
            'qa_index_snapshot': frozen.get('_qa_index_snapshot'),
            'input_snapshot': self._snapshot(project, scope), 'model': model,
            'encoder': {'identity': self.service.encoder.identity,
                        'semantic': self.service.encoder.semantic},
            'generate': request.get('generate', False) is True,
            'graph_budget': {'max_nodes': 500, 'max_edges': 200},
            'token_budget': None, 'token_budget_status': 'service_does_not_configure_token_limit',
            'qualification': 'temporal_source_scope_and_structure_governance_always_enforced'}
        run = {'id': str(uuid4()), 'project_id': project, 'owner_id': actor_id,
            'name': str(request.get('name') or '检索与问答消融')[:200],
            'status': 'queued', 'created_at': utc_now(), 'updated_at': utc_now(),
            'manifest': manifest, 'manifest_digest': digest(manifest), 'results': [],
            'summary': {}, 'completed_items': 0, 'total_items': len(variants) * len(questions),
            'attempts': 0, 'job_id': None}
        self.repo.save_artifact(ARTIFACT, run)
        self._enqueue(run)
        return self.get(project, run['id'], actor_id=actor_id)

    def _enqueue(self, run):
        if self.jobs is None:
            return
        job = self.jobs.enqueue('retrieval_experiment', {'run_id': run['id'],
            'actor_id': run['owner_id']}, project_id=run['project_id'])
        with self.lock:
            current = self.repo.get_artifact(ARTIFACT, run['id'])
            current['job_id'] = job['id']
            self.repo.save_artifact(ARTIFACT, current)

    def _owned(self, project, id, actor_id, *, write=False):
        self.authorize(actor_id, write=write)
        self.repo.get_project(project)
        run = self.repo.get_artifact(ARTIFACT, id)
        if run['project_id'] != project or run['owner_id'] != actor_id:
            raise PermissionError('不能读取其他账号或项目的实验')
        return run

    def _valid(self, run):
        manifest = run['manifest']
        if not manifest['explicit_known_at']:
            pin = manifest.get('qa_index_snapshot')
            if not isinstance(pin, dict) or pin.get('project_id') != run['project_id']:
                return False
            from .retrieval import PROJECTION_SCHEMA_VERSIONS
            try:
                index = self.repo.projections.search_index(
                    run['project_id'], PROJECTION_SCHEMA_VERSIONS['milvus'])
            except (RuntimeError, KeyError, ValueError):
                return False
            if any(index[key] != pin.get(key) for key in ('index_version', 'watermark')):
                return False
        expected = {key: value for key, value in run['manifest']['input_snapshot'].items()
                    if key != 'record_versions'}
        return (digest(run['manifest']) == run['manifest_digest'] and
                self._snapshot(run['project_id'], run['manifest']['scope'], versions=False) == expected)

    def _invalidate(self, run):
        with self.lock:
            run = self.repo.get_artifact(ARTIFACT, run['id'])
            run.update(status='invalidated', updated_at=utc_now(),
                error='项目知识、来源、本体或索引输入发生变化，固定实验已失效；请创建新实验')
            self.repo.save_artifact(ARTIFACT, run)
        return {'id': run['id'], 'project_id': run['project_id'], 'name': run['name'],
            'status': 'invalidated', 'manifest': None, 'results': [], 'summary': {},
            'error': run['error'], 'created_at': run['created_at'], 'updated_at': run['updated_at']}

    def get(self, project, id, *, actor_id):
        run = self._owned(project, id, actor_id)
        if run['status'] == 'invalidated' or not self._valid(run):
            return self._invalidate(run)
        return {key: value for key, value in run.items() if key not in {'owner_id', 'execution_id'}}

    def list(self, project, *, actor_id):
        self.authorize(actor_id)
        self.repo.get_project(project)
        items = []
        for run in self.repo.list_artifacts(ARTIFACT, project_id=project):
            if run['owner_id'] != actor_id:
                continue
            public = self.get(project, run['id'], actor_id=actor_id)
            items.append({key: public.get(key) for key in (
                'id', 'name', 'status', 'created_at', 'updated_at', 'completed_items', 'total_items', 'error')})
        return {'runs': items}

    def export(self, project, id, *, actor_id):
        public = self.get(project, id, actor_id=actor_id)
        if public['status'] == 'invalidated':
            raise ValueError(public['error'])
        return {'format': 'knowledge-retrieval-ablation-v1', **public}

    def cancel(self, project, id, *, actor_id):
        with self.lock:
            run = self._owned(project, id, actor_id, write=True)
            if run['status'] not in TERMINAL:
                run.update(status='cancelled', updated_at=utc_now())
                self.repo.save_artifact(ARTIFACT, run)
                if self.jobs is not None and run.get('job_id'):
                    self.jobs.control(run['job_id'], 'cancel')
        return self.get(project, id, actor_id=actor_id)

    def retry(self, project, id, *, actor_id):
        run = self._owned(project, id, actor_id, write=True)
        if run['status'] == 'invalidated' or not self._valid(run):
            self._invalidate(run)
            raise ValueError('固定实验输入已变化或失效，请创建新实验')
        if run['status'] not in {'failed', 'cancelled'}:
            raise ValueError('只有失败或取消的实验可以重试')
        with self.lock:
            run.update(status='queued', updated_at=utc_now(), error=None, execution_id=None)
            # Successful receipts remain fixed; retry only missing/failed items.
            run['results'] = [row for row in run['results'] if row['status'] == 'completed']
            run['completed_items'] = len(run['results'])
            run['summary'] = self._summary(run['results'], run['manifest']['variants'])
            self.repo.save_artifact(ARTIFACT, run)
        self._enqueue(run)
        return self.get(project, id, actor_id=actor_id)

    @staticmethod
    def _summary(results, variants):
        summary = {}
        for variant in variants:
            rows = [row for row in results if row['variant'] == variant and row['status'] == 'completed']
            evidence = [row['metrics']['evidence_recall'] for row in rows if row['metrics']['evidence_recall'] is not None]
            answers = [row['metrics']['answer_exact_match'] for row in rows if row['metrics']['answer_exact_match'] is not None]
            summary[variant] = {'completed_questions': len(rows),
                'evidence_scored_questions': len(evidence), 'answer_scored_questions': len(answers),
                'mean_evidence_recall': sum(evidence) / len(evidence) if evidence else None,
                'answer_exact_match': sum(answers) / len(answers) if answers else None,
                'mean_elapsed_ms': sum(row['elapsed_ms'] for row in rows) / len(rows) if rows else None}
        return summary

    def run_job(self, payload, project, progress, task_id):
        run = self.execute(project, payload['run_id'], actor_id=payload['actor_id'], progress=progress, task_id=task_id)
        # Generic task pages must not become a second unguarded frozen-result
        # endpoint after an experiment's sources or owner lose eligibility.
        return {key: run.get(key) for key in ('id', 'project_id', 'status', 'completed_items', 'total_items')}

    def execute(self, project, id, *, actor_id, progress=None, task_id=None):
        run = self._owned(project, id, actor_id, write=True)
        if run['status'] in TERMINAL:
            return self.get(project, id, actor_id=actor_id)
        if not self._valid(run):
            return self._invalidate(run)
        manifest = deepcopy(run['manifest'])
        # Re-execution must use the same model/encoder/prompt configuration.
        runtime = self._runtime()
        if runtime != manifest['model'] or manifest['encoder'] != {
                'identity': self.service.encoder.identity, 'semantic': self.service.encoder.semantic}:
            run.update(status='failed', error='模型、编码器或提示模板已变化，不能继续固定实验', updated_at=utc_now())
            self.repo.save_artifact(ARTIFACT, run)
            return self.get(project, id, actor_id=actor_id)
        execution = task_id or str(uuid4())
        with self.lock:
            run.update(status='running', execution_id=execution, attempts=run['attempts'] + 1, updated_at=utc_now())
            self.repo.save_artifact(ARTIFACT, run)
        completed = {(row['variant'], row['question_id']) for row in run['results'] if row['status'] == 'completed'}
        failed = False
        try:
            for variant in manifest['variants']:
                for question in manifest['questions']:
                    if (variant, question['id']) in completed:
                        continue
                    if progress:
                        progress(f"{variant} · 问题 {question['id']}", round(len(completed) / run['total_items'] * 100))
                    run = self._owned(project, id, actor_id, write=True)
                    if run['status'] != 'running' or run.get('execution_id') != execution:
                        return self.get(project, id, actor_id=actor_id)
                    if not self._valid(run):
                        return self._invalidate(run)
                    body, omitted = variant_request(manifest, question, variant)
                    started = perf_counter()
                    try:
                        result = self.service.answer(project, body)
                        receipt = {'variant': variant, 'question_id': question['id'], 'status': 'completed',
                            'request': body, 'omitted_stages': omitted, 'result': result,
                            'metrics': metrics(question, result),
                            'model_requested': manifest['generate'],
                            'model_available': manifest['model']['configured'],
                            'generation_skip_reason': ('model_unavailable' if manifest['generate'] and not manifest['model']['configured']
                                                       else 'not_requested' if not manifest['generate'] else None)}
                    except Exception as exc:
                        failed = True
                        receipt = {'variant': variant, 'question_id': question['id'], 'status': 'failed',
                            'request': body, 'omitted_stages': omitted,
                            'error': '实际检索或问答执行失败；请检查 Milvus、模型和所选范围，未生成替代答案',
                            'error_type': type(exc).__name__,
                            'error_category': ('milvus_unavailable' if 'Milvus' in str(exc)
                                               else 'scope_unsupported' if isinstance(exc, ValueError)
                                               else 'execution_failed'),
                            'result': None, 'metrics': None}
                    receipt['elapsed_ms'] = round((perf_counter() - started) * 1000, 2)
                    with self.lock:
                        current = self._owned(project, id, actor_id, write=True)
                        if current['status'] != 'running' or current.get('execution_id') != execution:
                            return self.get(project, id, actor_id=actor_id)
                        if not self._valid(current):
                            return self._invalidate(current)
                        current['results'] = [row for row in current['results']
                            if (row['variant'], row['question_id']) != (variant, question['id'])] + [receipt]
                        current['completed_items'] = len(current['results'])
                        current['summary'] = self._summary(current['results'], manifest['variants'])
                        current['updated_at'] = utc_now()
                        self.repo.save_artifact(ARTIFACT, current)
                        completed.add((variant, question['id']))
            with self.lock:
                run = self._owned(project, id, actor_id, write=True)
                if run['status'] == 'running' and run.get('execution_id') == execution:
                    run.update(status='failed' if failed else 'completed', updated_at=utc_now())
                    self.repo.save_artifact(ARTIFACT, run)
        except Exception:
            with self.lock:
                run = self.repo.get_artifact(ARTIFACT, id)
                if run['status'] == 'running' and run.get('execution_id') == execution:
                    run.update(status='failed', error='实验在资格核对或任务检查点停止，未继续执行', updated_at=utc_now())
                    self.repo.save_artifact(ARTIFACT, run)
            raise
        return self.get(project, id, actor_id=actor_id)
