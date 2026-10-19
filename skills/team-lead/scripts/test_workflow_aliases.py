"""S35-1: a read is timed against the body it resolves to, not against the spelling of the path that names it."""
import os
import unittest

import test_workflow_cuts as cuts

START_IMPLEMENT = "Check: start implement | passed | gate exit 0 at:2026-10-17T10:00:00Z\n"
START_REVIEW = "Check: start review | passed | gate exit 0 at:2026-10-17T12:00:00Z\n"
EARLY, LATE = "2026-10-17T09:30:00Z", "2026-10-17T11:00:00Z"
RETURN = ("\n## Return\nCloses: 1\n### Result\nReviewed fixture.\n### Claims\n"
          "- behavior: passed at fixture identity\n### Evidence\n")
LATE_NEEDLES = ("after", "'implement'", "start Check")


class AliasTimingTests(cuts.StepFixture):
    """The canonical tdd body is read at LATE (or EARLY) and the record is at Phase review; implement started at 10:00."""

    def setUp(self):
        super().setUp()
        self.tdd = self.f.phase_skills[1]
        self.assertEqual(self.tdd.parent.name, "tdd")

    def relative_tdd(self):
        """The body spelled relative to the record's folder, resolved first (the stock TMPDIR sits behind /var)."""
        return os.path.relpath(self.tdd, self.f.root.resolve())

    def alias(self, relative, target=None):
        path = self.f.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target or self.tdd)
        return path

    def canonical_line(self, at):
        old = next(l for l in self.base().splitlines() if l.startswith(f"Loaded: {self.tdd} |"))
        return old, f"{old} at:{at}"

    def base(self, checks=START_IMPLEMENT, phase="review"):
        text = self.record(cuts.steps("Scope", "Grilling", "Implement", pending=("Review",)),
                           cuts.NO_BRANCH + " at:2026-10-17T09:00:00Z", phase=phase, extra=checks)
        return text

    def record_with(self, spelling, at, checks=START_IMPLEMENT, extra_lines=()):
        """The record whose tdd Loaded line names the body as `spelling`, stamped `at`, after any `extra_lines`."""
        text = self.base(checks)
        old, _ = self.canonical_line(at)
        line = old.replace(f"Loaded: {self.tdd} |", f"Loaded: {spelling} |") + f" at:{at}"
        return text.replace(old, "".join(l + "\n" for l in extra_lines) + line)

    def as_receipt(self, text):
        lines = text.splitlines()
        rest = "\n".join(l for l in lines if not l.startswith("Loaded: ")) + "\n"
        return self.f.brief() + rest + RETURN + "\n".join(l for l in lines if l.startswith("Loaded: ")) + "\n"

    def verdict(self, kind, text):
        if kind == "workflow":
            return self.f.run_check("workflow", text, "--gate", "entry")
        return self.f.run_check("receipt", self.as_receipt(text), "--matt")

    def refused(self, kind, text, spelling):
        """Refused as late for implement; the message names the read as the record spelled it."""
        result = self.verdict(kind, text)
        self.assertEqual(result.returncode, 1, result.stderr)
        for needle in (str(spelling),) + LATE_NEEDLES:
            self.assertIn(needle, result.stderr)

    def passes(self, kind, text):
        result = self.verdict(kind, text)
        self.assertEqual(result.returncode, 0, result.stderr)

    # The canonical controls keep their verdict.
    def test_the_canonical_late_read_is_refused_and_the_early_one_passes(self):
        for kind in ("workflow", "receipt"):
            for checks in (START_IMPLEMENT, START_IMPLEMENT + START_REVIEW):
                with self.subTest(kind=kind, starts=checks.count("Check:")):
                    self.refused(kind, self.record_with(self.tdd, LATE, checks), self.tdd)
                    self.passes(kind, self.record_with(self.tdd, EARLY, checks))

    # The defect: an alias named for another skill, or for no skill, moved the deadline.
    def test_an_alias_named_for_a_later_step_is_held_to_the_body_it_resolves_to(self):
        alias = self.alias("code-review/SKILL.md")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(alias, LATE, START_IMPLEMENT + START_REVIEW), alias)

    def test_a_renamed_alias_is_held_to_the_body_it_resolves_to(self):
        alias = self.alias("alias.md")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(alias, LATE), alias)

    def test_an_alias_line_before_the_canonical_line_does_not_choose_the_deadline(self):
        alias = self.alias("alias.md")
        _, canonical = self.canonical_line(LATE)
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(self.tdd, LATE, extra_lines=[
                    canonical.replace(f"Loaded: {self.tdd} |", f"Loaded: {alias} |")]), alias)

    def test_an_alias_named_for_the_same_skill_in_another_folder_is_still_late(self):
        alias = self.alias("elsewhere/tdd/SKILL.md")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with(alias, LATE), alias)

    def test_a_relative_alias_is_resolved_from_the_records_folder(self):
        self.alias("code-review/SKILL.md")
        for kind in ("workflow", "receipt"):
            with self.subTest(kind):
                self.refused(kind, self.record_with("code-review/SKILL.md", LATE, START_IMPLEMENT + START_REVIEW),
                             "code-review/SKILL.md")
                relative = self.relative_tdd()
                self.refused(kind, self.record_with(relative, LATE), relative)

    # An alias must not make a read worse than the same read at the canonical path.
    def test_an_early_read_passes_under_any_spelling(self):
        aliases = [self.alias("code-review/SKILL.md"), self.alias("alias.md"), "code-review/SKILL.md"]
        for kind in ("workflow", "receipt"):
            for spelling in aliases:
                with self.subTest(kind=kind, spelling=str(spelling)):
                    self.passes(kind, self.record_with(spelling, EARLY, START_IMPLEMENT + START_REVIEW))

    def test_a_late_reload_beside_an_on_time_read_of_the_same_body_passes_under_any_spelling(self):
        alias = self.alias("alias.md")
        _, late = self.canonical_line(LATE)
        for kind in ("workflow", "receipt"):
            for spelling in (alias, "alias.md", self.relative_tdd()):
                with self.subTest(kind=kind, spelling=str(spelling)):
                    reload = late.replace(f"Loaded: {self.tdd} |", f"Loaded: {spelling} |")
                    self.passes(kind, self.record_with(self.tdd, EARLY, extra_lines=[reload]))

    # The role still comes from the body, not from the neighbouring spelling.
    def test_an_alias_named_tdd_that_resolves_to_code_review_keeps_the_review_deadline(self):
        review = self.f.phase_skills[2]
        alias = self.alias("tdd/SKILL.md", review)
        for at, code in (("2026-10-17T11:00:00Z", 0), ("2026-10-17T12:30:00Z", 1)):
            with self.subTest(read_at=at):
                text = self.base(START_IMPLEMENT + START_REVIEW)
                old = next(l for l in text.splitlines() if l.startswith(f"Loaded: {review} |"))
                text = text.replace(old, old.replace(f"Loaded: {review} |", f"Loaded: {alias} |") + f" at:{at}")
                result = self.verdict("workflow", text)
                self.assertEqual(result.returncode, code, result.stderr)
                if code:
                    for needle in ("after", "'review'", "start Check"):
                        self.assertIn(needle, result.stderr)


if __name__ == "__main__":
    unittest.main()
