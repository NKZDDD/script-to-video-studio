"""Create an allowlisted deployment zip, never package databases or local secrets."""
import argparse
import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TOP = {"README.md", "API.md", "ADMIN-UPGRADE.md", "VALIDATION.md", "requirements.txt", "requirements-test.txt", "pytest.ini"}
TREES = {"studio_auth", "deploy", "examples", "tests", "tools"}
SUFFIXES = {".py", ".sh", ".service", ".timer", ".conf", ".html", ".css", ".js"}


def build(destination):
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if len(relative.parts) == 1 and path.name in TOP:
            files.append((relative.as_posix(), path.read_bytes()))
        elif relative.parts[0] in TREES and path.suffix in SUFFIXES and "__pycache__" not in relative.parts:
            files.append((relative.as_posix(), path.read_bytes()))
    manifest = "".join(hashlib.sha256(data).hexdigest()+"  "+name+"\n" for name, data in files)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            entry = zipfile.ZipInfo("respect-studio-auth/"+name)
            entry.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("respect-studio-auth/SHA256SUMS", manifest, compress_type=zipfile.ZIP_DEFLATED)
    return {"file": str(destination), "files": len(files)+1, "bytes": Path(destination).stat().st_size,
            "sha256": hashlib.sha256(Path(destination).read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(build(args.destination))
