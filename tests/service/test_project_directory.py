"""Project pickers must not aggregate unrelated projects' record inventories."""
from contextlib import contextmanager
from types import SimpleNamespace
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api.projects import install
from knowledge_service.repository.core import Repository


def test_directory_repository_reads_only_project_rows():
    statements = []

    class Database:
        def execute(self, sql):
            statements.append(sql)
            assert 'record_versions' not in sql and 'ontologies' not in sql
            return SimpleNamespace(fetchall=lambda: [
                {'id': 'p', 'name': 'Project', 'metadata': '{"ontology_mode":"discovery"}'}])

    repository = object.__new__(Repository)
    repository._lock = threading.RLock()
    repository._db = Database()
    assert repository.list_project_directory() == [
        {'id': 'p', 'name': 'Project', 'metadata': {'ontology_mode': 'discovery'}}]
    assert len(statements) == 1


def test_picker_uses_independent_directory_while_management_keeps_counts():
    calls = []

    class RepositoryStub:
        @contextmanager
        def independent_read_view(self):
            calls.append('snapshot')
            yield SimpleNamespace(list_project_directory=lambda: [{'id': 'p', 'name': 'Project'}])

        def list_projects(self):
            calls.append('counts')
            return [{'id': 'p', 'counts': {'entities': 7}}]

    app = FastAPI()
    install(app, SimpleNamespace(data=RepositoryStub()))
    with TestClient(app) as client:
        response = client.get('/api/projects?include_counts=false')
        assert response.status_code == 200
        assert response.json() == {'projects': [{'id': 'p', 'name': 'Project'}]}
        assert calls == ['snapshot']
        response = client.get('/api/projects')
        assert response.json()['projects'][0]['counts']['entities'] == 7
        assert calls == ['snapshot', 'counts']
