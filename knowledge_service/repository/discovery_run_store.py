"""Insert-only discovery-run snapshots stored in the shared artifacts table."""

from __future__ import annotations

import json
import sqlite3

from ..core.time import utc_now
from ..services.ontology_iri import valid_application_iri


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
REUSABLE_BINDING_STATES = frozenset({'existing', 'reusable'})
PROPOSED_BINDING_STATES = frozenset({'proposed', 'new'})
DIAGNOSTIC_BINDING_STATES = frozenset({
    'diagnostic', 'quarantined', 'deferred',
})
DOCUMENTED_BINDING_STATES = (
    REUSABLE_BINDING_STATES
    | PROPOSED_BINDING_STATES
    | DIAGNOSTIC_BINDING_STATES
)
DOCUMENTED_TARGET_KINDS = frozenset({'class', 'relation', 'attribute'})
CANDIDATE_TARGET_KIND_ALIASES = {
    'entity': 'class',
    'class': 'class',
    'relation': 'relation',
    'attribute': 'attribute',
}
INITIAL_OUTCOME_DISPOSITIONS = {
    'ontology_term_conflict': frozenset({'skipped'}),
    'low_frequency_attribute': frozenset({'deferred'}),
    'manual_review_deferred': frozenset({'deferred'}),
}
TERMINAL_OUTCOME_DISPOSITIONS = {
    'materialized': frozenset({'materialized'}),
    'required_operation_missing': frozenset({'skipped'}),
    'required_operation_rejected': frozenset({'skipped'}),
    'required_operation_superseded': frozenset({'skipped'}),
    'ontology_validation_failed': frozenset({'skipped'}),
    'draft_closed': frozenset({'skipped'}),
}
REQUIRED_OPERATION_FAILURE_REASONS = frozenset({
    'required_operation_missing',
    'required_operation_rejected',
    'required_operation_superseded',
})
OUTCOME_REQUIRED_FIELDS = frozenset({
    'candidate_id', 'status', 'reason_code',
})
OPERATION_ID_FIELDS = ('required_operation_ids', 'optional_operation_ids')
IRI_FIELDS = ('target_iri', 'reuse_iri', 'iri')


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
        and (binding.get('binding_kind') in REUSABLE_BINDING_STATES
             or binding.get('status') in REUSABLE_BINDING_STATES))


def _is_proposed_binding(binding):
    return bool(
        isinstance(binding, dict)
        and binding.get('candidate_id')
        and (binding.get('binding_kind') in PROPOSED_BINDING_STATES
             or binding.get('status') in PROPOSED_BINDING_STATES))


def _is_actionable_binding(binding):
    return _is_reusable_binding(binding) or _is_proposed_binding(binding)


def _is_resolved_diagnostic_binding(binding, initial_outcome_ids):
    return bool(
        isinstance(binding, dict)
        and binding.get('candidate_id') in initial_outcome_ids
        and (binding.get('binding_kind') in DIAGNOSTIC_BINDING_STATES
             or binding.get('status') in DIAGNOSTIC_BINDING_STATES))


def _binding_state_family(state):
    if state in REUSABLE_BINDING_STATES:
        return 'reusable'
    if state in PROPOSED_BINDING_STATES:
        return 'proposed'
    if state in DIAGNOSTIC_BINDING_STATES:
        return 'diagnostic'
    return None


def _binding_reuse_iri(binding):
    if not isinstance(binding, dict):
        return None
    if 'target_iri' in binding:
        return binding['target_iri']
    return binding.get('reuse_iri') or binding.get('iri')


def _binding_has_operations(binding):
    return bool(
        isinstance(binding, dict)
        and (binding.get('required_operation_ids')
             or binding.get('optional_operation_ids')))


def _unique_nonempty_strings(values, label):
    if (not isinstance(values, list)
            or any(not isinstance(value, str) or not value.strip()
                   for value in values)
            or len(values) != len(set(values))):
        raise ValueError(
            f'discovery run {label} must be unique non-empty strings')
    return set(values)


