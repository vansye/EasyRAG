"""Maintenance uses the same process exclusion before opening owned resources."""

import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app import maintenance
from app.application.process_lock import ProcessLock, RuntimeLockUnavailable
from app.application.recovery import RebuildResult
from app.application.runtime import RuntimeSettings
from app.modules.knowledge.public import SchemaMismatch


@pytest.fixture
def command(tmp_path, monkeypatch):
    path = tmp_path / 'backend.lock'
    monkeypatch.setattr(maintenance, 'RuntimeSettings',
                        lambda: RuntimeSettings(_env_file=None, runtime_lock_file=path), raising=False)
    a, b = Mock(), Mock()

    def locked():
        with pytest.raises(RuntimeLockUnavailable):
            with ProcessLock(path):
                pass

    def knowledge():
        locked()
        return a

    def retrieval():
        locked()
        return b

    a.close.side_effect = locked
    b.close.side_effect = locked
    make_a, make_b = Mock(side_effect=knowledge), Mock(side_effect=retrieval)
    monkeypatch.setattr(maintenance, 'Knowledge', make_a, raising=False)
    monkeypatch.setattr(maintenance, 'Retrieval', make_b, raising=False)
    return path, a, b, make_a, make_b


@pytest.mark.parametrize('name,method', [('init-db', 'initialize_database'), ('adopt-legacy-db', 'adopt_legacy_database')])
def test_schema_commands_only_construct_knowledge_under_lock(command, capsys, name, method):
    path, a, b, make_a, make_b = command
    assert maintenance.main([name]) == 0
    getattr(a, method).assert_called_once_with()
    a.close.assert_called_once_with()
    make_a.assert_called_once_with()
    make_b.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {'command': name, 'status': 'OK'}
    with ProcessLock(path):
        pass


def test_running_backend_rejects_maintenance_before_any_resource_creation(command, capsys):
    path, _a, _b, make_a, make_b = command
    with ProcessLock(path):
        assert maintenance.main(['rebuild-index']) == 1
    make_a.assert_not_called()
    make_b.assert_not_called()
    assert json.loads(capsys.readouterr().err)['cause'] == 'RuntimeLockUnavailable'


def test_rebuild_calls_public_workflow_and_closes_both_modules(command, monkeypatch, capsys):
    path, a, b, _make_a, _make_b = command
    rebuild = Mock(return_value=RebuildResult(3, 9, 5))
    monkeypatch.setattr(maintenance, 'rebuild_index', rebuild, raising=False)
    assert maintenance.main(['rebuild-index']) == 0
    rebuild.assert_called_once_with(a, b)
    a.close.assert_called_once_with()
    b.close.assert_called_once_with()
    assert json.loads(capsys.readouterr().out) == {
        'command': 'rebuild-index', 'status': 'OK', 'documents': 3, 'chunks': 9, 'reused_chunks': 5,
    }
    with ProcessLock(path):
        pass


def test_failed_maintenance_is_nonzero_sanitized_and_still_closes(command, monkeypatch, capsys):
    path, a, b, _make_a, _make_b = command
    monkeypatch.setattr(maintenance, 'rebuild_index', Mock(side_effect=SchemaMismatch('secret-db-password')), raising=False)
    assert maintenance.main(['rebuild-index']) == 1
    output = capsys.readouterr()
    assert output.out == '' and 'secret-db-password' not in output.err
    assert json.loads(output.err)['cause'] == 'SchemaMismatch'
    a.close.assert_called_once_with()
    b.close.assert_called_once_with()
    with ProcessLock(path):
        pass


def test_python_module_exposes_all_three_maintenance_commands():
    result = subprocess.run([sys.executable, '-m', 'app.maintenance', '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert all(name in result.stdout for name in ('init-db', 'adopt-legacy-db', 'rebuild-index'))
