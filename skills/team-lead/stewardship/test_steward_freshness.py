import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location("steward", Path(__file__).with_name("steward.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "inventory.json"
        for name in ["INVENTORY_JSON", "build_inventory", "committed_inventory"]:
            value = getattr(MODULE, name)
            self.addCleanup(setattr, MODULE, name, value)
        MODULE.INVENTORY_JSON = self.path
        self.current = {"generatedAt": "today", "skills": {"team-lead": {"version": "new"}},
                        "reconciliation": {"poolSkills": 1, "lockEntries": 0}}
        MODULE.build_inventory = lambda: copy.deepcopy(self.current)
        self.committed = copy.deepcopy(self.current)
        self.committed["skills"]["team-lead"]["version"] = "old"
        MODULE.committed_inventory = lambda: copy.deepcopy(self.committed)

    def check(self, working):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            MODULE.cmd_check(SimpleNamespace(working_tree=working))

    def test_working_gate_passes_without_claiming_committed_freshness(self):
        self.path.write_text(json.dumps(self.current))
        self.check(True)
        with self.assertRaises(SystemExit) as error:
            self.check(False)
        self.assertEqual(error.exception.code, 1)

    def test_stale_working_inventory_fails(self):
        self.path.write_text(json.dumps(self.committed))
        with self.assertRaises(SystemExit) as error:
            self.check(True)
        self.assertEqual(error.exception.code, 1)

    def test_missing_working_inventory_fails(self):
        with self.assertRaises(SystemExit) as error:
            self.check(True)
        self.assertEqual(error.exception.code, 1)

    def test_generation_time_does_not_create_false_drift(self):
        prior = copy.deepcopy(self.current)
        prior["generatedAt"] = "yesterday"
        self.path.write_text(json.dumps(prior))
        self.check(True)


if __name__ == "__main__":
    unittest.main()
