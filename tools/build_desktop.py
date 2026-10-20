"""Build the native Studio + CLI companion in one shared onedir, then an NSIS installer."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.updates import VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--makensis", type=Path, required=True)
    parser.add_argument("--skip-freeze", action="store_true")
    parser.add_argument("--bootstrapper", type=Path)
    args = parser.parse_args()
    build = args.build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    spec = build / "StudioDesktop.spec"
    # Reuse the existing release requirements and checks; keep all providers.
    legacy_spec = importlib.util.spec_from_file_location("studio_packaging", ROOT / "打包exe.py")
    legacy = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy)
    missing = [m for m in [*legacy.REQUIRED, "webview"] if importlib.util.find_spec(m) is None]
    if missing:
        raise RuntimeError("Missing dependencies: " + ",".join(missing))
    if not legacy.check_page():
        raise RuntimeError("Page syntax check failed")
    text = f"""
from PyInstaller.utils.hooks import collect_all, collect_submodules
from pathlib import Path
root = Path({str(ROOT)!r})
datas, binaries, hiddenimports = [], [], {legacy.HIDDEN!r}
for package in {legacy.COLLECT_ALL!r}:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    # The hook directory is for the build machine, never a runtime import.
    hiddenimports += [m for m in h if not m.startswith('webview.__pyinstaller')]
hiddenimports += collect_submodules('core.providers')
datas += [(str(root / p), p) for p in ['web','prompts','skills/production-skill']]
if (root / 'model-snapshot.json').is_file():
    datas += [(str(root / 'model-snapshot.json'), '.')]
a = Analysis([str(root/'run.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports,
             excludes={legacy.EXCLUDE + ['PySide2','PySide6','PyQt6','qtpy','django']!r})
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Respect-Studio', console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Respect-Studio-CLI', console=True)
out = COLLECT(gui, cli, a.binaries, a.datas, name='Respect-Studio')
"""
    spec.write_text(text, encoding="utf-8")
    temp = build / "temp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp),
               PYINSTALLER_CONFIG_DIR=str(build / "cache"), PYTHONIOENCODING="utf-8")
    if not args.skip_freeze:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--distpath", str(build / "dist"), "--workpath", str(build / "work"),
                        str(spec)], cwd=ROOT, env=env, check=True)
    payload = build / "dist/Respect-Studio"
    # Runtime and --no-browser tests run through the companion with real stdout.
    if not legacy.selfcheck(str(payload / "Respect-Studio-CLI.exe")):
        raise RuntimeError("Packaged runtime check failed")
    remove = build / "uninstall-files.nsh"
    entries = sorted(p for p in payload.rglob("*") if p.is_file())
    lines = ['Delete "$INSTDIR\\' + str(p.relative_to(payload)).replace("/", "\\") + '"' for p in entries]
    dirs = sorted((p for p in payload.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    lines += ['RMDir "$INSTDIR\\' + str(p.relative_to(payload)).replace("/", "\\") + '"' for p in dirs]
    remove.write_text("\n".join(lines), encoding="utf-8-sig")
    output = build / ("Respect-Studio-Setup-" + VERSION + ".exe")
    bootstrapper = (args.bootstrapper or build / "tools/MicrosoftEdgeWebview2Setup.exe").resolve()
    if not bootstrapper.is_file():
        raise RuntimeError("Download Microsoft's WebView2 bootstrapper and pass --bootstrapper")
    # This script is build-time only; never accept an arbitrary unsigned dependency.
    verify_env = dict(env, STUDIO_WEBVIEW_BOOTSTRAPPER=str(bootstrapper))
    subprocess.run(["powershell", "-NoProfile", "-Command",
        "$s=Get-AuthenticodeSignature -LiteralPath $env:STUDIO_WEBVIEW_BOOTSTRAPPER; "
        "if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 1}"],
        env=verify_env, check=True)
    subprocess.run([str(args.makensis), "/INPUTCHARSET", "UTF8",
                    "/DVERSION=" + VERSION, "/DPAYLOAD=" + str(payload),
                    "/DOUTPUT=" + str(output), "/DREMOVE_LIST=" + str(remove), "/DBOOTSTRAPPER=" + str(bootstrapper),
                    str(ROOT / "tools/studio-installer.nsi")], env=env, check=True)
    with output.open("rb") as fp:
        checksum = hashlib.file_digest(fp, "sha256").hexdigest()
    release = {"version": VERSION, "flavor": "all",
               "url": "https://login.respect.chat/downloads/" + output.name,
               "sha256": checksum, "bytes": output.stat().st_size,
               "notes": "独立桌面窗口、安装器与软件更新；保留生产配置和项目，移除内置字幕功能。"}
    (build / "release.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".exe.sha256").write_text(checksum + "  " + output.name + "\n", encoding="ascii")
    print(json.dumps(release, ensure_ascii=False))


if __name__ == "__main__":
    main()
