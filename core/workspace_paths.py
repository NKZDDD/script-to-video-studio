"""Editable storage locations; never merge or overwrite an existing project."""
import os
from pathlib import Path
import shutil
import tempfile

from . import paths
from .store import LOCK, Project, read_json, write_json


def directory(value):
    value = str(value or '').strip().strip('"')
    if not value or not os.path.isabs(os.path.expanduser(value)):
        raise ValueError('请填写完整的绝对目录，也可以使用选择文件夹')
    return Path(value).expanduser().resolve()


def writable(path):
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as check:
        check.write(b'ok')


def browse(value=''):
    if not value:
        roots = [f'{c}:\\' for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{c}:\\').is_dir()] if os.name == 'nt' else ['/']
        return {'path': '', 'parent': '', 'dirs': roots}
    folder = directory(value)
    if not folder.is_dir():
        raise ValueError('目录不存在；新目录可直接在输入框填写后保存')
    return {'path': str(folder), 'parent': str(folder.parent) if folder.parent != folder else '',
            'dirs': sorted((str(p) for p in folder.iterdir() if p.is_dir()), key=str.casefold)}


def change_data(app, value):
    with LOCK:
        if app.JOBS.active_count():
            raise ValueError('请等待运行中的批次结束或先停止，再修改数据目录')
        src, dst = Path(paths.data_dir()).resolve(), directory(value)
        if src == dst:
            return {'ok': True, 'data_dir': str(dst)}
        if dst.is_relative_to(src) or src.is_relative_to(dst):
            raise ValueError('新旧数据目录不能互相包含，请选择独立目录')
        if dst.exists() and any(dst.iterdir()):
            raise ValueError('目标数据目录非空；请选空目录，避免覆盖已有密钥和外挂')
        writable(dst)
        cfg = app.load_config()
        # Projects stay where they are. Copy only mutable application data, not the EXE/repository.
        from .captions import USER_DIR_NAME
        for name in ('providers', 'prompts', 'model-catalog.json', 'pricing.json', 'account_usage.json', 'studio-device.json', USER_DIR_NAME):
            source = src/name
            if source.is_dir():
                shutil.copytree(source, dst/name)
            elif source.is_file():
                shutil.copy2(source, dst/name)
        write_json(str(dst/'config.json'), cfg)
        write_json(str(src/paths.DATA_REDIRECT), {'data_dir': str(dst)})
        paths.set_data_dir(str(dst))
        return {'ok': True, 'data_dir': str(dst), 'previous': str(src)}


def relocate_project(app, pj, value):
    with LOCK:
        if app.JOBS.list(project_root=pj.root, active_only=True):
            raise ValueError('请等待这个项目的运行批次结束或先停止，再修改项目目录')
        src, dst = Path(pj.root).resolve(), directory(value)
        if src == dst:
            return {'ok': True, 'root': str(src)}
        if dst.exists():
            raise ValueError('目标项目目录已存在；请选择新的完整目录，避免覆盖或合并项目')
        if dst.is_relative_to(src) or src.is_relative_to(dst):
            raise ValueError('新旧项目目录不能互相包含')
        # Verify resolved source is precisely the registered project; never move its parent.
        if not src.is_dir() or src == Path(src.anchor) or str(src) != str(Path(pj.root).resolve()):
            raise ValueError('原项目目录无效')
        writable(dst.parent)
        cfg = app.load_config()
        originals = {name: (src/'00_项目说明'/name).read_bytes()
                     for name in ('project-lock.json', 'production-contract.md', 'AGENT_TASK.md')
                     if (src/'00_项目说明'/name).is_file()}
        shutil.move(str(src), str(dst))
        try:
            new = Project(str(dst))
            lockfile = new.p('00_项目说明', 'project-lock.json')
            lock = read_json(lockfile, {})
            if lock:
                lock['project_root'] = str(dst)
                write_json(lockfile, lock)
            # Preserve user-edited contract text and replace only its literal former root.
            for name in ('production-contract.md', 'AGENT_TASK.md'):
                file = dst/'00_项目说明'/name
                if file.is_file():
                    text = file.read_text(encoding='utf-8')
                    file.write_text(text.replace(pj.root, str(dst)).replace(src.as_posix(), dst.as_posix()), encoding='utf-8')
            cfg['agent_project_dirs'] = sorted(set(cfg.get('agent_project_dirs', []) + [str(dst.parent)]))
            app.save_config(cfg)
        except Exception:
            for name, blob in originals.items():
                (dst/'00_项目说明'/name).write_bytes(blob)
            # Both absolute targets above were verified, and src is now absent.
            if not src.exists():
                shutil.move(str(dst), str(src))
            raise
        for job in app.JOBS.jobs.values():
            if job.project_root == pj.root:
                job.project_root = str(dst)
        return {'ok': True, 'root': str(dst), 'previous': str(src)}
