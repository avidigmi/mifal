"""Exercise the workflow map and its hold on Ask Matt's body through the public validator CLI."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("validate_contracts.py")
POOL = Path.home() / ".agents" / "skills"
MAP = Path("engineering-workflow") / "references" / "workflow-map.md"

# The shipped set (Mifal's plan D9). Written out here on purpose: the validator carries its own copy, and this one is the oracle.
ASK_MATT_ROWS = (
    "ask-matt", "grill-with-docs", "grill-me", "grilling", "domain-modeling", "prototype", "handoff",
    "to-spec", "to-tickets", "implement", "implement-spec", "tdd", "code-review", "pr", "retro", "triage",
    "diagnosing-bugs", "wayfinder", "improve-codebase-architecture", "codebase-design", "research",
    "to-questionnaire", "wizard", "wait-what", "teach", "writing-for-agents", "setup-matt-pocock-skills",
)
LOCAL_ATTACHMENTS = (
    "receiving-code-review", "ddd", "ddd-scope", "ddd-ground", "ddd-verify",
    "frontend-design", "agent-browser",
)
FACTORY_CORE = ("engineering-workflow", "team-lead", "herdr-adapter", "validate-contracts", "setup-pre-commit")
FACTORY_STARTS = ("operational or one-off task", "team coordination")


def table_rows(text):
    """Every data row of every pipe table in `text`, as a list of stripped cell lists."""
    rows = []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                rows.append(cells)
    return rows


class MapFixture(unittest.TestCase):
    """A scratch HOME whose pool holds copies of Ask Matt, the entry and the map, so a test can change them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.pool = self.home / ".agents" / "skills"
        for rel in (Path("ask-matt") / "SKILL.md", Path("engineering-workflow") / "SKILL.md", MAP):
            if (POOL / rel).is_file():
                (self.pool / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.pool / rel).write_bytes((POOL / rel).read_bytes())
        self.record = self.root / "state.md"

    def digest(self, name):
        return hashlib.sha256((self.pool / name / "SKILL.md").read_bytes()).hexdigest()

    def state(self):
        router, entry = (self.pool / name / "SKILL.md" for name in ("ask-matt", "engineering-workflow"))
        return ("## Workflow\nMethodology: matt\nExecutor: worker\nRoute: research\nPhase: scope\n"
                "Next gate: sources named\nStart: none: no code baseline\n"
                "DDD: n/a: no unfamiliar API\nUX: n/a: no journey change\n"
                f"Procedure: {router}\nProcedure: {entry}\n"
                f"Loaded: {router} | worker: sha256:{self.digest('ask-matt')} read event fixture-router\n"
                f"Loaded: {entry} | worker: sha256:{self.digest('engineering-workflow')} read event fixture-entry\n"
                "\n## Phase checklist\n- [ ] Scope: questions named\n")

    def brief(self):
        return ("---\ntask: 1\nowner: worker\nlatitude: look\nreturn: task.md\n---\n# Task 1\n"
                "## Why\nInspect a change.\n## Outcome\nEvidence-backed review.\n"
                "## Done means\n- behavior: check the artifact.\n## Stop rule\nOne pass.\n")

    def run_check(self, kind, text, *args):
        self.record.write_text(text)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), kind, str(self.record), *args],
            capture_output=True, text=True, env={**os.environ, "HOME": str(self.home)})

    def gate(self, gate="entry"):
        return self.run_check("workflow", self.state(), "--gate", gate)

    def edit_map(self, old, new):
        map_file = self.pool / MAP
        text = map_file.read_text()
        self.assertEqual(text.count(old), 1, old)
        map_file.write_text(text.replace(old, new))

    def hash_line(self):
        return f"Ask Matt body: sha256:{self.digest('ask-matt')}"

    def row(self, name):
        lines = [line for line in (self.pool / MAP).read_text().splitlines() if line.startswith(f"| {name} |")]
        self.assertEqual(len(lines), 1, name)
        return lines[0]

    def refused(self, *needles):
        result = self.gate()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow-map.md", result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)

    def change_ask_matt(self):
        body = self.pool / "ask-matt" / "SKILL.md"
        body.write_text(body.read_text() + "\nA new paragraph from upstream.\n")


