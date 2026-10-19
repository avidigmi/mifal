import unittest

from support import OPENCODE_CONFIG_INPUTS, ROOT_VARIABLES, RefusedEnvironment, Sandbox


class RootVariables(Sandbox):
    def test_a_root_outside_the_test_home_is_refused_before_anything_runs(self):
        self.fake("probe", "")
        for name in ROOT_VARIABLES:
            with self.subTest(name=name):
                with self.assertRaises(RefusedEnvironment):
                    self.run_cmd(self.fakebin / "probe", **{name: "/Users/someone/.claude"})
                with self.assertRaises(RefusedEnvironment):
                    self.run_in_terminal(self.fakebin / "probe", answer="", **{name: str(self.root)})
        self.assertEqual(self.called(), [])

    def test_a_root_inside_the_test_home_is_allowed(self):
        self.fake("probe", "")
        self.run_cmd(self.fakebin / "probe", CLAUDE_CONFIG_DIR=str(self.home / ".claude-other"))
        self.assertEqual(self.called(), ["probe "])

    def test_an_empty_or_relative_root_is_refused(self):
        self.fake("probe", "")
        for name in ROOT_VARIABLES:
            for value in ("", "relative/dir"):
                if name == "XDG_CONFIG_HOME" and value == "":
                    continue
                with self.subTest(name=name, value=value):
                    with self.assertRaises(RefusedEnvironment):
                        self.run_cmd(self.fakebin / "probe", **{name: value})
        self.assertEqual(self.called(), [])

    def test_an_empty_xdg_config_home_counts_as_unset(self):
        self.fake("probe", "")
        self.run_cmd(self.fakebin / "probe", XDG_CONFIG_HOME="")
        self.assertEqual(self.called(), ["probe "])

    def test_opencode_config_inputs_are_refused(self):
        self.fake("probe", "")
        for name in OPENCODE_CONFIG_INPUTS:
            with self.subTest(name=name):
                with self.assertRaises(RefusedEnvironment):
                    self.run_cmd(self.fakebin / "probe", **{name: "{}"})
        self.assertEqual(self.called(), [])


if __name__ == "__main__":
    unittest.main()
