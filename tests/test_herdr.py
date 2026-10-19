import hashlib
import unittest

from support import Sandbox

URL = "https://herdr.dev/install.sh"


class HerdrRequirement(Sandbox):
    def setUp(self):
        super().setUp()
        self.unfake("herdr")
        self.script = self.root / "herdr-install.sh"
        self.official_installer()
        self.fake("curl", 'out=""; while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; shift; fi; shift; done; '
                          f'cp "{self.script}" "$out"')

    def official_installer(self, version="0.9.6", status=0):
        """A stand-in for Herdr's script: it really writes the binary where the official one does."""
        self.script.write_text(
            "#!/bin/sh\n"
            f'echo "installer ran" >> "{self.calls}"\n'
            'dir="${HERDR_INSTALL_DIR:-$HOME/.local/bin}"\n'
            'mkdir -p "$dir"\n'
            f"printf '#!/bin/sh\\necho \"herdr {version}\"\\n' > \"$dir/herdr\"\n"
            'chmod +x "$dir/herdr"\n'
            f"exit {status}\n")

    def script_sha(self):
        return hashlib.sha256(self.script.read_bytes()).hexdigest()

    def brew_that_installs(self, version="0.9.6"):
        self.fake("brew", f"printf '#!/bin/sh\\necho \"herdr {version}\"\\n' > \"{self.fakebin}/herdr\"; "
                          f'chmod +x "{self.fakebin}/herdr"')

    def ran_installer(self):
        return "installer ran" in self.called()

    def nothing_of_mifal(self):
        self.assertFalse((self.home / ".agents").exists())

    def test_a_current_herdr_installs_nothing(self):
        self.fake("herdr", 'if [ "$1" = "--version" ]; then echo "herdr 0.9.5"; fi')
        result = self.install()
        self.assertIn("herdr 0.9.5 found", result.stdout)
        self.assertFalse([c for c in self.called() if not c.startswith("herdr --version")])
        self.assertEqual(self.manifest()["dependencies"]["herdr"]["route"], "found")

    def test_without_herdr_homebrew_installs_it_and_the_version_is_rechecked(self):
        self.brew_that_installs()
        preview = self.install("--dry-run")
        self.assertIn("brew install herdr", preview.stdout)
        self.assertNotIn("brew install herdr", self.called())
        result = self.install()
        self.assertIn("brew install herdr", self.called())
        self.assertIn("herdr 0.9.6 found", result.stdout)
        herdr = self.manifest()["dependencies"]["herdr"]
        self.assertEqual((herdr["route"], herdr["installed_by_mifal"], herdr["version"]), ("brew", True, "0.9.6"))
        self.assertIn("brew uninstall herdr", self.mifal("uninstall").stdout)

    def test_a_brew_install_that_leaves_no_herdr_stops(self):
        self.fake("brew", "")
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("herdr", result.stdout + result.stderr)
        self.nothing_of_mifal()

    def test_without_homebrew_a_no_answer_runs_nothing(self):
        result = self.run_in_terminal("/bin/bash", self.clone / "install.sh", answer="n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.script_sha(), result.stdout)
        self.assertFalse(self.ran_installer())
        self.nothing_of_mifal()

    def test_a_yes_runs_the_shown_script_and_records_it(self):
        result = self.run_in_terminal("/bin/bash", self.clone / "install.sh", answer="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(result.stdout.index(self.script_sha()), result.stdout.index("[y/N]"))
        self.assertIn(URL, result.stdout)
        downloads = [c for c in self.called() if c.startswith("curl")]
        self.assertEqual(len(downloads), 1)
        self.assertRegex(downloads[0], r"^curl -fsSL -o \S+ " + URL + "$")
        self.assertTrue(self.ran_installer())
        self.assertTrue((self.home / ".local/bin/herdr").exists())
        herdr = self.manifest()["dependencies"]["herdr"]
        self.assertEqual((herdr["route"], herdr["installed_by_mifal"], herdr["version"], herdr["script_sha256"]),
                         ("script", True, "0.9.6", self.script_sha()))
        self.assertIn("Nothing to change", self.install().stdout)
        result = self.mifal("uninstall")
        self.assertTrue((self.home / ".local/bin/herdr").exists())
        self.assertIn("rm ~/.local/bin/herdr", result.stdout)

    def test_a_script_changed_after_it_was_shown_is_not_run(self):
        result = self.install("--herdr-script", check=False, MIFAL_TEST_FAIL_AT="tamper-herdr-script")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed", result.stdout + result.stderr)
        self.assertFalse(self.ran_installer())
        self.nothing_of_mifal()

    def test_non_interactive_without_homebrew_stops_unless_flagged(self):
        stopped = self.install(check=False)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("--herdr-script", stopped.stderr + stopped.stdout)
        self.assertFalse(self.ran_installer())
        self.nothing_of_mifal()
        self.install("--herdr-script")
        self.assertTrue(self.ran_installer())

    def test_the_dry_run_downloads_and_runs_nothing(self):
        result = self.install("--dry-run", "--herdr-script")
        self.assertIn(URL, result.stdout)
        self.assertEqual([c for c in self.called() if c.startswith("curl")], [])
        self.assertFalse(self.ran_installer())

    def test_a_failing_installer_stops_and_reports_what_it_left(self):
        self.official_installer(status=3)
        result = self.install("--herdr-script", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exit 3", result.stdout + result.stderr)
        self.assertIn("~/.local/bin/herdr", result.stdout + result.stderr)
        self.nothing_of_mifal()

    def test_an_installer_that_leaves_an_old_herdr_stops(self):
        self.official_installer(version="0.9.1")
        result = self.install("--herdr-script", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0.9.1", result.stdout + result.stderr)
        self.nothing_of_mifal()

    def test_an_existing_herdr_off_path_is_used_not_overwritten(self):
        target = self.home / ".local/bin/herdr"
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\necho "herdr 0.9.7"\n')
        target.chmod(0o755)
        result = self.install("--herdr-script")
        self.assertIn("herdr 0.9.7 found", result.stdout)
        self.assertFalse(self.ran_installer())
        self.assertEqual(self.manifest()["dependencies"]["herdr"]["installed_by_mifal"], False)

    def test_a_broken_file_where_the_script_would_write_stops_it(self):
        for env in ({}, {"HERDR_INSTALL_DIR": str(self.home / "opt/bin")}):
            with self.subTest(env=env):
                target = self.home / (env and "opt/bin/herdr" or ".local/bin/herdr")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("not a program\n")
                result = self.install("--herdr-script", check=False, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(target.relative_to(self.home)), result.stdout + result.stderr)
                self.assertFalse(self.ran_installer())
                self.assertEqual(target.read_text(), "not a program\n")
                target.unlink()

    def test_an_old_herdr_is_reported_with_its_upgrade_command(self):
        self.fake("herdr", 'if [ "$1" = "--version" ]; then echo "herdr 0.9.3"; fi')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0.9.3", result.stdout + result.stderr)
        self.assertIn("herdr update", result.stdout + result.stderr)
        self.nothing_of_mifal()


if __name__ == "__main__":
    unittest.main()
