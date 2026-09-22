"""Build a standalone subtitle EXE without Studio's server or project resources."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
stage=ROOT/'build/subtitle-standalone'
(stage/'core').mkdir(parents=True,exist_ok=True)
(stage/'core/__init__.py').write_text('',encoding='utf-8')
for name in ('subtitle.py','captions.py','paths.py','probe.py','store.py'):
    shutil.copy2(ROOT/'core'/name,stage/'core'/name)
shutil.copy2(ROOT/'subtitle_app/main.py',stage/'main.py')
(stage/'字幕样式').mkdir(exist_ok=True)
for source in (ROOT/'字幕样式').glob('*.txt'):
    target=stage/'字幕样式'/source.name
    if not target.exists() or target.read_bytes()!=source.read_bytes():
        if target.exists():target.chmod(0o600)
        shutil.copy2(source,target)
cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--name','Respect-Subtitles',
     '--distpath',str(ROOT/'dist/subtitles'),'--workpath',str(stage/'pyinstaller'),
     '--specpath',str(stage),'--add-data',str(stage/'字幕样式')+';字幕样式',
     '--hidden-import','audioop','--hidden-import','GPUtil',
     '--collect-all','videocaptioner','--collect-all','imageio_ffmpeg',
     '--collect-all','PIL','--collect-all','certifi',
     '--exclude-module','PyQt5','--exclude-module','qfluentwidgets',
     '--exclude-module','qframelesswindow','--exclude-module','matplotlib',
     '--exclude-module','pytest','main.py']
subprocess.run(cmd,cwd=stage,check=True)
