import unittest

from pydantic import ValidationError

from knowledge_service.models import (
    Ingest,
    IngestBatchCreate,
    IngestBatchSettings,
    IngestSettings,
    UploadOptions,
)


class IngestRequestContractTests(unittest.TestCase):
    def request_types(self):
        return (
            (IngestSettings, {}),
            (Ingest, {'title': '规则', 'text': '原文'}),
            (UploadOptions, {}),
            (IngestBatchSettings, {}),
        )

    def test_ontology_is_the_default_for_every_ingest_request(self):
        for model, required in self.request_types():
            with self.subTest(model=model.__name__):
                request = model.model_validate(required)
                self.assertEqual(request.extraction_mode, 'ontology')
                self.assertNotIn('extract', request.model_dump())

    def test_each_supported_mode_is_preserved(self):
        for model, required in self.request_types():
            for mode in ('ontology', 'discovery', 'documents'):
                with self.subTest(model=model.__name__, mode=mode):
                    request = model.model_validate({**required, 'extraction_mode': mode})
                    self.assertEqual(request.model_dump()['extraction_mode'], mode)

    def test_removed_extract_flag_is_rejected_even_with_an_explicit_mode(self):
        for model, required in self.request_types():
            for flag in (False, True):
                for mode in (None, 'documents'):
                    with self.subTest(model=model.__name__, flag=flag, mode=mode):
                        payload = {**required, 'extract': flag}
                        if mode is not None:
                            payload['extraction_mode'] = mode
                        with self.assertRaises(ValidationError) as caught:
                            model.model_validate(payload)
                        self.assertIn(
                            ('extract', 'extra_forbidden'),
                            [(error['loc'][0], error['type']) for error in caught.exception.errors()],
                        )

    def test_null_or_unknown_modes_are_rejected(self):
        for model, required in self.request_types():
            for mode in (None, 'extract', ''):
                with self.subTest(model=model.__name__, mode=mode):
                    with self.assertRaises(ValidationError):
                        model.model_validate({**required, 'extraction_mode': mode})

    def test_batch_envelope_rejects_removed_flag_in_settings(self):
        with self.assertRaises(ValidationError) as caught:
            IngestBatchCreate.model_validate({'settings': {'extract': False}})
        self.assertEqual(caught.exception.errors()[0]['loc'], ('settings', 'extract'))


if __name__ == '__main__':
    unittest.main()
