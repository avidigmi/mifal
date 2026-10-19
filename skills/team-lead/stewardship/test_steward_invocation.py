import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("steward_invocation", Path(__file__).with_name("steward.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        pool = root / "skills"
        skill = pool / "db-query-optimizer"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: db-query-optimizer\ndescription: Database performance indexing\ndisable-model-invocation: true\n---\nBody\n")
        (skill / "agents").mkdir()
        (skill / "agents/openai.yaml").write_text("policy:\n  allow_implicit_invocation: false\n")
        policy = root / "policy.json"
        policy.write_text(json.dumps({"default": "auto"}))
        self.body = skill / "SKILL.md"
        mapping = dict(POOL=pool, POLICY_FILE=policy, LOCK_FILE=root / "lock.json",
                       SCOPES_FILE=root / "scopes.json", OVERLAYS_FILE=root / "overlays.json",
                       CATALOG_FILE=root / "catalog.md", BRIEFS_DIR=root / "briefs", LEDGER_FILE=root / "ledger.json")
        self.patch = patch.multiple(MODULE, **mapping)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_automatic_intent_does_not_claim_blocked_engine_or_runtime_visibility(self):
        skill = MODULE.build_inventory()["skills"]["db-query-optimizer"]
        self.assertTrue(skill["automaticPolicy"])
        self.assertEqual(skill["resolvedBody"], str(self.body))
        self.assertTrue(skill["declaredInvocation"]["claudeDisableModelInvocation"])
        self.assertFalse(skill["declaredInvocation"]["codexAllowImplicitInvocation"])
        self.assertFalse(skill["declaredInvocation"]["claudeAutomaticEligible"])
        self.assertFalse(skill["declaredInvocation"]["codexAutomaticEligible"])
        self.assertEqual(skill["runtimeDiscovery"], "unverified")

    def test_case_and_punctuation_do_not_change_technology_selection(self):
        outputs = []
        for query in (["Database", "PERFORMANCE"], ["database", "performance"], ["Database:performance"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                MODULE.who(query)
            outputs.append(out.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
        self.assertIn("db-query-optimizer", outputs[0])

    def test_unsupported_indentation_cannot_be_reported_as_engine_eligible(self):
        (self.body.parent / "agents/openai.yaml").write_text("  policy:\n    allow_implicit_invocation: false\n")
        with self.assertRaisesRegex(ValueError, "Unsupported.*indent"):
            MODULE.build_inventory()

    def test_indented_skill_frontmatter_refuses_inventory_eligibility(self):
        self.body.write_text("---\n  name: db-query-optimizer\n  description: Example\n  disable-model-invocation: true\n---\nBody\n")
        with self.assertRaisesRegex(ValueError, "Unsupported frontmatter indentation"):
            MODULE.build_inventory()

    def test_scalar_reference_is_not_a_declared_blocker(self):
        (self.body.parent / "agents/openai.yaml").write_text("policy:\n  note: |\n    allow_implicit_invocation: false\n")
        skill = MODULE.build_inventory()["skills"]["db-query-optimizer"]
        self.assertIsNone(skill["declaredInvocation"]["codexAllowImplicitInvocation"])
        self.assertTrue(skill["declaredInvocation"]["codexAutomaticEligible"])


if __name__ == "__main__":
    unittest.main()
