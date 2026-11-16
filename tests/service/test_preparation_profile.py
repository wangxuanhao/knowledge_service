import io
import unittest
import zipfile
from knowledge_service.integrations.document_package import SingleDocumentPackageReader

class ResourceUploadProfileTests(unittest.TestCase):
    def archive(self, name, body):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as out:
            out.writestr(name, body)
        return stream.getvalue()

    def test_resource_upload_profile_rejects_pdf_before_parser(self):
        reader = SingleDocumentPackageReader()
        self.assertTrue(callable(getattr(reader, 'validate_upload', None)), 'new uploads need an explicit lightweight MD/HTML resource profile')
        with self.assertRaisesRegex(ValueError, 'MD/HTML'):
            reader.validate_upload(self.archive('main.pdf', b'%PDF-1.7'))

    def test_resource_upload_profile_allows_outer_folder_without_manifest(self):
        reader = SingleDocumentPackageReader()
        self.assertTrue(callable(getattr(reader, 'validate_upload', None)))
        value = reader.validate_upload(self.archive('outer/main.md', '# Rules'))
        self.assertEqual(value['entry'], 'main.md')

    def test_manifest_cannot_hide_multiple_root_main_documents(self):
        import json
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'schema_version':2,'document':{'entry':'first.md'}}))
            archive.writestr('first.md','# First')
            archive.writestr('second.html','<h1>Second</h1>')
        with self.assertRaisesRegex(ValueError,'分别上传'):
            SingleDocumentPackageReader().validate_upload(stream.getvalue())


class DisabledOCRTests(unittest.TestCase):
    def test_disabled_preparation_does_not_invoke_image_ocr(self):
        from types import SimpleNamespace
        from knowledge_service.services.source_ingest import prepare_source_ingest
        def forbidden(*args,**kwargs):
            self.fail('disabled preparation invoked OCR')
        sources=SimpleNamespace(image_ocr=SimpleNamespace(process_asset=forbidden))
        source={'id':'source','source_text':'','blocks':[],'assets':[{'id':'image','kind':'local','status':'available','provenance':{}}]}
        chunks,text,warnings=prepare_source_ingest(sources,'project',source,{'extraction_mode':'documents','parse_config':{'ocr_mode':'disabled'}})
        self.assertEqual(chunks,[])
        self.assertTrue(any(w.get('code')=='ocr_disabled' for w in warnings if isinstance(w,dict)))

class EffectiveModelIdentityTests(unittest.TestCase):
    def test_default_relative_model_files_participate_in_identity(self):
        import os
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from knowledge_service.services.preparations import fingerprint
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                root=Path('data/model/ocr/pp-ocr-v6-small')
                root.mkdir(parents=True)
                weights=root/'model.onnx'
                weights.write_bytes(b'first')
                with patch.dict(os.environ,{},clear=False):
                    os.environ.pop('KG_IMAGE_OCR_MODEL_ROOT',None)
                    one=fingerprint('same','same.md',{})
                    weights.write_bytes(b'changed weights')
                    two=fingerprint('same','same.md',{})
                self.assertNotEqual(one,two)
            finally:os.chdir(previous)

if __name__ == '__main__':
    unittest.main()
