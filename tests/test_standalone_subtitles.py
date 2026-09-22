import importlib.util
from pathlib import Path
import threading

import pytest
from core import paths


@pytest.fixture
def app(monkeypatch,tmp_path):
    original=paths.data_dir()
    spec=importlib.util.spec_from_file_location('subtitle_app_fixture',Path(__file__).resolve().parents[1]/'subtitle_app/main.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    paths.set_data_dir(str(tmp_path/'settings'))
    try:yield mod
    finally:paths.set_data_dir(original)


def test_imported_subtitles_skip_recognition_and_preserve_sources(app,tmp_path,monkeypatch):
    video=tmp_path/'video.mp4';video.write_bytes(b'original')
    source=tmp_path/'video.srt';source.write_text('1\n00:00:00,000 --> 00:00:01,000\n测试\n',encoding='utf-8')
    out=tmp_path/'out';out.mkdir()
    seen=[]
    def execute(args,*rest):
        seen.append(args);Path(args[args.index('-o')+1]).write_bytes(b'video'*200)
    monkeypatch.setattr(app,'execute',execute)
    monkeypatch.setattr(app.probe,'video_size',lambda _: (128,128))
    st=dict(app.subtitle.DEFAULTS,optimize=False,translate=False,style='')
    dest=app.process_one(video,str(source),out,st,'字幕与带字幕视频',threading.Event(),lambda _:None)
    assert [a[0] for a in seen]==['synthesize']
    assert video.read_bytes()==b'original' and source.exists()
    assert (dest/'video_SUB.mp4').is_file()
    assert not (dest/'.work/caption.toml').exists()


def test_translation_is_an_explicit_stage_before_export(app,tmp_path,monkeypatch):
    video=tmp_path/'video.mp4';video.touch();source=tmp_path/'video.srt';source.write_text('original')
    out=tmp_path/'out';out.mkdir();seen=[]
    def execute(args,*rest):
        seen.append(args);Path(args[args.index('-o')+1]).write_text('translated')
    monkeypatch.setattr(app,'execute',execute)
    st=dict(app.subtitle.DEFAULTS,optimize=False,translate=True)
    dest=app.process_one(video,str(source),out,st,'只导出字幕',threading.Event(),lambda _:None)
    assert len(seen)==1 and seen[0][0]=='subtitle'
    assert '--no-optimize' in seen[0] and '--no-split' in seen[0]
    assert (dest/'video_处理.srt').read_text()=='translated'
    assert source.read_text()=='original'


def test_failed_job_removes_temporary_key_configuration(app,tmp_path,monkeypatch):
    out=tmp_path/'out';out.mkdir()
    def fail(*a):raise RuntimeError('fixture failure')
    monkeypatch.setattr(app,'execute',fail)
    with pytest.raises(RuntimeError):
        app.process_one(tmp_path/'v.mp4','',out,dict(app.subtitle.DEFAULTS),'只导出字幕',threading.Event(),lambda _:None)
    assert not list(out.rglob('caption.toml'))
