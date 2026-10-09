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

ROOT = Path(os.environ.get("BTC_RESEARCH_HOME", Path(__file__).resolve().parents[1])).resolve()
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
        return None
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
    return folder


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


def bootstrap():
    """One-time safe migration from a checkout with the OLD updater.

    Expected use from the *existing* checkout, after verifying origin:
        git fetch origin master
        git show origin/master:scripts/local_artifact_retention.py > /tmp/btc-retain.py
        BTC_RESEARCH_HOME="$PWD" python3 /tmp/btc-retain.py bootstrap

    This first migration must not use the old update_local_and_test.sh.
    """
    def git(*args, capture=False):
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True,
            stdout=subprocess.PIPE if capture else None,
            text=False,
        )
        return result.stdout if capture else None

    actual = Path(os.fsdecode(git("rev-parse", "--show-toplevel", capture=True)).strip()).resolve()
    if actual != ROOT:
        raise RuntimeError("Directorio no corresponde a la raíz Git esperada")
    branch = os.fsdecode(git("branch", "--show-current", capture=True)).strip()
    remote = os.fsdecode(git("remote", "get-url", "origin", capture=True)).strip()
    if branch != "master" or not (
        remote.endswith("isnardokun/btc-market-lab")
        or remote.endswith("isnardokun/btc-market-lab.git")
    ):
        raise RuntimeError("La migración exige master y origin isnardokun/btc-market-lab")
    if git("diff", "--cached", "--name-only", "-z", capture=True):
        raise RuntimeError("Existen cambios staged: detener sin modificar")
    changed = [
        os.fsdecode(x) for x in
        git("diff", "--name-only", "-z", capture=True).split(b"\0") if x
    ]
    if not all(allowed(path) for path in changed):
        raise RuntimeError("Existen cambios de código/documentos sin guardar: detener")
    # Backup before *any* working-tree edits or remote updates.
    folder = backup()
    try:
        if changed:
            git("restore", "--worktree", "--", *changed)
        git("fetch", "origin", "master")
        git("merge", "--ff-only", "origin/master")
    finally:
        if folder is not None:
            restore(str(folder))
    print("Migración segura completada. Commit instalado: " +
          os.fsdecode(git("rev-parse", "--short", "HEAD", capture=True)).strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore", "bootstrap"))
    parser.add_argument("--folder", help="Ruta del directorio de respaldo privado")
    args = parser.parse_args()
    if args.action == "backup":
        backup()
    elif args.action == "bootstrap":
        bootstrap()
    elif not args.folder:
        parser.error("restore requiere --folder")
    else:
        restore(args.folder)


if __name__ == "__main__":
    main()