class AskMattHashTests(MapFixture):
    def test_record_passes_while_the_map_hash_matches_the_installed_body(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_ask_matt_body_holds_the_gate_and_names_the_map(self):
        self.change_ask_matt()
        result = self.gate()  # the record's Loaded digest follows the new body, as after a reload
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow-map.md", result.stderr)
        self.assertIn("Ask Matt body", result.stderr)

    def test_the_hold_covers_the_brief_check_before_dispatch(self):
        self.change_ask_matt()
        result = self.run_check("brief", self.brief() + "\n" + self.state(), "--matt")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow-map.md", result.stderr)

    def test_updating_the_map_hash_after_review_clears_the_hold(self):
        self.change_ask_matt()
        map_file = self.pool / MAP
        map_file.write_text(re.sub(r"(?m)^Ask Matt body: sha256:[0-9a-f]{64}$",
                                   f"Ask Matt body: sha256:{self.digest('ask-matt')}", map_file.read_text()))
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)


class MapAvailabilityTests(MapFixture):
    def test_missing_map_holds_the_gate(self):
        (self.pool / MAP).unlink()
        result = self.gate()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("workflow map unavailable", result.stderr)
        self.assertIn("workflow-map.md", result.stderr)

    def test_map_must_record_exactly_one_well_formed_hash_line(self):
        for label, new in (("absent", ""),
                           ("short digest", "Ask Matt body: sha256:abc123"),
                           ("repeated", self.hash_line() + "\n" + self.hash_line()),
                           ("only in a fence", "```\n" + self.hash_line() + "\n```")):
            with self.subTest(label):
                self.setUp()
                self.edit_map(self.hash_line(), new)
                result = self.gate()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("exactly one 'Ask Matt body:", result.stderr)
                self.assertIn("workflow-map.md", result.stderr)


class MapCoverageTests(MapFixture):
    def test_a_missing_row_names_the_shipped_skill(self):
        for name in ("wayfinder", "receiving-code-review", "ddd-verify", "agent-browser",
                     "herdr-adapter", "operational or one-off task"):
            with self.subTest(name):
                self.setUp()
                self.edit_map(self.row(name) + "\n", "")
                self.refused(f"no row for shipped skill '{name}'")

    def test_a_row_for_a_skill_that_is_not_shipped_is_refused(self):
        self.setUp()
        grilling = self.row("grilling")
        self.edit_map(grilling, grilling + "\n" + grilling.replace("| grilling |", "| cloudflare |", 1))
        self.refused("'cloudflare'", "not in the shipped set")

    def test_a_skill_listed_twice_is_refused(self):
        self.setUp()
        grilling = self.row("grilling")
        self.edit_map(grilling, grilling + "\n" + grilling)
        self.refused("'grilling'", "more than once")


class MapPlacementTests(MapFixture):
    def review_new_ask_matt(self, change):
        """Change the installed Ask Matt body, then pin the map to it, as a reviewer who left the rows alone would."""
        body = self.pool / "ask-matt" / "SKILL.md"
        body.write_text(change(body.read_text()))
        map_file = self.pool / MAP
        map_file.write_text(re.sub(r"(?m)^Ask Matt body: sha256:[0-9a-f]{64}$",
                                   f"Ask Matt body: sha256:{self.digest('ask-matt')}", map_file.read_text()))

    def test_an_ask_matt_row_must_be_a_skill_the_installed_body_names(self):
        self.review_new_ask_matt(lambda text: text.replace("/wayfinder", "wayfinder"))
        self.refused("'wayfinder'", "body does not name /wayfinder")

    def test_a_factory_row_must_not_restate_a_skill_ask_matt_names(self):
        self.review_new_ask_matt(lambda text: text + "\n- **`/setup-pre-commit`**: new upstream placement.\n")
        self.refused("'setup-pre-commit'", "names /setup-pre-commit")

    def test_an_attachment_must_attach_to_an_ask_matt_row(self):
        row = self.row("receiving-code-review")
        self.edit_map(row, row.replace("attaches: code-review", "attaches: no-such-row"))
        self.refused("'receiving-code-review'", "'no-such-row'", "not an Ask Matt row")

    def test_an_anchor_that_infers_a_branch_is_refused(self):
        for name, old, new in (
                ("grill-me", "ask-matt: Standalone", "if there is no working directory"),
                ("grill-me", "ask-matt: Standalone", "factory: Ask Matt is silent here"),
                ("ddd-scope", "attaches: research", "ask-matt: main flow step 1"),
                ("team-lead", "factory: team coordination", "attaches: code-review")):
            with self.subTest(name=name, new=new):
                self.setUp()
                row = self.row(name)
                self.edit_map(row, row.replace(old, new))
                self.refused(f"'{name}'", "Anchor")


