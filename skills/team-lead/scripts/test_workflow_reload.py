"""F35-1: the documented reload (a new `Loaded:` line beside the original) must pass the validator, through the CLI."""
import hashlib
import unittest

import test_workflow_cuts as cuts

EARLY, LATE = "2026-10-17T09:59:00Z", "2026-10-17T11:00:00Z"
START_IMPLEMENT = "Check: start implement | passed | gate exit 0 at:2026-10-17T10:00:00Z\n"
# The Loaded loop runs for a state record (`workflow --gate`) and a receipt (`receipt --matt`, which also validates
# each griller receipt the same way); `brief --matt` runs validate_workflow with require_loads=False.
KINDS = ("workflow", "receipt")
RETURN = ("\n## Return\nCloses: 1\n### Result\nBuilt the fixture.\n### Claims\n"
          "- behavior: passed at the fixture digest\n### Evidence\nTest trace.\n")


class ReloadTests(cuts.StepFixture):
    """The fixture's custom procedure changes after the original read, so the original `Loaded:` line is stale."""

    def setUp(self):
        super().setUp()
        self.body = self.f.procedure
        self.old = self.f.procedure_digest
        self.original = f"Loaded: {self.body} | worker: sha256:{self.old} read event fixture-1 before work\n"
        self.body.write_text("A revised fixture procedure.\n")
        self.data = self.body.read_bytes()
        self.new = hashlib.sha256(self.data).hexdigest()

    def line(self, path=None, executor="worker", digest=None, size=None, pointer="reload after the change", at=None):
        extra = f"bytes:{len(self.data) if size is None else size} " if size is not False else ""
        return (f"Loaded: {path or self.body} | {executor}: sha256:{digest or self.new} {extra}{pointer}"
                + (f" at:{at}" if at else "") + "\n")

    def text(self, kind, *companions, original_at=None, checks=""):
        record = self.record([("x", "Scope: done"), ("x", "Grilling: done"), (" ", "Implement: next")],
                             cuts.NO_BRANCH + " at:2026-10-17T09:00:00Z", phase="implement", extra=checks)
        if kind == "receipt":
            record += RETURN + self.f.load_lines()
        stamped = self.original.replace("\n", f" at:{original_at}\n") if original_at else self.original
        text = (self.f.brief() + record) if kind == "receipt" else record
        self.assertIn(self.original, text)
        return text.replace(self.original, stamped + "".join(companions))

    def check(self, kind, text):
        if kind == "workflow":
            return self.f.run_check("workflow", text, "--gate", "entry")
        return self.f.run_check(kind, text, "--matt")

    def passes_both(self, *companions, **kwargs):
        for kind in KINDS:
            with self.subTest(kind):
                result = self.check(kind, self.text(kind, *companions, **kwargs))
                self.assertEqual(result.returncode, 0, result.stderr)

    def refused_both(self, *companions, needles=("Loaded body changed",), **kwargs):
        for kind in KINDS:
            with self.subTest(kind):
                result = self.check(kind, self.text(kind, *companions, **kwargs))
                self.assertEqual(result.returncode, 1, result.stderr)
                for needle in needles:
                    self.assertIn(needle, result.stderr)

    # The fix.
    def test_a_fresh_line_alone_passes(self):
        for kind in KINDS:
            with self.subTest(kind):
                result = self.check(kind, self.text(kind).replace(self.original, self.line()))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_fixture_record_without_the_reload_is_refused_as_stale(self):
        self.refused_both()

    def test_a_reload_beside_the_stale_original_passes(self):
        self.passes_both(self.line(size=False))

    def test_a_reload_with_a_correct_byte_count_passes(self):
        self.passes_both(self.line())

    def test_the_stale_original_may_follow_the_reload(self):
        for kind in KINDS:
            with self.subTest(kind):
                text = self.text(kind).replace(self.original, self.line() + self.original)
                self.assertEqual(self.check(kind, text).returncode, 0)

    def test_a_symlink_alias_of_the_same_body_counts(self):
        alias = self.f.root / "alias" / "SKILL.md"
        alias.parent.mkdir()
        alias.symlink_to(self.body)
        self.passes_both(self.line(path=alias))

    # What stays refused.
    def test_a_stale_line_alone_is_refused(self):
        self.refused_both()

    def test_a_companion_from_another_executor_does_not_clear_it(self):
        self.refused_both(self.line(executor="lead"), needles=("Loaded body changed", "Loaded needs"))

    def test_a_partial_companion_does_not_clear_it(self):
        self.refused_both(self.line(pointer="partial read of the body"),
                          needles=("Loaded body changed", "partial read"))

    def test_a_companion_with_the_wrong_byte_count_does_not_clear_it(self):
        self.refused_both(self.line(size=len(self.data) + 1), needles=("Loaded body changed", "byte count differs"))

    def test_a_companion_with_another_digest_does_not_clear_it(self):
        self.refused_both(self.line(digest="0" * 64))

    def test_a_lookalike_file_of_the_same_name_does_not_clear_it(self):
        other = self.f.root / "elsewhere" / "custom" / "SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_bytes(self.data)
        self.refused_both(self.line(path=other))

    def test_a_symlink_alias_of_another_body_does_not_clear_it(self):
        other = self.f.root / "elsewhere" / "SKILL.md"
        other.parent.mkdir()
        other.write_bytes(self.data)
        alias = self.f.root / "alias" / "SKILL.md"
        alias.parent.mkdir()
        alias.symlink_to(other)
        self.refused_both(self.line(path=alias))

    def test_a_companion_for_another_procedure_does_not_clear_it(self):
        entry = self.f.entry
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()
        self.refused_both(self.line(path=entry, digest=digest))

    # The timing rule is unchanged.
    def test_an_on_time_original_stays_on_time_after_a_late_reload(self):
        self.passes_both(self.line(at=LATE), original_at=EARLY, checks=START_IMPLEMENT)

    def test_a_reload_after_the_start_check_does_not_rescue_a_late_original(self):
        self.refused_both(self.line(at=LATE), original_at=LATE, checks=START_IMPLEMENT,
                          needles=("is stamped", "after", "start Check"))


if __name__ == "__main__":
    unittest.main()
