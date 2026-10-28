import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_copilot", REPO / "maint" / "windows_copilot.py")
windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(windows)


class WindowsCopilot(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.home = root / "home"
        self.source = root / "source"
        self.home.mkdir()
        fixture = {
            "install.sh": "unchanged macOS guard",
            "VERSION": "0.0.0",
            "LICENSE": "license",
            "NOTICE": "notice",
            "third_party/provenance.json": "{}",
            "guide/AGENTS.md": "# Mifal Guide\n",
            "tools/review-snapshot.py": "# snapshot\n",
            "maint/windows_copilot.py": (REPO / "maint" / "windows_copilot.py").read_text(encoding="utf-8"),
            "skills/engineering-workflow/SKILL.md": "---\nname: engineering-workflow\ndescription: workflow\n---\n",
            "skills/team-lead/scripts/validate_contracts.py": "# validator\n",
            "skills/team-lead/SKILL.md": "---\nname: team-lead\ndescription: team\n---\n",
        }
        for name, body in fixture.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        self.lines = []

    def install(self, dry_run=False):
        return windows.install(self.home, self.source, dry_run, self.lines.append)

    def snapshot(self):
        return {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}

    def write_user(self, name, body=b"user content\r\n"):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def test_preview_writes_nothing(self):
        self.assertEqual(self.install(True), 0)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertIn("nothing written", self.lines[-1])

    def test_lifecycle_and_user_preservation(self):
        personal = self.write_user(".copilot/copilot-instructions.md")
        other = self.write_user(".agents/skills/my-skill/SKILL.md")
        before = self.snapshot()
        self.assertEqual(self.install(), 0)
        self.assertEqual(windows.doctor(self.home, self.lines.append), 0)
        self.assertEqual(personal.read_bytes(), before[str(personal.relative_to(self.home))])
        self.assertEqual(other.read_bytes(), before[str(other.relative_to(self.home))])
        installed = self.snapshot()
        self.assertEqual(self.install(), 0)
        self.assertEqual(self.snapshot(), installed)
        self.assertEqual(windows.uninstall(self.home, True, self.lines.append), 0)
        self.assertEqual(self.snapshot(), installed)
        self.assertEqual(windows.uninstall(self.home, False, self.lines.append), 0)
        self.assertEqual(self.snapshot(), before)

    def test_conflicts_refused_before_any_write(self):
        for name in (
            ".agents/skills/engineering-workflow/local.txt",
            ".copilot/skills/engineering-workflow/SKILL.md",
            ".copilot/instructions/mifal.instructions.md",
            ".agents/mifal/install-manifest.json",
        ):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as temporary:
                self.home = Path(temporary)
                self.write_user(name)
                before = self.snapshot()
                with self.assertRaises(windows.Refused):
                    self.install()
                self.assertEqual(self.snapshot(), before)

    def test_missing_source_refused(self):
        (self.source / "install.sh").unlink()
        with self.assertRaises(windows.Refused):
            self.install()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_modified_managed_files_preserved_and_reported(self):
        self.install()
        edited = self.write_user(".agents/skills/engineering-workflow/SKILL.md", b"edited")
        self.assertEqual(windows.doctor(self.home, self.lines.append), 1)
        with self.assertRaises(windows.Refused):
            self.install()
        self.assertEqual(windows.uninstall(self.home, False, self.lines.append), 1)
        self.assertEqual(edited.read_bytes(), b"edited")
        self.assertTrue((self.home / windows.MANIFEST).is_file())
        self.assertTrue((self.home / windows.ENGINE / "windows_copilot.py").is_file())
        edited.unlink()
        self.assertEqual(windows.uninstall(self.home, False, self.lines.append), 0)
        self.assertFalse((self.home / windows.MANIFEST).exists())

    def test_user_additions_survive_removal(self):
        self.install()
        added = self.write_user(".agents/skills/engineering-workflow/my-notes.txt")
        windows.uninstall(self.home, False, self.lines.append)
        self.assertEqual(added.read_bytes(), b"user content\r\n")
        self.assertIn("KEEP nonempty directory", "\n".join(self.lines))

    def test_new_source_cannot_overwrite_installed_revision(self):
        self.install()
        before = self.snapshot()
        (self.source / "VERSION").write_text("new")
        with self.assertRaises(windows.Refused):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_interrupted_install_is_diagnosed_and_removable(self):
        original = windows.write_new

        def interrupted(path, body):
            if path.name == "SKILL.md":
                raise OSError("simulated write failure")
            return original(path, body)

        with patch.object(windows, "write_new", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(windows.load(self.home)["state"], "installing")
        self.assertEqual(windows.doctor(self.home, self.lines.append), 1)
        self.assertEqual(windows.uninstall(self.home, False, self.lines.append), 0)
        self.assertEqual(self.snapshot(), {})

    def test_manifest_paths_cannot_escape_managed_roots(self):
        self.install()
        manifest = self.home / windows.MANIFEST
        for name in ("../outside", ".copilot/config.json", "/outside", ".agents/skills/../../outside"):
            with self.subTest(path=name):
                data = json.loads(manifest.read_text())
                original = manifest.read_bytes()
                data["files"][name] = "a" * 64
                manifest.write_text(json.dumps(data))
                before = self.snapshot()
                with self.assertRaises(windows.Refused):
                    windows.uninstall(self.home, False, self.lines.append)
                self.assertEqual(self.snapshot(), before)
                manifest.write_bytes(original)

    def test_foreign_home_manifest_refused(self):
        self.install()
        manifest = self.home / windows.MANIFEST
        data = json.loads(manifest.read_text())
        data["home"] = str(self.source)
        manifest.write_text(json.dumps(data))
        with self.assertRaises(windows.Refused):
            windows.doctor(self.home)

    def test_linked_path_refused(self):
        target = self.home.parent / "elsewhere"
        target.mkdir()
        try:
            (self.home / ".agents").symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Creating a test symlink requires OS privileges: {error}")
        with self.assertRaises(windows.Refused):
            self.install()
        self.assertEqual(list(target.iterdir()), [])

    def test_reparse_point_guard_without_new_python_api(self):
        with patch.object(windows, "linked", side_effect=lambda p: p.name == ".agents"):
            with self.assertRaises(windows.Refused):
                self.install()

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_refused(self):
        target = self.home.parent / "junction-target"
        target.mkdir()
        junction = self.home / ".agents"
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True, check=True,
        )
        self.addCleanup(junction.rmdir)
        with self.assertRaises(windows.Refused):
            self.install()
        self.assertEqual(list(target.iterdir()), [])

    def test_replaced_ancestor_prevents_removal(self):
        self.install()
        with patch.object(windows, "linked", side_effect=lambda p: p.name == "skills"):
            with self.assertRaises(windows.Refused):
                windows.uninstall(self.home, False)
        self.assertTrue((self.home / windows.MANIFEST).exists())

    def test_invalid_manifest_shape_is_refused(self):
        self.install()
        path = self.home / windows.MANIFEST
        original = path.read_bytes()
        for field, value in (("directories", [{}]), ("files", {}), ("format", 99)):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(windows.Refused):
                    windows.load(self.home)
                path.write_bytes(original)

    def test_lock_prevents_concurrent_uninstall(self):
        self.install()
        self.write_user(windows.LOCK)
        before = self.snapshot()
        with self.assertRaises(windows.Refused):
            windows.uninstall(self.home, False, self.lines.append)
        self.assertEqual(self.snapshot(), before)

    def test_custom_copilot_home_and_other_platforms_refused(self):
        with patch.object(windows.sys, "platform", "win32"), patch.dict(
            os.environ, {"COPILOT_HOME": str(self.source)}
        ), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                windows.main(["install"])
        with patch.object(windows.sys, "platform", "darwin"), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                windows.main(["install"])

    def test_real_payload_preserves_upstream_skills_and_guide(self):
        files = windows.payload(REPO)
        skills = {p.parts[2] for p in files if p.parts[:2] == windows.POOL.parts}
        self.assertEqual(len(skills), 39)
        for target, body in files.items():
            if target.parts[:2] == windows.POOL.parts:
                self.assertEqual(body, (REPO / "skills" / Path(*target.parts[2:])).read_bytes())
        self.assertEqual(
            files[windows.ENGINE / "guide" / "AGENTS.md"], (REPO / "guide" / "AGENTS.md").read_bytes()
        )
        self.assertIn(b"unsupported", files[windows.INSTRUCTIONS])
        self.assertIn(windows.ENGINE / "tools" / "review-snapshot.py", files)


if __name__ == "__main__":
    unittest.main()