class MapRowTests(MapFixture):
    def set_cell(self, name, index, value):
        row = self.row(name)
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        cells[index] = value
        self.edit_map(row, "| " + " | ".join(cells) + " |")

    def test_each_row_keeps_every_column_filled_with_a_real_value(self):
        for index, value, needle in (
                (1, "whenever convenient", "names no route"),
                (2, "somebody", "names neither the lead nor a worker"),
                (3, "", "empty or placeholder 'Invocation path'"),
                (4, "n/a", "empty or placeholder 'Dependencies'"),
                (5, "TBD", "empty or placeholder 'Evidence'")):
            with self.subTest(index=index):
                self.setUp()
                self.set_cell("tdd", index, value)
                self.refused("'tdd'", needle)

    def test_a_row_with_a_missing_cell_is_refused(self):
        row = self.row("tdd")
        self.edit_map(row, row.replace(" | codebase-design", "", 1))
        self.refused("'tdd'", "needs 7 cells")

    def test_the_table_header_must_name_the_seven_columns(self):
        self.edit_map("| Skill | Route and phase | Invoker | Invocation path | Dependencies | Evidence | Anchor |\n"
                      "|---|---|---|---|---|---|---|\n| ask-matt |",
                      "| Skill | Route | Invoker | Invocation path | Dependencies | Evidence | Anchor |\n"
                      "|---|---|---|---|---|---|---|\n| ask-matt |")
        self.refused("needs the columns Skill | Route and phase | Invoker")


