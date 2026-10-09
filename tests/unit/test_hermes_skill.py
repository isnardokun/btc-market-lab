#!/usr/bin/env python3
"""Regression tests for Hermes skill packaging and safe local installation."""
import os
import shutil
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "mercados-daily-pro" / "SKILL.md"
INSTALLER = ROOT / "scripts" / "install_hermes_skill.sh"
UPDATER = ROOT / "scripts" / "update_local_and_test.sh"


class HermesSkillTests(unittest.TestCase):
    def test_skill_has_required_manifest_and_workflows(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        for phrase in (
            "name: mercados-daily-pro",
            "description:",
            "metadata:",
            "hermes:",
            "update_local_and_test.sh",
            "publication_gate",
            "send_report.py --dry-run",
            "TELEGRAM_BOT_TOKEN",
            "No hacer force-push",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_shell_scripts_parse(self):
        for script in (INSTALLER, UPDATER, ROOT / "scripts" / "daily.sh"):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    cwd=ROOT, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_skill_installs_without_exposing_secrets(self):
        with tempfile.TemporaryDirectory() as scratch:
            env = os.environ.copy()
            env["HERMES_SKILLS_HOME"] = scratch
            first = subprocess.run(["bash", str(INSTALLER)], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            destination = Path(scratch) / "finance" / "mercados-daily-pro" / "SKILL.md"
            self.assertEqual(destination.read_bytes(), SKILL.read_bytes())
            # Reinstallation is idempotent when nothing changed.
            second = subprocess.run(["bash", str(INSTALLER)], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(list(destination.parent.glob("*.backup-*")), [])

    def test_local_runtime_artifact_retention_survives_git_untracking(self):
        """Preserve modified on-disk receipts, manifests, reports and snapshot."""
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts" / "local_artifact_retention.py",
                            root / "scripts" / "local_artifact_retention.py")
            paths = (
                "reports/portable/daily_report_2026-10-09.html",
                "reports/portable/daily_report_2026-10-09.html.manifest.json",
                "reports/deliveries/delivery.json",
                "dashboards/dashboard_standalone.html",
            )
            for name in paths:
                item = root / name
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(("versionada:" + name).encode())
            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=root, text=True,
                    capture_output=True, check=True,
                )
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Hermes Tests")
            git("add", "--", *paths)
            git("commit", "-qm", "old public runtime files")
            latest = {}
            for name in paths:
                payload = ("nuevo informe privado / no subir / " + name).encode()
                (root / name).write_bytes(payload)
                latest[name] = payload
            script = root / "scripts" / "local_artifact_retention.py"
            result = subprocess.run(
                [sys.executable, str(script), "backup"], cwd=root,
                capture_output=True, text=True, check=True,
            )
            folder = Path(result.stdout.strip())
            self.assertTrue(folder.is_dir())
            self.assertEqual(folder.parent, root / "reports" / "local-retained")
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            git("rm", "-q", "--", *paths)
            git("commit", "-qm", "stop tracking generated files")
            restored = subprocess.run(
                [sys.executable, str(script), "restore", "--folder", str(folder)],
                cwd=root, capture_output=True, text=True, check=True,
            )
            self.assertIn("4", restored.stdout)
            for name, expected in latest.items():
                self.assertEqual((root / name).read_bytes(), expected)
            self.assertEqual(git("ls-files", "--", *paths).stdout.strip(), "")
            self.assertEqual(subprocess.run(
                [sys.executable, str(script), "backup"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip(), "-")
            bad = subprocess.run(
                [sys.executable, str(script), "restore", "--folder", scratch],
                cwd=root, capture_output=True, text=True,
            )
            self.assertNotEqual(bad.returncode, 0)

    def test_install_preserves_local_customizations(self):
        with tempfile.TemporaryDirectory() as scratch:
            env = os.environ.copy()
            env["HERMES_SKILLS_HOME"] = scratch
            destination = Path(scratch) / "finance" / "mercados-daily-pro" / "SKILL.md"
            destination.parent.mkdir(parents=True)
            destination.write_text("custom previous skill", encoding="utf-8")
            res = subprocess.run(["bash", str(INSTALLER)], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            backups = list(destination.parent.glob("SKILL.md.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "custom previous skill")
            self.assertEqual(destination.read_bytes(), SKILL.read_bytes())


if __name__ == "__main__":
    unittest.main()
