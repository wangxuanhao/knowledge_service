from unittest import TestCase
from knowledge_service.services.answer_evidence import AnswerEvidence

class ExpansionEvidenceLabelTest(TestCase):
    def test_only_server_extension_marker_survives_as_the_evidence_channel(self):
        trace = {'subject': {'answer_id': 'a'}, 'retrieval': {'requested_mode': 'semantic'}}
        for channel, expected in [('ontology_entity_type', 'ontology_entity_type'), ('ontology_type', 'ontology_type'),
                                  ('arbitrary', 'semantic'), (None, 'semantic')]:
            with self.subTest(channel=channel):
                summary = AnswerEvidence(None)._summary({'citation': 'E1', 'retrieval_channel': channel}, trace)
                self.assertEqual(summary['retrieval_channel'], expected)
