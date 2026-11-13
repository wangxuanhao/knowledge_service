"""Exact, release-bound source types and audited canonical reclassification."""
from collections import defaultdict

from .ontology import UnknownOntologyTerm


class IdentityTypeLineage:
    def __init__(self, snapshot, ontology, actions):
        self.snapshot=snapshot
        self.ontology=ontology
        self.aliases={}
        for release,classes in snapshot.get('ontology_class_aliases',{}).items():
            names=defaultdict(set)
            for item in classes:
                for name in (item.get('id'),item.get('name')):
                    if isinstance(name,str) and name:names[name].add(item['id'])
            self.aliases[release]=names
        self.bindings=defaultdict(set)
        events={event['id']:event for event in snapshot['assertion_events']}
        mapping_releases=defaultdict(lambda:defaultdict(set))
        mapping_types=defaultdict(lambda:defaultdict(set))
        for mapping in snapshot['record_version_assertions']:
            event=events.get(mapping['assertion_event_id'])
            if event and event['to_status']=='accepted' and mapping.get('mapped_ontology_id'):
                mapping_releases[mapping['assertion_id']][event['decision_version']].add(mapping['mapped_ontology_id'])
            if (event and event['to_status']=='accepted'
                    and event['assertion_id']==mapping['assertion_id']
                    and event.get('canonical_record_id')==mapping['record_id']):
                mapping_types[mapping['assertion_id']][event['decision_version']].add(
                    (mapping.get('mapped_type'),mapping.get('mapped_ontology_id')))
        self.source_releases={}
        for assertion_id,versions in mapping_releases.items():
            # The first accepted binding identifies the immutable occurrence's
            # vocabulary. Later identity moves map the same payload to newer
            # canonical releases; they must not reinterpret its local type.
            original=versions[min(versions)]
            if len(original)==1:self.source_releases[assertion_id]=next(iter(original))
        # A proposal is not an asserted type. Its first exact accepted mapping
        # fixes the reviewed classification; later identity moves cannot replace it.
        self.reviewed_types={}
        for assertion_id,versions in mapping_types.items():
            original=versions[min(versions)]
            classification=next(iter(original)) if len(original)==1 else (None,None)
            value,release=classification
            published=(self._published_type(value,release)
                       if isinstance(release,str) and release else None)
            self.reviewed_types[assertion_id]=(published,release) if published else None
        for event in snapshot['assertion_events']:
            if event['to_status']=='accepted' and event.get('canonical_record_id'):
                self.bindings[event['assertion_id']].add(event['canonical_record_id'])
        # Ledgers are append-only and ordered by commit, with planned ledgers
        # following the snapshot. An active undo cancels its target's authority;
        # undoing that undo restores it. Keep the historical audit rows intact.
        ledgers=[*snapshot['merge_operations'],
                 *(action for action in actions if action['kind']=='merge_ledger')]
        self.canceled_ledgers=set()
        for ledger in reversed(ledgers):
            if (ledger['id'] not in self.canceled_ledgers
                    and ledger.get('operation')=='merge_reversal' and ledger.get('reversal_of')):
                self.canceled_ledgers.add(ledger['reversal_of'])
        self.edges=defaultdict(lambda:defaultdict(set))
        for ledger in snapshot['merge_operations']:
            self._add_ledger(ledger,ledger.get('after_state',[]))
        token_records={action['token']:{**action['record'],
            'version':(action.get('expected_version') or 0)+1}
            for action in actions if action['kind']=='record_put'}
        version_records={row['version_id']:row for row in snapshot['records']}
        self.prospective_types=defaultdict(set)
        originals={row['id']:row for row in snapshot['assertions']}
        for action in actions:
            kind=action['kind']
            if kind=='assertion_transition' and action['status']=='accepted':
                assertion_id=action['id']
                original=originals.get(assertion_id)
                if (original is None or original['status']!='pending'
                        or original['decision_version']!=action['expected_version']):continue
            elif kind=='assertion_accept' and action['mode'] in {'create','pending'}:
                assertion_id=action['assertion']['id']
                original=originals.get(assertion_id)
                if original is not None and original['status']!='pending':continue
            else:continue
            ref=action.get('record_ref') or {}
            record=(token_records.get(ref.get('token')) if 'token' in ref
                    else version_records.get(ref.get('version_id')))
            if (record is None or record.get('kind')!='entity'
                    or record['id']!=action.get('canonical_record_id')):continue
            release=record.get('ontology_id') or snapshot.get('ontology_id')
            published=self._published_type(record.get('type'),release)
            if published:
                self.prospective_types[(assertion_id,record['id'])].add((published,release))
        for action in actions:
            if action['kind']=='merge_ledger':
                after=[token_records.get(ref.get('token')) if 'token' in ref
                       else version_records.get(ref.get('version_id'))
                       for ref in action['after_state_refs']]
                self._add_ledger(action,[row for row in after if row is not None],prospective=True)

    def _published_type(self, value, release):
        if not isinstance(value,str) or not value:return None
        if release in self.aliases:
            matches=self.aliases[release].get(value,set())
            return next(iter(matches)) if len(matches)==1 else None
        if release and release!=self.snapshot.get('ontology_id'):return None
        if self.ontology is not None:
            try:return str(self.ontology.resolve(value,self.ontology.classes))
            except UnknownOntologyTerm:pass
        return None

    def normalize(self, value, release=None):
        if release in self.aliases:
            matches=self.aliases[release].get(value,set())
            return next(iter(matches)) if len(matches)==1 else value
        # No historical local-name guess: an unavailable old publication can
        # only contribute its exact supplied IRI/literal.
        if release and release!=self.snapshot.get('ontology_id'):
            return value
        if self.ontology is not None:
            try:return str(self.ontology.resolve(value,self.ontology.classes))
            except UnknownOntologyTerm:pass
        return value

    def _add_ledger(self, ledger, after, *, prospective=False):
        if ledger.get('operation')!='ontology_remap' or ledger['id'] in self.canceled_ledgers:return
        before={row['id']:row for row in ledger.get('before_state',[]) if row.get('kind')=='entity'}
        actual={row['id']:row for row in self.snapshot['records']}
        audits=[row.get('metadata',{}) for row in after if row.get('kind')=='document'
                and row.get('metadata',{}).get('_audit') is True]
        for audit in audits:
            proof=audit.get('reclassify') or {}
            if audit.get('operation')!='reclassify' or audit.get('operation_id')!=ledger.get('id'):
                continue
            release=proof.get('to_ontology_id')
            if not release or release not in self.aliases:continue
            selected=proof.get('from_to') or []
            for target in after:
                original=before.get(target.get('id'))
                if (target.get('kind')!='entity' or original is None
                        or target.get('ontology_id')!=release
                        or ledger.get('expected_versions',{}).get(target['id'])!=original.get('version')
                        or target.get('version')!=original.get('version',0)+1):
                    continue
                if prospective:
                    previous=actual.get(target['id'])
                    if (previous is None or original.get('version_id')!=previous.get('version_id')
                            or original.get('version')!=previous.get('version')
                            or original.get('ontology_id')!=previous.get('ontology_id')
                            or self.normalize(original.get('type'),original.get('ontology_id'))
                               !=self.normalize(previous.get('type'),previous.get('ontology_id'))):
                        continue
                old=self.normalize(original.get('type'),original.get('ontology_id'))
                new=self.normalize(target.get('type'),release)
                if old==new:continue
                if any(self.normalize(item.get('from_type'),original.get('ontology_id'))==old
                       and self.normalize(item.get('to_type'),release)==new for item in selected):
                    self.edges[target['id']][old].add(new)

    def source_type(self, assertion, target):
        payload=assertion['payload']
        supplied=payload.get('type')
        canonical=self.normalize(target.get('type'),target.get('ontology_id'))
        release=payload.get('ontology_id') or self.source_releases.get(assertion['id'])
        if not supplied:
            if assertion['id'] in self.reviewed_types:
                classification=self.reviewed_types[assertion['id']]
                if classification is None:
                    raise ValueError('实体审核的首次接受映射缺少唯一的已发布类型')
                supplied,release=classification
            elif (assertion['id'],target['id']) in self.prospective_types:
                classifications=self.prospective_types[(assertion['id'],target['id'])]
                if len(classifications)!=1:
                    raise ValueError('实体审核的接受目标类型不唯一')
                supplied,release=next(iter(classifications))
            else:supplied=payload.get('proposed_type')
        if not supplied:return canonical
        source=self.normalize(supplied,release)
        if source==canonical:return canonical
        record_ids={target['id'],*self.bindings.get(assertion['id'],set())}
        adjacency=defaultdict(set)
        for record_id in record_ids:
            for old,new in self.edges.get(record_id,{}).items():adjacency[old].update(new)
        frontier,seen={source},set()
        while frontier:
            node=frontier.pop()
            if node in seen:continue
            seen.add(node)
            targets=adjacency.get(node,set())
            # Conflicting audited mappings do not authorize a convenient path.
            if len(targets)!=1:continue
            next_type=next(iter(targets))
            if next_type==canonical:return canonical
            frontier.add(next_type)
        return source
