import pytest

from knowledge_service.repository import Repository


def test_public_unit_of_work_rolls_back_nested_repository_writes(tmp_path):
    repository = Repository(tmp_path / 'unit-of-work.sqlite')

    with pytest.raises(RuntimeError, match='abort'):
        with repository.unit_of_work():
            repository.create_project('will roll back')
            raise RuntimeError('abort')

    assert repository.list_projects() == []


def test_public_unit_of_work_commits_nested_repository_writes(tmp_path):
    repository = Repository(tmp_path / 'unit-of-work-commit.sqlite')

    with repository.unit_of_work():
        created = repository.create_project('committed')

    assert repository.get_project(created['id'])['name'] == 'committed'