class MapSectionTests(MapFixture):
    """The gate reads one section per table, so a second copy of an authoritative section is an ambiguous map."""

    SECTIONS = ("## Ask Matt rows", "## Local attachments", "## Factory section",
                "### Factory core", "### Starts Ask Matt does not cover")

    def block(self, heading):
        """The map text from `heading` to the next heading of the same or a higher level."""
        text = (self.pool / MAP).read_text()
        start = text.index("\n" + heading + "\n") + 1
        level = heading.split()[0]
        end = re.search(r"\n#{1,%d} " % len(level), text[start + len(heading):])
        return text[start:start + len(heading) + end.start() + 1] if end else text[start:]

    def append(self, text):
        map_file = self.pool / MAP
        map_file.write_text(map_file.read_text() + text)

    def test_a_second_ask_matt_rows_section_with_an_invented_row_is_refused(self):
        # The reviewer's probe (tasks/13/probe_map.py:duplicate): a same-shaped second section the gate never read.
        self.append("\n## Ask Matt rows\n\n"
                    "| Skill | Route and phase | Invoker | Invocation path | Dependencies | Evidence | Anchor |\n"
                    "|---|---|---|---|---|---|---|\n"
                    "| cloudflare | build: implement | lead | Skill tool | none | files | ask-matt: invented branch |\n")
        self.refused("'## Ask Matt rows'", "exactly one")

    def test_a_repeated_copy_of_each_authoritative_section_is_refused(self):
        for heading in self.SECTIONS:
            with self.subTest(heading):
                self.setUp()
                self.append("\n" + self.block(heading))
                self.refused(f"'{heading}'", "exactly one")

    def test_a_repeated_heading_is_refused_wherever_it_sits_and_however_it_is_cased(self):
        for label, extra in (("under another section", "\n## Appendix\n\n" + "{block}"),
                             ("lower case", "\n" + "{block}")):
            with self.subTest(label):
                self.setUp()
                block = self.block("### Factory core")
                self.append(extra.format(block=block.lower() if label == "lower case" else block))
                self.refused("'### Factory core'", "exactly one")

    def test_a_second_table_is_refused_under_the_same_section_or_a_different_heading(self):
        header = ("| Skill | Route | Invoker | Invocation path | Dependencies | Evidence | Anchor |\n"
                  "|---|---|---|---|---|---|---|\n"
                  "| cloudflare | build | lead | Skill tool | none | files | ask-matt: invented branch |")
        last = self.row("setup-matt-pocock-skills")
        for label, edit in (("same section", lambda: self.edit_map(last, last + "\n\nA second table:\n\n" + header)),
                            ("different heading", lambda: self.append("\n## Ask Matt rows (old)\n\n" + header + "\n"))):
            with self.subTest(label):
                self.setUp()
                edit()
                self.refused("tables", "one under each")

    def test_a_heading_inside_a_fence_is_an_example_and_does_not_repeat_a_section(self):
        self.append("\n```\n## Ask Matt rows\n```\n")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    # S19-1 (tasks/19/review.md): the variants below are what a GFM reader renders as a second authoritative section
    # or table, but not in the map's own spelling. The reviewer's fixtures are tasks/19/probe_variants.py.
    ROW = "cloudflare | build: implement | lead | Skill tool | none | files | ask-matt: invented branch"
    HEADER = "Skill | Route and phase | Invoker | Invocation path | Dependencies | Evidence | Anchor"
    NO_OUTER_PIPES = HEADER + "\n--- | --- | --- | --- | --- | --- | ---\n" + ROW + "\n"
    PIPED = "".join(f"| {line} |\n" for line in (HEADER, " | ".join(["---"] * 7), ROW))

    def test_a_second_section_or_table_in_another_gfm_spelling_is_refused(self):
        tables = ("tables", "one under each")
        sections = ("'## Ask Matt rows'", "exactly one")
        for label, add, needles in (
                ("v1 other heading, no outer pipes", "\n## Ask Matt rows (old)\n\n" + self.NO_OUTER_PIPES, tables),
                ("v2 indented heading, no outer pipes", "\n ## Ask Matt rows\n\n" + self.NO_OUTER_PIPES, sections + tables),
                ("v3 closing hashes, no outer pipes", "\n## Ask Matt rows ##\n\n" + self.NO_OUTER_PIPES, sections + tables),
                ("v4 setext heading, no outer pipes", "\nAsk Matt rows\n-------------\n\n" + self.NO_OUTER_PIPES,
                 sections + tables),
                ("v6 one-hyphen delimiter", "\n## Ask Matt rows (old)\n\n" + self.PIPED.replace("---", "-"), tables),
                ("v7 indented table", "\n## Ask Matt rows (old)\n\n" + "".join(" " + l + "\n" for l in self.PIPED.splitlines()),
                 tables),
                ("setext with equals, a level 1 heading", "\nAsk Matt rows\n=============\n\n" + self.PIPED, sections + tables),
                ("a repeated sub-section, indented and closed", "\n  ### Factory core ###\n\n" + self.PIPED,
                 ("'### Factory core'", "exactly one"))):
            with self.subTest(label):
                self.setUp()
                self.append(add)
                self.refused(*needles)

    def test_a_second_table_without_outer_pipes_inside_the_real_section_is_refused(self):
        # v5: the second table sits under the one real "Ask Matt rows" heading.
        self.setUp()
        last = self.row("setup-matt-pocock-skills")
        self.edit_map(last, last + "\n\nA second table:\n\n" + self.NO_OUTER_PIPES.rstrip("\n"))
        self.refused("tables", "one under each")

    def test_markdown_that_is_not_a_heading_or_a_table_repeats_nothing(self):
        # Guards against over-refusing: a four-space indent is a code block, a thematic break after a blank line is not a
        # setext underline, a delimiter-like line with no pipe is a thematic break, and a fenced heading is an example.
        self.append("\n    ## Ask Matt rows\n\n    " + self.HEADER + "\n    " + " | ".join(["---"] * 7) + "\n\n"
                    "---\n\nAsk Matt rows are listed above.\n\n***\n\n- - -\n\n    ---\n")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)


