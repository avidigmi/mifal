"""Native solo-tool regressions. All generated records are synthetic tool tests."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_windows_copilot import windows


REPO = Path(__file__).resolve().parents[1]
VALIDATOR = REPO / "skills/team-lead/scripts/validate_contracts.py"
SPEC = importlib.util.spec_from_file_location("validator", VALIDATOR)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class RegularFiles(unittest.TestCase):
    def test_regular_text_and_digest_preserve_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.txt"
            body = b"one\r\ntwo\n"
            path.write_bytes(body)
            self.assertEqual(validator.regular_text(path).encode(), body)
            self.assertEqual(validator.workflow_digest(path), hashlib.sha256(body).hexdigest())

    def test_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(OSError):
                validator.regular_text(Path(temporary))

    @unittest.skipUnless(os.name == "nt", "Windows reparse points")
    def test_leaf_symlink_and_junction_parent_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            body = target / "body.txt"
            body.write_bytes(b"regular")
            junction = root / "junction"
            subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(junction), str(target)],
                           capture_output=True, check=True)
            try:
                with self.assertRaisesRegex(OSError, "reparse"):
                    validator.regular_text(junction / "body.txt")
            finally:
                junction.rmdir()
            alias = root / "alias.txt"
            try:
                alias.symlink_to(body)
            except OSError as error:
                self.skipTest(f"Symlink creation unavailable: {error}")
            with self.assertRaises(OSError):
                validator.regular_text(alias)

    def test_stream_is_closed_if_kind_check_fails(self):
        if os.name == "nt":
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "body.txt"
                path.write_bytes(b"bytes")
                original_close = os.close
                with patch("os.fdopen", side_effect=OSError("cannot wrap descriptor")), \
                        patch("os.close", wraps=original_close) as close:
                    with self.assertRaisesRegex(OSError, "cannot wrap"):
                        validator.regular_stream(path)
                close.assert_called_once()
                with self.assertRaises(OSError):
                    os.fstat(close.call_args[0][0])
        else:
            with tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(OSError):
                    validator.regular_stream(Path(temporary))

    @unittest.skipUnless(os.name == "nt", "Windows namespaces")
    def test_device_pipe_and_stream_paths_are_refused(self):
        for path in ("NUL", "CON", r"\\.\pipe\mifal-test", r"\\?\GLOBALROOT\Device\Null",
                     r"\\localhost\pipe\mifal-test", r"C:\file.txt:stream"):
            with self.subTest(path=path):
                result = subprocess.run(
                    [sys.executable, "-B", str(VALIDATOR), "read", path],
                    capture_output=True, timeout=5,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)
        with self.assertRaises(OSError):
            validator.regular_text("file.txt\0ignored")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_fifo_is_refused_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fifo"
            os.mkfifo(path)
            result = subprocess.run(
                [sys.executable, "-B", str(VALIDATOR), "read", str(path)],
                capture_output=True, timeout=5,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(b"not a regular file", result.stderr)


class InstalledTools(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home),
                    "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    def install(self):
        windows.install(self.home, REPO, False, lambda line: None)

    def command(self, *args):
        tool = self.home / windows.POOL / "team-lead/scripts/validate_contracts.py"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(tool), *map(str, args)],
            env=self.env, capture_output=True, text=True, timeout=30,
        )

    def test_installed_map_refuses_semantic_and_line_ending_edits(self):
        self.install()
        bodies = [self.home / windows.POOL / name / "SKILL.md"
                  for name in ("engineering-workflow", "ask-matt")]
        record = self.home / "synthetic-record.md"
        text = (
            "# Synthetic tool test, not real workflow evidence\n"
            "## Workflow\nMethodology: matt\nExecutor: synthetic\nRoute: research\nPhase: scope\n"
            "Next gate: test parser\nStart: none: synthetic tool test\n"
            "DDD: n/a: fixture\nUX: n/a: fixture\n" +
            "".join(f"Procedure: {p}\nLoaded: {p} | synthetic: sha256:"
                    f"{hashlib.sha256(p.read_bytes()).hexdigest()} synthetic full-read marker\n"
                    for p in bodies) +
            "\n## Phase checklist\n- [ ] Scope: synthetic tool test\n"
        )
        record.write_bytes(text.encode("utf-8"))
        passed = self.command("workflow", record, "--gate", "entry")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        original = bodies[1].read_bytes()
        for changed in (original + b"\nSemantic change.\n", original.replace(b"\n", b"\r\n")):
            bodies[1].write_bytes(changed)
            refused = self.command("workflow", record, "--gate", "entry")
            self.assertEqual(refused.returncode, 1, refused.stderr)
            self.assertIn("Ask Matt", refused.stderr)
            self.assertNotIn("Traceback", refused.stderr)
            self.assertEqual(windows.doctor(self.home, lambda line: None), 1)

    def test_installed_read_and_digest_commands(self):
        self.install()
        record = self.home / "synthetic-brief.md"
        body = b"# Synthetic\r\n- [x] item\r\n## Return\r\nignored\r\n"
        record.write_bytes(body)
        read = self.command("read", record)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn(hashlib.sha256(body).hexdigest(), read.stdout)
        brief = self.command("brief-digest", record)
        expected = hashlib.sha256(b"# Synthetic\n- [ ] item\n").hexdigest()
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertEqual(brief.stdout.strip(), "sha256:" + expected)
        record.write_bytes(b"## Design decisions\r\nSynthetic only.\r\n")
        design = self.command("design-digest", record)
        self.assertEqual(design.returncode, 0, design.stderr)
        expected = hashlib.sha256(b"## Design decisions\nSynthetic only.\n").hexdigest()
        self.assertEqual(design.stdout.strip(), "sha256:" + expected)

    def test_installed_public_workflow_contracts(self):
        self.install()
        scripts = self.home / windows.POOL / "team-lead/scripts"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "unittest",
             "test_workflow_contracts.WorkflowTests", "-q"],
            cwd=scripts, env=self.env, capture_output=True, text=True, timeout=240,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("Installed synthetic contracts: " + result.stderr.strip().replace("\n", " "))

    def test_installed_solo_helpers_and_selfcheck(self):
        self.install()
        checked = self.command("selfcheck")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        scripts = self.home / windows.POOL / "team-lead/scripts"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-q",
             "test_check_compliance", "test_resolve_role", "test_route_risk"],
            cwd=scripts, env=self.env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("Installed solo helpers: " + result.stderr.strip().replace("\n", " "))

    def test_payload_matches_the_unchanged_workflow_map_pin(self):
        files = windows.payload(REPO)
        ask = files[windows.POOL / "ask-matt/SKILL.md"]
        mapping = files[windows.POOL / "engineering-workflow/references/workflow-map.md"]
        pin = re.search(rb"Ask Matt body:.*?([a-f0-9]{64})", mapping).group(1).decode()
        self.assertEqual(hashlib.sha256(ask).hexdigest(), pin)
        self.assertNotIn(b"\r\n", ask)

    def test_installed_inventory_is_platform_independent(self):
        self.install()
        steward = self.home / windows.POOL / "team-lead/stewardship/steward.py"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(steward), "check", "--working-tree"],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        inventory = steward.parent / "generated/skill-inventory.json"
        before = json.loads(inventory.read_bytes())
        rebuilt = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(steward), "build"],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
        after = json.loads(inventory.read_bytes())
        before.pop("generatedAt")
        after.pop("generatedAt")
        self.assertEqual(after, before)


class WindowsSnapshot(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows command and mode semantics")
    def test_snapshot_batches_arguments_and_uses_powershell_quoting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "a repo's \u2018\u2019\u201a\u201b & files"
            root.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], check=True,
                                      capture_output=True, timeout=30)
            git("init", "-b", "main")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "core.autocrlf", "false")
            git("config", "core.hooksPath", str(root / "no-hooks"))
            for number in range(750):
                (root / f"long synthetic file name for argv budgeting {number:04}.txt").write_bytes(b"before\n")
            git("add", ".")
            git("update-index", "--chmod=+x", "long synthetic file name for argv budgeting 0000.txt")
            git("commit", "-m", "synthetic fixture")
            command = [sys.executable, "-B", str(REPO / "tools/review-snapshot.py"),
                       "--repo", str(root), "--base", "HEAD"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            self.assertFalse(snapshot["reviewable"], snapshot["workingTreePaths"])
            preview = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 snapshot["reviewCommands"]["index"]],
                capture_output=True, timeout=30,
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)


if __name__ == "__main__":
    unittest.main()
