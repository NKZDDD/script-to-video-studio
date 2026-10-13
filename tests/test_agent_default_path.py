import copy
from pathlib import Path

import pytest

from core import agent_projects as A
from server import app, agent_api


@pytest.fixture
def config(tmp_path, monkeypatch):
    data = {'projects_dir': str(tmp_path/'old'), 'providers': {},
            'limits': {'global': 8, 'per_provider': {}}}
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(data))
    monkeypatch.setattr(app, 'save_config', lambda value: data.update(copy.deepcopy(value)))
    monkeypatch.setattr(app, 'caps_of', lambda cfg=None: [])
    return data


def test_save_default_path_preserves_old_projects_and_persists(tmp_path, config):
    old = A.create_project(config['projects_dir'], {'title': 'old project', 'source': ''}, [])
    new = tmp_path/'new parent'
    new.mkdir()
    sentinel = new/'.写入自检'
    sentinel.write_text('existing file', encoding='utf-8')
    result = agent_api.post(app, '/api/agent/paths/projects', {'projects_dir': str(new)})
    assert result['ok'] and config['projects_dir'] == str(new)
    assert old.root in [p['root'] for p in agent_api.projects(config)]
    assert sentinel.read_text(encoding='utf-8') == 'existing file'
    assert list(new.iterdir()) == [sentinel]
    created = agent_api.post(app, '/api/agent/create', {'title': 'new project', 'source': ''})
    assert Path(created['root']).parent == new
    assert Path(old.root).is_dir()


@pytest.mark.parametrize('bad', ['', 'relative/folder', 'D:relative'])
def test_invalid_default_path_leaves_config_unchanged(config, bad):
    before = copy.deepcopy(config)
    with pytest.raises(ValueError):
        agent_api.post(app, '/api/agent/paths/projects', {'projects_dir': bad})
    assert config == before


def test_file_cannot_be_default_directory(tmp_path, config):
    f = tmp_path/'file.txt'
    f.write_text('keep')
    before = copy.deepcopy(config)
    with pytest.raises(ValueError):
        agent_api.post(app, '/api/agent/paths/projects', {'projects_dir': str(f)})
    assert config == before and f.read_text() == 'keep'
