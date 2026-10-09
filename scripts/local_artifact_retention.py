#!/usr/bin/env python3
"""Preserve local runtime artifacts when a public Git commit stops tracking them.

Only whitelist paths used by the report publisher and Telegram idempotency
receipts. Backups stay inside an explicitly ignored, mode-0700 local folder.
No network calls, Git push, database mutations, or report deletion.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / "reports" / "local-retained"
PATHSPECS = (
    "reports/portable",
    "reports/deliveries",
    "dashboards/dashboard_standalone.html",
)


def allowed(relative):
    if not isinstance(relative, str) or relative.startswith("/"):
        return False
    if any(part in ("", ".", "..") for part in relative.split("/")):
        return False
    return (relative.startswith("reports/portable/")
            or relative.startswith("reports/deliveries/")
            or relative == "dashboards/dashboard_standalone.html")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_artifacts():
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", *PATHSPECS], cwd=ROOT,
    )
    names = [os.fsdecode(part) for part in raw.split(b"\0") if part]
    if not all(allowed(name) for name in names):
        raise RuntimeError("Git devolvió una ruta fuera de la lista permitida")
    return names


def backup():
    originals = []
    for relative in tracked_artifacts():
        src = ROOT / relative
        if src.is_symlink():
            raise RuntimeError("Archivo generado es symlink: " + relative)
        if src.is_file():
            originals.append((relative, src))
    if not originals:
        print("-")
        return
    RETAINED.mkdir(parents=True, exist_ok=True, mode=0o700)
    RETAINED.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(tempfile.mkdtemp(prefix=stamp + "-", dir=RETAINED))
    folder.chmod(0o700)
    manifest = {}
    for relative, source in originals:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        original_hash = sha256(source)
        if sha256(target) != original_hash:
            raise RuntimeError("Copia no coincide con el original: " + relative)
        manifest[relative] = original_hash
    (folder / "manifest.json").write_text(
        json.dumps({"files": manifest}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(str(folder))


def restore(folder_name):
    folder = Path(folder_name).resolve()
    if not folder.is_relative_to(RETAINED.resolve()) or folder == RETAINED.resolve():
        raise RuntimeError("Backup fuera de reports/local-retained/")
    payload = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Manifest de respaldo vacío o inválido")
    # Verify *every* backed-up byte before touching any destination.
    for relative, expected in files.items():
        if not allowed(relative):
            raise RuntimeError("Ruta de restauración no permitida")
        source = folder / relative
        if source.is_symlink() or not source.is_file() or sha256(source) != expected:
            raise RuntimeError("Respaldo incompleto o modificado: " + relative)
        dest = ROOT / relative
        if dest.is_symlink() or any(part.is_symlink() for part in dest.parents if part != ROOT):
            raise RuntimeError("Destino de restauración es un symlink")
    for relative, expected in files.items():
        source = folder / relative
        dest = ROOT / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or sha256(dest) != expected:
            shutil.copy2(source, dest)
        if sha256(dest) != expected:
            raise RuntimeError("Restauración no coincide: " + relative)
    print("Artefactos locales restaurados: " + str(len(files)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--folder", help="Ruta del directorio de respaldo privado")
    args = parser.parse_args()
    if args.action == "backup":
        backup()
    elif not args.folder:
        parser.error("restore requiere --folder")
    else:
        restore(args.folder)


if __name__ == "__main__":
    main()