def _validate_outcome_schema(outcome, *, initial):
    label = 'initial candidate outcome' if initial else 'terminal candidate outcome'
    allowed_fields = (
        OUTCOME_REQUIRED_FIELDS | {'diagnostic_code'}
        if initial else OUTCOME_REQUIRED_FIELDS
    )
    if (not isinstance(outcome, dict)
            or set(outcome) - allowed_fields
            or not OUTCOME_REQUIRED_FIELDS.issubset(outcome)
            or not isinstance(outcome.get('candidate_id'), str)
            or not outcome['candidate_id'].strip()
            or not isinstance(outcome.get('status'), str)
            or not isinstance(outcome.get('reason_code'), str)):
        raise ValueError(f'discovery run {label} has an invalid schema')
    if ('diagnostic_code' in outcome
            and (not isinstance(outcome['diagnostic_code'], str)
                 or not outcome['diagnostic_code'].strip())):
        raise ValueError(f'discovery run {label} has an invalid schema')
    dispositions = (
        INITIAL_OUTCOME_DISPOSITIONS
        if initial else TERMINAL_OUTCOME_DISPOSITIONS
    )
    allowed_statuses = dispositions.get(outcome['reason_code'])
    if allowed_statuses is None or outcome['status'] not in allowed_statuses:
        raise ValueError(
            f'discovery run {label} has an incompatible status or reason_code')


def _validate_candidate_bindings(run, initial_outcomes):
    snapshot_ids = []
    snapshot_by_id = {}
    for candidate in run['candidate_snapshot']:
        if (not isinstance(candidate, dict)
                or not isinstance(candidate.get('id'), str)
                or not candidate['id'].strip()):
            raise ValueError(
                'discovery run candidate snapshot requires non-empty string IDs')
        snapshot_ids.append(candidate['id'])
        snapshot_by_id[candidate['id']] = candidate
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise ValueError(
            'discovery run candidate snapshot IDs must be unique')
    snapshot_id_set = set(snapshot_ids)
    accepted_ids = _unique_nonempty_strings(
        run['accepted_candidate_ids'], 'accepted candidate IDs')
    if not accepted_ids.issubset(snapshot_id_set):
        raise ValueError(
            'discovery run accepted candidate IDs must exist in the candidate snapshot')
    outcome_ids = set(initial_outcomes)
    if (not outcome_ids.issubset(snapshot_id_set)
            or outcome_ids & accepted_ids):
        raise ValueError(
            'discovery run outcome candidate must exist in the candidate '
            'snapshot and cannot be accepted')
    if accepted_ids | outcome_ids != snapshot_id_set:
        raise ValueError(
            'discovery run accepted candidates and initial outcome candidates '
            'must exactly partition the candidate snapshot')

    binding_ids = set()
    active_binding_ids = set()
    for binding in run['candidate_bindings']:
        if not isinstance(binding, dict):
            raise ValueError('discovery run candidate binding must be a mapping')
        candidate_id = binding.get('candidate_id')
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError(
                'discovery run candidate binding requires a non-empty candidate_id')
        if candidate_id in binding_ids:
            raise ValueError(
                'discovery run binding candidate IDs must exactly cover '
                'accepted_candidate_ids once')
        binding_ids.add(candidate_id)
        if candidate_id not in snapshot_id_set:
            raise ValueError(
                'discovery run binding candidate_id must exist in the candidate snapshot')
        if binding.get('target_kind') not in DOCUMENTED_TARGET_KINDS:
            raise ValueError(
                'discovery run binding target_kind is not documented')
        candidate_target_kind = CANDIDATE_TARGET_KIND_ALIASES.get(
            snapshot_by_id[candidate_id].get('kind'))
        if binding['target_kind'] != candidate_target_kind:
            raise ValueError(
                'discovery run binding target_kind must match the candidate kind')

        operation_ids = {}
        for field in OPERATION_ID_FIELDS:
            if field not in binding:
                raise ValueError(
                    'discovery run binding operation IDs must include required '
                    'and optional lists')
            operation_ids[field] = _unique_nonempty_strings(
                binding[field], f'binding {field} operation IDs')
        if (operation_ids['required_operation_ids']
                & operation_ids['optional_operation_ids']):
            raise ValueError(
                'discovery run binding required and optional operation IDs '
                'cannot overlap')

        binding_kind = binding.get('binding_kind')
        binding_status = binding.get('status')
        if (binding_kind is not None
                and binding_kind not in DOCUMENTED_BINDING_STATES):
            raise ValueError('discovery run binding kind is not documented')
        if (binding_status is not None
                and binding_status not in DOCUMENTED_BINDING_STATES):
            raise ValueError('discovery run binding status is not documented')
        if binding_kind is None and binding_status is None:
            raise ValueError('discovery run binding state is required')
        kind_family = _binding_state_family(binding_kind)
        status_family = _binding_state_family(binding_status)
        if (kind_family is not None and status_family is not None
                and kind_family != status_family):
            raise ValueError(
                'discovery run binding state fields are contradictory')

        diagnostic = _is_resolved_diagnostic_binding(
            binding, initial_outcomes)
        diagnostic_state = (
            kind_family == 'diagnostic' or status_family == 'diagnostic')
        if diagnostic_state and not diagnostic:
            raise ValueError(
                'discovery run diagnostic binding requires an initial outcome')
        if (_is_proposed_binding(binding)
                and not operation_ids['required_operation_ids']):
            raise ValueError(
                'discovery run proposed binding requires a required operation')
        provided_iris = [
            binding[field] for field in IRI_FIELDS if field in binding]
        if any(not valid_application_iri(iri) for iri in provided_iris):
            raise ValueError(
                'discovery run binding target must be a valid application IRI')
        if len(set(provided_iris)) > 1:
            raise ValueError(
                'discovery run binding target IRI fields must agree')
        if diagnostic:
            if _binding_has_operations(binding):
                raise ValueError(
                    'discovery run diagnostic binding cannot contain operation IDs')
            continue
        if not (_is_reusable_binding(binding) or _is_proposed_binding(binding)):
            raise ValueError('discovery run binding state is not actionable')
        if not provided_iris:
            raise ValueError(
                'discovery run binding requires a valid application target IRI')
        active_binding_ids.add(candidate_id)

    if active_binding_ids != accepted_ids:
        raise ValueError(
            'discovery run binding candidate IDs must exactly cover '
            'accepted_candidate_ids once')


