import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("review-snapshot.py")
SPEC = importlib.util.spec_from_file_location("review_snapshot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        (self.repo / "app.txt").write_text("original\n")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "fixture")

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def read_snapshot(self, paths=()):
        return MODULE.snapshot(self.repo, "main", paths)

    def test_empty_scope_is_honest(self):
        self.assertFalse(self.read_snapshot()["reviewable"])

    def test_uncommitted_work_is_reviewable_with_unchanged_head(self):
        head = self.run_git("rev-parse", "HEAD").decode().strip()
        (self.repo / "app.txt").write_text("new behavior\n")
        result = self.read_snapshot()
        self.assertEqual(result["head"], head)
        self.assertTrue(result["reviewable"])
        self.assertEqual(result["workingTreePaths"], ["app.txt"])
        self.assertEqual(result["files"]["app.txt"]["sha256"],
                         "b6ef043d5cf71cde0488c1dc0e0689ab8a57c3a287e00ae019f2893700d50e58")

    def test_staged_and_working_versions_are_distinguished(self):
        (self.repo / "app.txt").write_text("staged\n")
        self.run_git("add", "app.txt")
        (self.repo / "app.txt").write_text("working\n")
        result = self.read_snapshot()
        self.assertEqual(result["indexWorktreeDifferPaths"], ["app.txt"])
        patch = subprocess.run(result["reviewCommands"]["index"], shell=True, check=True, capture_output=True).stdout
        self.assertIn(b"+staged", patch)
        self.assertNotIn(b"+working", patch)

    def test_untracked_requires_explicit_scope(self):
        (self.repo / "new.txt").write_text("new implementation\n")
        result = self.read_snapshot()
        self.assertEqual(result["omittedUntrackedPaths"], ["new.txt"])
        self.assertFalse(result["reviewable"])
        included = self.read_snapshot(["new.txt"])
        self.assertEqual(included["untrackedPaths"], ["new.txt"])
        self.assertTrue(included["reviewable"])

    def test_final_edit_invalidates_prior_review(self):
        (self.repo / "app.txt").write_text("first\n")
        before = self.read_snapshot()["snapshotSha256"]
        (self.repo / "app.txt").write_text("second\n")
        self.assertNotEqual(before, self.read_snapshot()["snapshotSha256"])

    def test_staging_changes_identity_even_with_same_working_bytes(self):
        (self.repo / "app.txt").write_text("new\n")
        before = self.read_snapshot()["snapshotSha256"]
        self.run_git("add", "app.txt")
        self.assertNotEqual(before, self.read_snapshot()["snapshotSha256"])

    def test_executable_mode_change_invalidates_review(self):
        path = self.repo / "app.txt"
        path.write_text("changed content\n")
        before = self.read_snapshot()["snapshotSha256"]
        path.chmod(path.stat().st_mode | 0o111)
        self.assertNotEqual(before, self.read_snapshot()["snapshotSha256"])

    def test_deleted_file_and_no_git_mutations(self):
        (self.repo / "app.txt").unlink()
        before = self.run_git("status", "--porcelain=v1")
        result = self.read_snapshot()
        self.assertEqual(result["files"]["app.txt"]["kind"], "deleted")
        self.assertEqual(before, self.run_git("status", "--porcelain=v1"))

    def test_cli_rejects_stale_snapshot(self):
        (self.repo / "app.txt").write_text("first\n")
        saved = self.repo / "snapshot.json"
        saved.write_text(json.dumps(self.read_snapshot(["app.txt"])))
        command = ["python3", "-B", str(SCRIPT), "--check", str(saved)]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        (self.repo / "app.txt").write_text("second\n")
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"STALE", result.stderr)


    def arm_hostile(self):
        """Config a reviewed member could write; each helper logs its name inside .git."""
        log, helper = self.repo / ".git/fired.log", self.repo / ".git/helper"
        helper.write_text(f'#!/bin/sh\necho "$1" >> {log}\n[ "$1" = clean ] && exec cat\nexit 1\n')
        helper.chmod(0o755)
        for key, value in (("filter.x.clean", f"{helper} clean"), ("filter.x.process", f"{helper} process"),
                           ("core.fsmonitor", f"{helper} fsmonitor"), ("diff.x.textconv", f"{helper} textconv"),
                           ("diff.x.command", f"{helper} command"), ("diff.external", f"{helper} external"),
                           ("core.checkStat", "minimal"), ("core.trustctime", "false")):
            self.run_git("config", key, value)
        (self.repo / ".git/info").mkdir(exist_ok=True)
        (self.repo / ".git/info/attributes").write_text("* filter=x diff=x\n")
        subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=20)
        self.assertTrue(log.exists(), "fixture not armed: plain git status ran no helper")
        log.unlink()
        return log

    def test_repository_helpers_never_run_and_edits_stay_visible(self):
        (self.repo / "app.txt").write_text("ORIGINAL\n")  # same size: only content can decide
        os.utime(self.repo / "app.txt", (1577836800, 1577836800))
        (self.repo / "staged.txt").write_text("staged\n")
        self.run_git("add", "staged.txt")
        log = self.arm_hostile()
        result = self.read_snapshot()
        index = subprocess.run(result["reviewCommands"]["index"], shell=True, capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=20)
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")
        self.assertEqual(result["workingTreePaths"], ["app.txt", "staged.txt"])
        self.assertEqual(result["indexWorktreeDifferPaths"], ["app.txt"])
        self.assertEqual(index.returncode, 0, index.stderr)
        self.assertIn(b"+staged", index.stdout)

    def test_only_helper_free_review_commands_are_offered(self):
        (self.repo / "app.txt").write_text("changed\n")
        result = self.read_snapshot()
        self.assertEqual(result["schemaVersion"], 2)
        self.assertEqual(set(result["reviewCommands"]), {"index"})
        for flag in ("core.fsmonitor=false", "--no-lazy-fetch", "--no-ext-diff", "--no-textconv", "--cached"):
            self.assertIn(flag, result["reviewCommands"]["index"])
        self.assertTrue(any("No working-tree diff command" in limit for limit in result["limits"]))

    def test_printed_index_command_starts_no_repository_pager_on_a_terminal(self):
        import pty
        (self.repo / "app.txt").write_text("staged\n")
        self.run_git("add", "app.txt")
        marker, pager = self.repo / ".git/pager-ran", self.repo / ".git/pager"
        pager.write_text(f"#!/bin/sh\ntouch {marker}\nexec cat\n")
        pager.chmod(0o755)
        self.run_git("config", "core.pager", str(pager))
        command = self.read_snapshot()["reviewCommands"]["index"]
        primary, secondary = pty.openpty()
        try:
            ran = subprocess.run(command, shell=True, stdin=subprocess.DEVNULL, stdout=secondary,
                                 stderr=secondary, timeout=20)
            output = os.read(primary, 65536)
        finally:
            os.close(primary)
            os.close(secondary)
        self.assertFalse(marker.exists(), "printed command started core.pager")
        self.assertEqual(ran.returncode, 0)
        self.assertIn(b"staged", output)

    def test_new_deleted_symlink_and_binary_changes_are_tracked(self):
        (self.repo / "bin.dat").write_bytes(b"\0\1\2")
        (self.repo / "link").symlink_to("app.txt")
        (self.repo / "gone.txt").write_text("gone\n")
        self.run_git("add", "bin.dat", "link", "gone.txt")
        self.run_git("commit", "-m", "more")
        (self.repo / "bin.dat").write_bytes(b"\0\1\3")
        (self.repo / "link").unlink()
        (self.repo / "link").symlink_to("elsewhere")
        (self.repo / "gone.txt").unlink()
        (self.repo / "new.txt").write_text("new\n")
        result = MODULE.snapshot(self.repo, "HEAD~1", ["bin.dat", "link", "gone.txt", "new.txt", "app.txt"])
        unchanged = MODULE.snapshot(self.repo, "HEAD", ["app.txt"])
        self.assertEqual(MODULE.snapshot(self.repo, "HEAD", ["bin.dat", "link", "gone.txt"])["workingTreePaths"],
                         ["bin.dat", "gone.txt", "link"])
        self.assertEqual(result["untrackedPaths"], ["new.txt"])
        self.assertEqual(result["files"]["link"], {"kind": "symlink",
                                                   "sha256": __import__("hashlib").sha256(b"elsewhere").hexdigest()})
        self.assertEqual(result["files"]["gone.txt"], {"kind": "deleted"})
        self.assertEqual(unchanged["workingTreePaths"], [])

    def test_unmerged_index_is_refused(self):
        self.run_git("checkout", "-q", "-b", "other")
        (self.repo / "app.txt").write_text("other\n")
        self.run_git("commit", "-qam", "other")
        self.run_git("checkout", "-q", "main")
        (self.repo / "app.txt").write_text("main\n")
        self.run_git("commit", "-qam", "main")
        subprocess.run(["git", "-C", str(self.repo), "merge", "-q", "other"], capture_output=True)
        with self.assertRaisesRegex(ValueError, "unmerged"):
            self.read_snapshot()

    def test_quoted_twin_of_a_path_is_hashed_as_itself(self):
        (self.repo / '"app.txt"').write_text("original\n")
        self.run_git("add", '"app.txt"')
        self.run_git("commit", "-m", "quoted twin")
        saved = self.repo / "snapshot.json"
        saved.write_text(json.dumps(self.read_snapshot(['"app.txt"'])))
        (self.repo / '"app.txt"').write_text("edited\n")
        self.assertEqual(self.read_snapshot()["workingTreePaths"], ['"app.txt"'])
        result = subprocess.run(["python3", "-B", str(SCRIPT), "--check", str(saved)], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"STALE", result.stderr)

    def test_newline_and_leading_dash_paths_are_tracked(self):
        names = ["two\nlines.txt", "-dash.txt"]
        for name in names:
            (self.repo / name).write_text("x\n")
        self.run_git("add", "--", *names)
        self.run_git("commit", "-m", "odd names")
        for name in names:
            (self.repo / name).write_text("y\n")
        self.assertEqual(self.read_snapshot()["workingTreePaths"], sorted(names))

    def test_paths_beyond_one_argument_batch_are_all_hashed(self):
        (self.repo / "b.txt").write_text("b\n")
        self.run_git("add", "b.txt")
        self.run_git("commit", "-m", "second file")
        (self.repo / "app.txt").write_text("edited\n")
        (self.repo / "b.txt").write_text("edited\n")
        budget = MODULE.ARGV_BUDGET
        self.addCleanup(setattr, MODULE, "ARGV_BUDGET", budget)
        MODULE.ARGV_BUDGET = 1
        self.assertEqual(self.read_snapshot()["workingTreePaths"], ["app.txt", "b.txt"])

    def test_cli_asks_for_a_new_snapshot_after_schema_change(self):
        saved = self.repo / "old.json"
        saved.write_text(json.dumps({**self.read_snapshot(["app.txt"]), "schemaVersion": 1}))
        result = subprocess.run(["python3", "-B", str(SCRIPT), "--check", str(saved)], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"schema", result.stderr)

if __name__ == "__main__":
    unittest.main()
