#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("check_compliance.py")
spec = importlib.util.spec_from_file_location("check_compliance", SCRIPT)
compliance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compliance)

AGENTS = """# Rules
- Never run `git push --force` on shared branches.
- Tests and lint must pass before commit.
"""


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "AGENTS.md").write_text(AGENTS)

    def cli(self, cmd, evidence):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), cmd, str(self.project), "--evidence", "-"],
            input=json.dumps(evidence), capture_output=True, text=True, timeout=30)

    def test_malformed_command_evidence_is_a_usage_error_in_api_and_cli(self):
        for evidence in ({"commands": "git push --force"},
                         {"planned_commands": [["git", "push", "--force"]]},
                         {"commands": ["git status", 7]}):
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(compliance.UsageError, "list of strings"):
                    compliance.gate_dispatch(self.project, evidence)
                with self.assertRaisesRegex(compliance.UsageError, "list of strings"):
                    compliance.check_task(self.project, evidence)
                for cmd in ("gate", "check"):
                    result = self.cli(cmd, evidence)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("list of strings", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_valid_command_evidence_still_decides(self):
        self.assertEqual(compliance.gate_dispatch(self.project, {"commands": []})["decision"], "allow")
        self.assertEqual(compliance.gate_dispatch(
            self.project, {"commands": ["git push --force"]})["decision"], "refuse")
        result = self.cli("gate", {"commands": ["git push origin main"]})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_force_push_behind_a_separator_is_refused(self):
        for cmd in ("git push --force; true", "git push -f&&true", "git push --force||true",
                    "git push --force|cat", "true; git push -f origin main", "git push --force &"):
            with self.subTest(cmd=cmd):
                gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                self.assertEqual(gate["decision"], "refuse", gate["reasons"])
                self.assertIn("push-force", " ".join(gate["reasons"]))
                self.assertEqual(self.cli("gate", {"commands": [cmd]}).returncode, 1)

    def test_substitution_and_unsupported_syntax_are_uncheckable_and_never_run(self):
        marker = self.project / "ran"
        for cmd in (f"echo $(touch {marker}; git push --force)", f"echo `touch {marker}`",
                    "git push $FLAGS", "sh -c 'git push --force'", "eval git push --force",
                    "git push 'unbalanced", "cat <(git push --force)", "git push origin main > log"):
            with self.subTest(cmd=cmd):
                report = compliance.check_task(self.project, {"commands": [cmd]})
                self.assertEqual([f for f in report["findings"] if f["detector"] == "forbidden-command"], [])
                self.assertTrue(any(u.get("detector") == "forbidden-command" and u["rule_id"] == "r1"
                                    for u in report["uncheckable"]), report["uncheckable"])
                gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                self.assertTrue(any("went unverified" in r and "r1" in r for r in gate["reasons"]),
                                gate["reasons"])
        self.assertFalse(marker.exists())

    def test_a_violation_in_one_segment_is_not_masked_by_an_unsupported_sibling(self):
        # CP2 Spec12 F1: a $ or redirect segment made the whole string uncheckable.
        marker = self.project / "ran"
        for cmd in ("git push -f ; echo $?", "echo '$HOME' ; git push -f",
                    "git commit -m 'costs $5'; git push -f", f"echo hi > {marker}; git push --force origin main"):
            with self.subTest(cmd=cmd):
                gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                self.assertEqual(gate["decision"], "refuse", gate["reasons"])
                self.assertIn("push-force", " ".join(gate["reasons"]))
                self.assertEqual(self.cli("gate", {"commands": [cmd]}).returncode, 1)
        self.assertFalse(marker.exists())

    def test_a_quoted_or_escaped_operator_is_an_argument_not_a_separator(self):
        # Task19 HIGH: POSIX decoding erased quotes, so a quoted ";" split git push from its --force.
        marker = self.project / "ran"
        for cmd in ('git push ";" --force', 'git push "&&" --force', "git push '|' --force",
                    "git push '||' -f", "git push \\; --force", "git push \\&\\& --force",
                    "git pu\"sh\" '--fo'rce origin", f"git push ';touch {marker};' --force"):
            with self.subTest(cmd=cmd):
                gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                self.assertEqual(gate["decision"], "refuse", gate["reasons"])
                self.assertIn("push-force", " ".join(gate["reasons"]))
                self.assertEqual(self.cli("gate", {"commands": [cmd]}).returncode, 1)
        self.assertFalse(marker.exists())

    def test_each_real_separator_and_newline_delimits_commands(self):
        for sep in (";", "&&", "||", "|", "&", "\n"):
            with self.subTest(sep=sep):
                gate = compliance.gate_dispatch(self.project, {"commands": [f"true{sep}git push -f"]})
                self.assertEqual(gate["decision"], "refuse", gate["reasons"])
                clean = compliance.check_task(self.project, {"commands": [f"git push origin main{sep}echo --force"]})
                self.assertEqual([f for f in clean["findings"] if f["detector"] == "forbidden-command"], [])
                self.assertFalse(any(u.get("detector") == "forbidden-command" for u in clean["uncheckable"]),
                                 clean["uncheckable"])

    def test_line_continuation_joins_and_a_quoted_newline_is_text(self):
        gate = compliance.gate_dispatch(self.project, {"commands": ["git push \\\n--force origin main"]})
        self.assertEqual(gate["decision"], "refuse", gate["reasons"])
        self.assertEqual(self.cli("gate", {"commands": ["git push \\\n--force"]}).returncode, 1)
        for cmd in ('git commit -m "a\nb; --force"', "git commit -m 'x\\\ny'"):
            with self.subTest(cmd=cmd):
                report = compliance.check_task(self.project, {"commands": [cmd]})
                self.assertEqual([f for f in report["findings"] if f["detector"] == "forbidden-command"], [])
                self.assertFalse(any(u.get("detector") == "forbidden-command" for u in report["uncheckable"]))

    def test_a_trailing_escape_is_uncheckable_not_a_refusal(self):
        report = compliance.check_task(self.project, {"commands": ["git push \\"]})
        self.assertTrue(any(u.get("detector") == "forbidden-command" for u in report["uncheckable"]), report)
        self.assertEqual(compliance.gate_dispatch(self.project, {"commands": ["git push \\"]})["decision"], "allow")

    def test_a_separator_next_to_other_punctuation_still_ends_the_command(self):
        # Security30 HIGH: the greedy punctuation run read ";>" as one unknown operator and hid the force-push before it.
        for cmd in ("git push -f origin main ;> output", "git push -f origin main ; > output", "git push -f&&> out",
                    "git push -f||> out", "git push -f|> out", "git push -f;(echo hi)", "git push -f&(echo hi)",
                    "(echo hi);git push -f", "(echo hi)|git push -f", "echo hi >&1;git push -f", "echo hi &>out;git push -f",
                    "echo hi >|out;git push -f", "git push -f;echo $(date)", "echo $(date);git push -f"):
            with self.subTest(cmd=cmd):
                gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                self.assertEqual(gate["decision"], "refuse", gate["reasons"])
                self.assertIn("push-force", " ".join(gate["reasons"]))
        self.assertEqual(compliance.command_segments("git push -f origin main ;> output")[0], (["git", "push", "-f", "origin", "main"], None))
        self.assertEqual(self.cli("gate", {"commands": ["git push -f origin main ;> output"]}).returncode, 1)

    def test_a_whole_redirect_or_control_operator_is_one_unsupported_token_not_a_separator(self):
        for op in (">&", "<&", "&>", "&>>", ">|", ">>&", "&>|", ">&|", "&>>|", ">!", "&>!", "<>", ">>", "<<<"):
            with self.subTest(op=op):
                report = compliance.check_task(self.project, {"commands": [f"git push {op} --force"]})
                self.assertEqual([f for f in report["findings"] if f["detector"] == "forbidden-command"], [])
                self.assertTrue(any(u.get("detector") == "forbidden-command" for u in report["uncheckable"]), report)
        self.assertEqual(compliance.command_segments("git push 2>&1")[0][0], ["git", "push", "2", ">&", "1"])

    def test_an_operator_that_ends_a_command_splits_it_like_a_separator(self):
        # Review45 HIGH: read as one unsupported token, zsh's "&!" hid the force-push before it.
        for op in ("&!", "&|", "|&", ";;", ";&", ";|"):
            for cmd in (f"git push -f {op}", f"git push -f{op}", f"git push -f {op} true", f"true {op} git push -f", f"true {op}git push -f"):
                with self.subTest(cmd=cmd):
                    gate = compliance.gate_dispatch(self.project, {"commands": [cmd]})
                    self.assertEqual(gate["decision"], "refuse", gate["reasons"])
            with self.subTest(op=op, flag="not borrowed"):
                report = compliance.check_task(self.project, {"commands": [f"git push {op} echo --force"]})
                self.assertEqual([f for f in report["findings"] if f["detector"] == "forbidden-command"], [])
        self.assertEqual(self.cli("gate", {"commands": ["true &! git push -f"]}).returncode, 1)

    def test_comments_and_here_doc_bodies_never_hide_a_force_push(self):
        for cmd in ("git push -f # harmless", "echo hi # ignored\ngit push -f", "echo x#y;git push -f", "echo '#';git push -f",
                    "echo \\#;git push -f", "echo hi # \\\ngit push -f", "cat <<EOF\ngit push -f\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertEqual(compliance.gate_dispatch(self.project, {"commands": [cmd]})["decision"], "refuse")

    def test_a_violation_is_reported_even_beside_an_uncheckable_command(self):
        gate = compliance.gate_dispatch(self.project, {"commands": ["echo $(date)", "git push -f"]})
        self.assertEqual(gate["decision"], "refuse")

    def test_supported_compound_commands_stay_checked_and_clean(self):
        for cmd in ("pnpm test && git push origin main", "git push --force-with-lease origin main",
                    "git status; git log | head", "git commit -m 'fix: handle a; b'"):
            with self.subTest(cmd=cmd):
                report = compliance.check_task(self.project, {"commands": [cmd]})
                self.assertEqual([f for f in report["findings"] if f["detector"] == "forbidden-command"], [])
                self.assertFalse(any(u.get("detector") == "forbidden-command" for u in report["uncheckable"]))

    def test_each_required_check_needs_its_own_passed_claim(self):
        partial = {"commit_messages": ["feat: x"], "claims": [{"claim": "tests", "status": "passed"}]}
        report = compliance.check_task(self.project, partial)
        self.assertEqual([f["rule_id"] for f in report["findings"]], ["r2"])
        self.assertIn("lint", report["findings"][0]["detail"])
        self.assertNotIn("test", report["findings"][0]["detail"].split("covering")[-1])
        failed_lint = {**partial, "claims": partial["claims"] + [{"claim": "lint", "status": "failed"}]}
        self.assertEqual(len(compliance.check_task(self.project, failed_lint)["findings"]), 1)

    def test_all_required_checks_passed_is_clean(self):
        for claims in ([{"claim": "tests", "status": "passed"}, {"claim": "lint", "status": "passed"}],
                       [{"claim": "unit test and lint", "status": "passed"}]):
            with self.subTest(claims=claims):
                report = compliance.check_task(self.project, {"commit_messages": ["feat: x"], "claims": claims})
                self.assertEqual(report["findings"], [])

    def test_a_check_word_inside_another_word_is_not_evidence(self):
        (self.project / "AGENTS.md").write_text("# Rules\n- Run the checks before commit.\n")
        evidence = {"commit_messages": ["feat: x"], "claims": [{"claim": "typecheck", "status": "passed"}]}
        self.assertEqual(len(compliance.check_task(self.project, evidence)["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