def discovery_result_kind(run):
    """Return the immutable creation result independently of lifecycle status."""
    if run.get('unified_draft_id'):
        return 'draft'
    if any(_is_reusable_binding(binding)
           for binding in run.get('candidate_bindings', ())):
        return 'mapping_only'
    return 'diagnosed_no_change'


def _merge_candidate_outcomes(
        current, terminal, initial, *, run_id, snapshot_candidate_ids,
        eligible_bindings, destination_status):
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
    normalized_terminal = []
    terminal_ids = set()
    for outcome in terminal:
        if (not isinstance(outcome, dict)
                or not isinstance(outcome.get('candidate_id'), str)
                or not outcome['candidate_id'].strip()):
            raise ValueError(
                'discovery run outcomes require a candidate_id')
        normalized = json.loads(_canonical_json(outcome))
        candidate_id = normalized['candidate_id']
        if candidate_id in terminal_ids:
            raise ValueError(
                'discovery run terminal outcome candidate IDs must be unique')
        terminal_ids.add(candidate_id)
        normalized_terminal.append(normalized)

    resolved_ids = set(positions) - set(protected)
    unresolved_ids = set(eligible_bindings) - resolved_ids
    if (destination_status in {'stale_base', 'stale_source'}
            and normalized_terminal):
        raise ValueError(
            'discovery run stale transition cannot contain terminal outcomes')
    for normalized in normalized_terminal:
        candidate_id = normalized['candidate_id']
        protected_outcome = protected.get(candidate_id)
        if protected_outcome is not None:
            raise DiscoveryRunConflict(
                'immutable discovery outcome cannot change', run_id=run_id)
        _validate_outcome_schema(normalized, initial=False)
        if candidate_id not in snapshot_candidate_ids:
            raise ValueError(
                'discovery run terminal outcome candidate must exist in the '
                'candidate snapshot')
        if candidate_id not in unresolved_ids:
            raise ValueError(
                'discovery run terminal outcomes require unresolved accepted '
                'and bound candidates')
        if (normalized['reason_code'] in REQUIRED_OPERATION_FAILURE_REASONS
                and not eligible_bindings[candidate_id].get(
                    'required_operation_ids')):
            raise ValueError(
                'discovery run terminal candidate outcome reason_code is '
                'incompatible with its binding')
        if (destination_status == 'closed'
                and (normalized['status'], normalized['reason_code'])
                != ('skipped', 'draft_closed')):
            raise ValueError(
                'discovery run terminal candidate outcome for closed runs '
                'must be skipped with reason_code draft_closed')
        if (destination_status in {'published', 'finalized_no_change'}
                and normalized['reason_code'] == 'draft_closed'):
            raise ValueError(
                'discovery run terminal candidate outcome has an '
                'incompatible destination lifecycle')
    if (destination_status in {
            'published', 'finalized_no_change', 'closed'}
            and terminal_ids != unresolved_ids):
        raise ValueError(
            'discovery run terminal outcomes must cover every unresolved '
            'accepted and bound candidate')
    merged.extend(normalized_terminal)
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
            _validate_outcome_schema(outcome, initial=True)
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
        _validate_candidate_bindings(run, initial_by_candidate)
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
        if run['status'] == 'ready_to_finalize':
            unresolved_bindings = [
                binding for binding in run['candidate_bindings']
                if not _is_resolved_diagnostic_binding(
                    binding, initial_by_candidate)]
            reusable_bindings = [
                binding for binding in unresolved_bindings
                if (_is_reusable_binding(binding)
                    and not binding.get('required_operation_ids'))]
            if (not reusable_bindings
                    or len(reusable_bindings) != len(unresolved_bindings)):
                raise ValueError(
                    'ready_to_finalize discovery run requires only reusable '
                    'bindings without required operations and at least one '
                    'reusable binding')
            if any(not valid_application_iri(_binding_reuse_iri(binding))
                   for binding in reusable_bindings):
                raise ValueError(
                    'ready_to_finalize reusable bindings require a valid '
                    'application IRI')
        if (run['status'] in {'ready_to_finalize', 'diagnosed_no_change'}
                and any(_binding_has_operations(binding)
                        for binding in run['candidate_bindings'])):
            raise ValueError(
                'no-draft discovery run bindings cannot contain operation IDs')
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
            snapshot_candidate_ids = {
                candidate.get('id')
                for candidate in current.get('candidate_snapshot', [])
                if isinstance(candidate, dict)
            }
            accepted_candidate_ids = set(
                current.get('accepted_candidate_ids', []))
            bindings_by_candidate = {
                binding.get('candidate_id'): binding
                for binding in current.get('candidate_bindings', [])
                if isinstance(binding, dict)
            }
            initial_candidate_ids = {
                outcome.get('candidate_id')
                for outcome in current.get('initial_candidate_outcomes', [])
                if isinstance(outcome, dict)
            }
            eligible_candidate_ids = (
                snapshot_candidate_ids
                & accepted_candidate_ids
                & set(bindings_by_candidate)
            ) - initial_candidate_ids
            eligible_bindings = {
                candidate_id: bindings_by_candidate[candidate_id]
                for candidate_id in eligible_candidate_ids
            }
            updated['candidate_outcomes'] = _merge_candidate_outcomes(
                current.get('candidate_outcomes', []),
                candidate_outcomes or [],
                current.get('initial_candidate_outcomes', []),
                run_id=run_id,
                snapshot_candidate_ids=snapshot_candidate_ids,
                eligible_bindings=eligible_bindings,
                destination_status=new_status)
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
