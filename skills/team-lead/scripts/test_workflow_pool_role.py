"""S39-1: a read's role comes from the canonical pool entry that resolves to its body, not from the body's folder name."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import test_workflow_aliases as aliases
import test_workflow_cuts as cuts

POOL = Path.home() / ".agents" / "skills"
START_IMPLEMENT, START_REVIEW = aliases.START_IMPLEMENT, aliases.START_REVIEW
EARLY, LATE = aliases.EARLY, aliases.LATE


class PoolRoleTests(aliases.AliasTimingTests):
    """The scratch HOME's pool links every real entry, except that `tdd` is a directory symlink to a folder named
    `versioned-procedure`: the pool name is `tdd`, the body's own folder is not."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.home = self.scratch / "home"
        self.pool = self.home / ".agents" / "skills"
        self.pool.mkdir(parents=True)
        self.external = self.scratch / "external" / "versioned-procedure"
        self.external.mkdir(parents=True)
        (self.external / "SKILL.md").write_bytes((POOL / "tdd" / "SKILL.md").read_bytes())
        for item in POOL.iterdir():
            if item.is_dir():
                (self.pool / item.name).symlink_to(self.external if item.name == "tdd" else item,
                                                   target_is_directory=True)
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        cuts.StepFixture.setUp(self)
        self.tdd = self.f.phase_skills[1]
        self.logical = self.pool / "tdd" / "SKILL.md"
        self.assertEqual(self.tdd, (self.external / "SKILL.md").resolve())
        self.assertEqual(self.logical.resolve(), self.tdd)

    def with_pool_entry(self, name, target):
        (self.pool / name).symlink_to(target, target_is_directory=True)

    def test_a_late_read_through_the_pool_entry_is_refused_and_the_early_one_passes(self):
        for kind in ("workflow", "receipt"):
            for checks in (START_IMPLEMENT, START_IMPLEMENT + START_REVIEW):
                with self.subTest(kind=kind, starts=checks.count("Check:")):
                    self.refused(kind, self.record_with(self.logical, LATE, checks), self.logical)
                    self.passes(kind, self.record_with(self.logical, EARLY, checks))

    def test_a_late_read_by_the_resolved_path_is_refused(self):
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(self.tdd, LATE), self.tdd)

    def test_pool_names_sharing_one_body_hold_the_read_to_the_earliest_governed_step(self):
        # code-review (governs review, 12:00) and tdd (governs implement, 10:00) are one body: the 10:00 deadline holds.
        (self.pool / "code-review").unlink()
        for extra in ("code-review", "aaa-first", "zzz-last"):
            self.with_pool_entry(extra, self.external)
            for kind in ("workflow", "receipt"):
                with self.subTest(extra=extra, kind=kind):
                    checks = START_IMPLEMENT + START_REVIEW
                    self.refused(kind, self.record_with(self.logical, LATE, checks), self.logical)
                    self.passes(kind, self.record_with(self.logical, EARLY, checks))

    def test_a_body_in_no_pool_entry_keeps_its_folder_name_role(self):
        outside = self.scratch / "outside" / "tdd"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("A body of the same name that the pool does not hold.\n")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                text = self.record_with(self.logical, EARLY).replace(
                    f"Loaded: {self.logical} |",
                    f"Loaded: {outside / 'SKILL.md'} | worker: sha256:{'0' * 64} read event fixture-outside at:{LATE}\n"
                    f"Loaded: {self.logical} |")
                result = self.verdict(kind, text)
                self.assertIn(str(outside / "SKILL.md"), result.stderr)
                self.assertIn("'implement'", result.stderr)

    def test_a_pool_entry_that_does_not_resolve_is_skipped(self):
        self.with_pool_entry("aaa-broken", self.scratch / "nowhere")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(self.logical, LATE), self.logical)
                self.passes(kind, self.record_with(self.logical, EARLY))

    def unlistable(self):
        """Make the pool searchable but not listable (0111) until cleanup; skip where the user can list it anyway."""
        self.addCleanup(self.pool.chmod, 0o755)
        self.pool.chmod(0o111)
        if os.access(self.pool, os.R_OK):
            self.skipTest("this user lists a 0111 directory")

    def test_a_pool_that_cannot_be_listed_is_refused_naming_the_pool_and_the_error(self):
        # S41-1: the role scan used to return no roles, so the late tdd read fell back to the folder name and passed.
        self.unlistable()
        for kind in ("workflow", "receipt"):
            for at in ("2026-10-17T08:30:00Z", EARLY, LATE):
                with self.subTest(kind=kind, at=at):
                    result = self.verdict(kind, self.record_with(self.logical, at))
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(str(self.pool), result.stderr)
                    self.assertIn("Permission denied", result.stderr)

    def test_the_same_reads_keep_their_verdicts_once_the_pool_can_be_listed_again(self):
        self.unlistable()
        self.pool.chmod(0o755)
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(self.logical, LATE), self.logical)
                self.passes(kind, self.record_with(self.logical, EARLY))

    def shared_body_with_a_blocked_tdd_wrapper(self):
        """code-review links straight to the body that tdd reaches through a wrapper folder (made 0000 by the caller)."""
        wrapper = self.scratch / "wrapper"
        wrapper.mkdir()
        (wrapper / "SKILL.md").symlink_to(self.external / "SKILL.md")
        (self.pool / "code-review").unlink()
        self.with_pool_entry("code-review", self.external)
        (self.pool / "tdd").unlink()
        self.with_pool_entry("tdd", wrapper)
        self.addCleanup(wrapper.chmod, 0o755)
        return wrapper

    def test_a_pool_entry_that_cannot_be_read_is_refused_naming_the_entry_not_skipped(self):
        # S43-1: tdd and code-review share one body; skipping the unreadable tdd entry left only review (12:00).
        wrapper = self.shared_body_with_a_blocked_tdd_wrapper()
        for kind in ("workflow", "receipt"):
            with self.subTest(kind=kind, mode="0755"):
                self.refused(kind, self.record_with(self.tdd, LATE, START_IMPLEMENT + START_REVIEW), self.tdd)
        wrapper.chmod(0)
        if os.access(wrapper, os.R_OK):
            self.skipTest("this user reads a 0000 directory")
        for kind in ("workflow", "receipt"):
            for at in (EARLY, LATE):
                with self.subTest(kind=kind, mode="0000", at=at):
                    result = self.verdict(kind, self.record_with(self.tdd, at, START_IMPLEMENT + START_REVIEW))
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(str(self.pool), result.stderr)
                    self.assertIn(wrapper.name, result.stderr)
                    self.assertIn("Permission denied", result.stderr)

    def test_a_pool_that_does_not_exist_is_not_reported_as_unlistable(self):
        self.pool.rename(self.pool.with_name("pool-held"))
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                result = self.verdict(kind, self.record_with(self.logical, LATE))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("cannot be read", result.stderr)

    # The alias cases of correction 37 are inherited and run again here against the linked pool.

if __name__ == "__main__":
    unittest.main()
