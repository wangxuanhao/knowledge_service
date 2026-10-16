import pytest

import pg_support


@pytest.fixture
def postgres_backend():
    yield


def test_expanded_test_slots_are_reused_without_replaying_migrations(monkeypatch):
    monkeypatch.setattr(pg_support, '_slots', {
        index: (f'slot_{index}', []) for index in range(1, 6)
    })
    monkeypatch.setattr(pg_support, '_binding', {
        f'path_{index}': index for index in range(1, 5)
    })
    monkeypatch.setattr(pg_support, 'ensure_test_database', lambda: None)
    monkeypatch.setattr(pg_support, '_reset_slot', lambda *args: None)
    monkeypatch.setattr(pg_support, '_slot_dsn', lambda schema: schema)

    def unexpected_create(index):
        raise AssertionError(f'Existing empty slot 5 should be reused, created {index}')

    monkeypatch.setattr(pg_support, '_create_slot', unexpected_create)
    assert pg_support.target('overflow') == 'slot_5'
    assert pg_support.target('overflow') == 'slot_5'
    assert set(pg_support._binding.values()) == {1, 2, 3, 4, 5}
