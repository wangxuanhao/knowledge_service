"""The public package facade must not initialize the application on import.

These tests also run directly with unittest, without the PostgreSQL fixtures.
Each contract gets a fresh interpreter so previously imported modules cannot
hide an accidental dependency on the HTTP, persistence, or ML stack.
"""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_EXPORTS = [
    'create_app', 'Repository', 'OntologyNotPublished', 'KnowledgeService',
    '__version__',
]


class PackageImportBoundaryTests(unittest.TestCase):
    def run_python(self, source):
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c', textwrap.dedent(source), str(ROOT)],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_lightweight_import(self, statement):
        self.run_python(f'''
            import importlib.abc
            import sys
            sys.path.insert(0, sys.argv[1])

            blocked = (
                'knowledge_service.api', 'knowledge_service.repository',
                'knowledge_service.services', 'fastapi', 'psycopg',
                'psycopg_pool', 'rdflib', 'pymilvus', 'numpy', 'torch',
                'transformers', 'sentence_transformers', 'semantica', 'docling',
            )

            class RejectApplicationImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + '.')
                           for name in blocked):
                        raise AssertionError('unexpected eager import: ' + fullname)

            sys.meta_path.insert(0, RejectApplicationImports())
            {statement}
            loaded = [name for name in sys.modules if any(
                name == root or name.startswith(root + '.') for root in blocked)]
            assert not loaded, loaded
        ''')

    def test_package_import_keeps_application_dependencies_unloaded(self):
        self.assert_lightweight_import('import knowledge_service')

    def test_lightweight_submodules_keep_application_dependencies_unloaded(self):
        for module in (
                'knowledge_service.core.time',
                'knowledge_service.core.config',
                'knowledge_service.utils.diagnostics'):
            with self.subTest(module=module):
                self.assert_lightweight_import(f'import {module}')

    def test_package_metadata_and_discovery_do_not_resolve_exports(self):
        self.assert_lightweight_import(
            'import knowledge_service; '
            f'assert knowledge_service.__all__ == {PUBLIC_EXPORTS!r}; '
            "assert knowledge_service.__version__ == '1.1.0'; "
            'assert set(knowledge_service.__all__) <= set(dir(knowledge_service)); '
            "assert not hasattr(knowledge_service, 'missing_public_export')")

    def test_public_exports_keep_canonical_identity(self):
        self.run_python(f'''
            import importlib
            import sys
            sys.path.insert(0, sys.argv[1])
            import knowledge_service

            from knowledge_service import Repository, OntologyNotPublished
            from knowledge_service.repository import (
                Repository as CanonicalRepository,
                OntologyNotPublished as CanonicalOntologyNotPublished,
            )
            assert Repository is CanonicalRepository
            assert OntologyNotPublished is CanonicalOntologyNotPublished
            assert 'knowledge_service.api' not in sys.modules
            assert 'fastapi' not in sys.modules
            assert 'knowledge_service.services.service' not in sys.modules

            from knowledge_service import create_app, KnowledgeService
            from knowledge_service.api import create_app as canonical_create_app
            from knowledge_service.services.service import (
                KnowledgeService as CanonicalKnowledgeService,
            )
            assert create_app is canonical_create_app
            assert KnowledgeService is CanonicalKnowledgeService
            assert knowledge_service.__all__ == {PUBLIC_EXPORTS!r}
            assert knowledge_service.__version__ == '1.1.0'

            namespace = {{}}
            exec('from knowledge_service import *', namespace)
            for name in knowledge_service.__all__:
                assert namespace[name] is getattr(knowledge_service, name), name
                assert knowledge_service.__dict__[name] is namespace[name], name

            try:
                knowledge_service.missing_public_export
            except AttributeError as exc:
                assert 'missing_public_export' in str(exc)
            else:
                raise AssertionError('unknown exports must raise AttributeError')
        ''')


if __name__ == '__main__':
    unittest.main()
