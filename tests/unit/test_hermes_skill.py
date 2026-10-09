#!/usr/bin/env python3
"""Regression tests for Hermes skill packaging and safe local installation."""
import os
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
