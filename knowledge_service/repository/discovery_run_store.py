"""Insert-only discovery-run snapshots stored in the shared artifacts table."""

from __future__ import annotations

import json
import sqlite3

from ..core.time import utc_now


KIND = 'ontology_discovery_run'
RUN_STATUSES = frozenset({
    'diagnosed_no_change', 'ready_to_finalize', 'draft_created', 'published',
    'finalized_no_change', 'stale_base', 'stale_source', 'closed',
})
INITIAL_RUN_STATUSES = frozenset({
    'diagnosed_no_change', 'ready_to_finalize', 'draft_created',
})
ALLOWED_TRANSITIONS = {
    'ready_to_finalize': frozenset({
        'finalized_no_change', 'stale_base', 'stale_source', 'closed'}),
    'draft_created': frozenset({
        'published', 'stale_base', 'stale_source', 'closed'}),
}
MUTABLE_FIELDS = frozenset({
    'status', 'candidate_outcomes', 'created_at', 'updated_at',
})


def _canonical_json(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise ValueError('discovery run must contain finite JSON values') from exc


def _immutable_snapshot(run):
    return {key: value for key, value in run.items() if key not in MUTABLE_FIELDS}


def _is_reusable_binding(binding):
    return bool(
        isinstance(binding, dict)
        and binding.get('candidate_id')
        and (binding.get('binding_kind') in {'existing', 'mapping_only'}
             or binding.get('status') == 'existing'
             or binding.get('mapping_only') is True))


def _is_actionable_binding(binding):
    return bool(
        _is_reusable_binding(binding)
        or (isinstance(binding, dict)
            and binding.get('candidate_id')
            and binding.get('binding_kind') == 'proposed'
            and (binding.get('target_iri')
                 or binding.get('required_operation_ids'))))


def discovery_result_kind(run):
    """Return the immutable creation result independently of lifecycle status."""
    if run.get('unified_draft_id'):
        return 'draft'
    if any(_is_reusable_binding(binding)
           for binding in run.get('candidate_bindings', ())):
        return 'mapping_only'
    return 'diagnosed_no_change'


def _merge_candidate_outcomes(current, terminal, initial, *, run_id):
    merged = [json.loads(_canonical_json(outcome)) for outcome in current]
    positions = {
        outcome.get('candidate_id'): index for index, outcome in enumerate(merged)
        if isinstance(outcome, dict) and outcome.get('candidate_id') is not None
    }
    protected = {}
    for outcome in initial:
        normalized = json.loads(_canonical_json(outcome))
        if not isinstance(normalized, dict):
            continue
        candidate_id = normalized.get('candidate_id')
        if candidate_id:
            protected[candidate_id] = normalized
    for candidate_id, outcome in protected.items():
        current_outcomes = [
            current_outcome for current_outcome in merged
            if isinstance(current_outcome, dict)
            and current_outcome.get('candidate_id') == candidate_id]
        if current_outcomes != [outcome]:
            raise DiscoveryRunConflict(
                'immutable discovery outcome cannot change', run_id=run_id)
    for outcome in terminal:
        if not isinstance(outcome, dict) or not outcome.get('candidate_id'):
            raise ValueError(
                'discovery run outcomes require a candidate_id')
        normalized = json.loads(_canonical_json(outcome))
        candidate_id = normalized['candidate_id']
        protected_outcome = protected.get(candidate_id)
        if protected_outcome is not None and normalized != protected_outcome:
            raise DiscoveryRunConflict(
                'immutable discovery outcome cannot change', run_id=run_id)
        position = positions.get(candidate_id)
        if position is None:
            positions[candidate_id] = len(merged)
            merged.append(normalized)
            continue
        merged[position] = normalized
    return merged


class DiscoveryRunConflict(ValueError):
    """A deterministic run key was reused or its lifecycle CAS lost."""

    code = 'discovery_run_conflict'

    def __init__(self, message, *, run_id=None, expected_status=None,
                 current_status=None):
        self.run_id = run_id
        self.expected_status = expected_status
        self.current_status = current_status
        self.details = {
            key: value for key, value in {
                'run_id': run_id,
                'expected_status': expected_status,
                'current_status': current_status,
            }.items() if value is not None
        }
        super().__init__(message)


class DiscoveryRunStore:
    """Narrow immutable/CAS boundary for ``ontology_discovery_run`` rows."""

    def __init__(self, repository):
        self.repo = repository
        self._db = repository._db

    @staticmethod
    def _validate(project_id, item):
        if not isinstance(item, dict):
            raise ValueError('discovery run must be an object')
        run = json.loads(_canonical_json(item))
        if not isinstance(run.get('id'), str) or not run['id']:
            raise ValueError('discovery run id must be a non-empty string')
        if run.get('project_id') != project_id:
            raise ValueError('discovery run does not belong to this project')
        if not isinstance(run.get('source_fingerprint'), str) or not run[
                'source_fingerprint']:
            raise ValueError('discovery run source_fingerprint is required')
        if run.get('status') not in RUN_STATUSES:
            raise ValueError('unsupported discovery run status')
        if run['status'] not in INITIAL_RUN_STATUSES:
            raise ValueError('unsupported initial discovery run status')
        has_current_outcomes = 'candidate_outcomes' in run
        for field, default, expected_type in (
                ('candidate_snapshot', [], list),
                ('accepted_candidate_ids', [], list),
                ('merged_groups', [], list),
                ('conflicts', [], list),
                ('mappings', {}, dict),
                ('candidate_bindings', [], list),
                ('initial_candidate_outcomes', [], list),
                ('candidate_outcomes', [], list),
                ('diagnostics', {}, dict)):
            run.setdefault(field, default)
            if not isinstance(run[field], expected_type):
                raise ValueError(f'discovery run {field} has an invalid shape')
        if not has_current_outcomes:
            run['candidate_outcomes'] = json.loads(_canonical_json(
                run['initial_candidate_outcomes']))
        initial_by_candidate = {}
        for outcome in run['initial_candidate_outcomes']:
            if not isinstance(outcome, dict) or not outcome.get('candidate_id'):
                raise ValueError(
                    'discovery run initial candidate outcome requires a candidate_id')
            candidate_id = outcome['candidate_id']
            if candidate_id in initial_by_candidate:
                raise ValueError(
                    'discovery run initial candidate outcomes require unique candidate_ids')
            initial_by_candidate[candidate_id] = outcome
        current_by_candidate = {}
        for outcome in run['candidate_outcomes']:
            if not isinstance(outcome, dict) or not outcome.get('candidate_id'):
                raise ValueError(
                    'discovery run candidate outcome requires a candidate_id')
            candidate_id = outcome['candidate_id']
            if candidate_id in current_by_candidate:
                raise ValueError(
                    'discovery run candidate outcomes require unique candidate_ids')
            current_by_candidate[candidate_id] = outcome
        if (len(run['candidate_outcomes']) != len(
                run['initial_candidate_outcomes'])
                or current_by_candidate != initial_by_candidate):
            raise ValueError(
                'discovery run candidate outcomes must exactly match '
                'initial candidate outcomes')
        run['candidate_outcomes'] = json.loads(_canonical_json(
            run['initial_candidate_outcomes']))
        run['candidate_snapshot'] = sorted(
            run['candidate_snapshot'], key=_canonical_json)
        run.setdefault('unified_draft_id', None)
        draft_id = run['unified_draft_id']
        if run['status'] == 'draft_created' and not draft_id:
            raise ValueError('draft_created discovery run requires a draft link')
        if (run['status'] in {
                'diagnosed_no_change', 'ready_to_finalize'}
                and draft_id is not None):
            raise ValueError(
                'non-draft discovery run cannot have a draft link')
        has_reusable_binding = any(
            _is_reusable_binding(binding)
            for binding in run['candidate_bindings'])
        if run['status'] == 'ready_to_finalize' and not has_reusable_binding:
            raise ValueError(
                'ready_to_finalize discovery run requires a reusable binding')
        if (run['status'] == 'diagnosed_no_change'
                and any(_is_actionable_binding(binding)
                        for binding in run['candidate_bindings'])):
            raise ValueError(
                'diagnosed_no_change discovery run cannot have an actionable binding')
        run.setdefault('supersedes_run_id', None)
        run.setdefault('created_at', utc_now())
        return run

    def create(self, project_id, item):
        run = self._validate(project_id, item)
        payload = _canonical_json(run)
        with self.repo._transaction():
            self.repo.get_project(project_id)
            try:
                self._db.execute(
                    'INSERT INTO artifacts(id,kind,project_id,payload) VALUES (?,?,?,?)',
                    (run['id'], KIND, project_id, payload))
            except sqlite3.IntegrityError as exc:
                row = self._db.execute(
                    'SELECT project_id,payload FROM artifacts WHERE id=? AND kind=?',
                    (run['id'], KIND)).fetchone()
                if row is None:
                    raise DiscoveryRunConflict(
                        'discovery run id collides with another artifact',
                        run_id=run['id']) from exc
                existing = json.loads(row['payload'])
                if (row['project_id'] != project_id
                        or existing.get('source_fingerprint') != run[
                            'source_fingerprint']
                        or _canonical_json(_immutable_snapshot(existing))
                        != _canonical_json(_immutable_snapshot(run))):
                    raise DiscoveryRunConflict(
                        'discovery run id has a different immutable snapshot',
                        run_id=run['id']) from exc
                return existing
        return self.get(project_id, run['id'])

    def get(self, project_id, run_id):
        with self.repo._lock:
            row = self._db.execute(
                'SELECT payload FROM artifacts WHERE id=? AND kind=? AND project_id=?',
                (run_id, KIND, project_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row['payload'])

    def list(self, project_id):
        self.repo.get_project(project_id)
        with self.repo._lock:
            rows = self._db.execute(
                'SELECT payload FROM artifacts WHERE kind=? AND project_id=? '
                'ORDER BY rowid DESC', (KIND, project_id)).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def latest(self, project_id, *, statuses=None):
        allowed = None if statuses is None else set(statuses)
        return next((run for run in self.list(project_id)
                     if allowed is None or run['status'] in allowed), None)

    def transition(self, project_id, run_id, expected_status, new_status, *,
                   unified_draft_id=None, candidate_outcomes=None):
        if expected_status not in RUN_STATUSES or new_status not in RUN_STATUSES:
            raise ValueError('unsupported discovery run status')
        if new_status not in ALLOWED_TRANSITIONS.get(expected_status, frozenset()):
            raise DiscoveryRunConflict(
                'invalid discovery run lifecycle transition', run_id=run_id,
                expected_status=expected_status)
        if candidate_outcomes is not None and not isinstance(
                candidate_outcomes, list):
            raise ValueError('discovery run outcomes must be a list')
        with self.repo._transaction():
            current = self.get(project_id, run_id)
            if current['status'] != expected_status:
                raise DiscoveryRunConflict(
                    'discovery run status changed', run_id=run_id,
                    expected_status=expected_status,
                    current_status=current['status'])
            updated = dict(current)
            updated['status'] = new_status
            if unified_draft_id is not None:
                if current.get('unified_draft_id') != unified_draft_id:
                    raise DiscoveryRunConflict(
                        'discovery run draft link cannot change', run_id=run_id,
                        expected_status=expected_status,
                        current_status=current['status'])
            updated['candidate_outcomes'] = _merge_candidate_outcomes(
                current.get('candidate_outcomes', []),
                candidate_outcomes or [],
                current.get('initial_candidate_outcomes', []),
                run_id=run_id)
            updated['updated_at'] = utc_now()
            cursor = self._db.execute(
                "UPDATE artifacts SET payload=? WHERE id=? AND kind=? "
                "AND project_id=? AND json_extract(payload,'$.status')=?",
                (_canonical_json(updated), run_id, KIND, project_id,
                 expected_status))
            if cursor.rowcount != 1:
                raise DiscoveryRunConflict(
                    'discovery run status changed', run_id=run_id,
                    expected_status=expected_status)
        return self.get(project_id, run_id)