class ShippedMapTests(unittest.TestCase):
    def test_map_lists_each_shipped_skill_exactly_once(self):
        self.assertTrue((POOL / MAP).is_file(), f"workflow map missing: {POOL / MAP}")
        text = (POOL / MAP).read_text()
        names =[row[0] for row in table_rows(text) if row[0] not in ("Skill", "Start")]
        expected = ASK_MATT_ROWS + LOCAL_ATTACHMENTS + FACTORY_CORE + FACTORY_STARTS
        self.assertEqual(sorted(names), sorted(expected))


class ShippedRowTests(unittest.TestCase):
    """The shipped rows keep every path Ask Matt gives their skill; each comment quotes the Ask Matt sentence it restates.

    The validator proves structure, coverage, category and body freshness; whether a row's prose is right is a
    reviewer's job, so these pin the prose the reviewer found narrowed (tasks/13/review.md, S13-2)."""

    COLUMNS = ("Skill", "Route and phase", "Invoker", "Invocation path", "Dependencies", "Evidence", "Anchor")

    @classmethod
    def setUpClass(cls):
        cls.text = (POOL / MAP).read_text()
        cls.rows = {cells[0]: cells for cells in table_rows(cls.text) if len(cells) == len(cls.COLUMNS)}

    def cell(self, name, column):
        return self.rows[name][self.COLUMNS.index(column)]

    def test_implement_runs_right_here_on_a_single_session_build_and_per_ticket_on_a_multi_session_one(self):
        # Main flow step 3: "No -> /implement right here, in the same context window." Yes -> "/implement per
        # ticket, /clear-ing context between each one."
        route, invoker = self.cell("implement", "Route and phase"), self.cell("implement", "Invoker")
        self.assertIn("same context window", route)
        self.assertRegex(invoker, r"\blead\b")
        self.assertRegex(invoker, r"\bworker\b")
        self.assertIn("multi-session", route)
        self.assertLess(route.index("multi-session"), route.index("cleared"))

    def test_domain_modeling_and_codebase_design_keep_direct_use_beside_their_wrappers(self):
        # Vocabulary underneath: "Reach for them directly when the words, not the process, are the problem; or let
        # the skills above pull them in."
        for name, wrapper in (("domain-modeling", "grill-with-docs"), ("codebase-design", "tdd")):
            with self.subTest(name):
                self.assertIn("directly", self.cell(name, "Invoker") + " " + self.cell(name, "Invocation path"))
                self.assertIn(wrapper, self.cell(name, "Invoker"))

    def test_grilling_runs_through_a_wrapper_by_a_griller_worker_or_directly(self):
        # Standalone: "Reach for it directly only when you want the interview with no wrapper around it."
        invoker, path = self.cell("grilling", "Invoker"), self.cell("grilling", "Invocation path")
        self.assertRegex(invoker, r"\blead\b")
        self.assertIn("griller worker", invoker)
        self.assertIn("directly", invoker + " " + path)
        self.assertIn("wrapper", invoker + " " + path)

    def test_tdd_and_code_review_run_on_their_own_by_the_lead_or_by_a_worker_on_a_brief(self):
        # S19-2. Main flow step 3: "Reach for /tdd on its own when you just want to build a concrete behaviour
        # test-first without a full spec, and /code-review on its own whenever you want to review a branch or PR
        # against a fixed point." implement calls both, and implement runs right here on a single-session build.
        for name in ("tdd", "code-review"):
            with self.subTest(name):
                invoker = self.cell(name, "Invoker")
                self.assertRegex(invoker, r"\blead\b")
                self.assertIn("directly", invoker)
                self.assertRegex(invoker, r"\bworker\b")
                self.assertIn("by brief", invoker)
                self.assertIn("implement", invoker)

    def test_the_map_and_the_phase_record_call_the_check_structure_not_proof_of_meaning(self):
        phase_record = (POOL / "engineering-workflow" / "references" / "phase-record.md").read_text()
        for label, text in (("workflow-map.md", self.text), ("phase-record.md", phase_record)):
            with self.subTest(label):
                self.assertIn("structure, coverage, category and body freshness", " ".join(text.split()))
        self.assertIn("records a read declaration", self.text)
        for overclaim in ("proves a read", "proves it ran"):
            self.assertNotIn(overclaim, self.text)


if __name__ == "__main__":
    unittest.main()
