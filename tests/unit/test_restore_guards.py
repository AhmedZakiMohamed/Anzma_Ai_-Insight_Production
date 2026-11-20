import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "restore_verify.sh"


class RestoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.log"
        self._tool("psql", '#!/bin/sh\nexit 0\n')
        self._tool("pg_restore", '#!/bin/sh\nexit 0\n')
        self._tool("docker", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.calls}"\nexit 0\n')
        self.archive = self.root / "anzma_20261119_120000.dump"
        self.archive.write_bytes(b"test archive")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        Path(f"{self.archive}.sha256").write_text(f"{digest}  {self.archive.name}\n")
        Path(f"{self.archive}.rowcounts.txt").write_text("telemetry_records=1\n")
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "ANZMA_BACKUP_MODE": "host",
            "ANZMA_RESTORE_SCRATCH_DB": "anzma_restore_check_test",
            "PGUSER": "postgres",
            "PGDATABASE": "anzma",
        })

    def tearDown(self):
        self.temp.cleanup()

    def _tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def _run(self, *args, env=None):
        return subprocess.run(
            [str(SCRIPT), *args], cwd=ROOT, env=env or self.env,
            capture_output=True, text=True, check=False,
        )

    def test_database_name_refusals_exit_two_without_database_commands(self):
        env = self.env.copy()
        env["PGDATABASE"] = "anzma_restore_check_test"
        env["ANZMA_RESTORE_SCRATCH_DB"] = "anzma_restore_check_test"
        equal = self._run("--backup", str(self.archive), env=env)
        self.assertEqual(equal.returncode, 2)
        self.assertIn("must differ", equal.stderr)

        env["ANZMA_RESTORE_SCRATCH_DB"] = "unsafe_scratch"
        prefix = self._run("--backup", str(self.archive), env=env)
        self.assertEqual(prefix.returncode, 2)
        self.assertIn("must start with anzma_restore_check", prefix.stderr)
        self.assertFalse(self.calls.exists())

    def test_missing_archive_exits_one_before_database_access(self):
        result = self._run("--backup", str(self.root / "anzma_20261119_130000.dump"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or empty", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_dry_run_is_read_only_and_reports_plan(self):
        before = sorted(path.name for path in self.root.iterdir())
        result = self._run("--backup", str(self.archive), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["dry_run"], 1)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
