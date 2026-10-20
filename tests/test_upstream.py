"""maint/upstream.py: see what changed upstream, take it without losing Mifal's changes, offer Mifal's back."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import REPO

TOOL = REPO / "maint" / "upstream.py"
SKILL = "---\nname: foo\n---\n# Foo\n\nStep one.\nStep two.\nStep three.\n"
NOTES = "notes, untouched by Mifal\n"


class Upstream(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mifal-upstream-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root / "home"), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com",
                    "MIFAL_UPSTREAM_BASE": str(self.root / "remotes"), "MIFAL_UPSTREAM_CACHE": str(self.root / "cache")}
        (self.root / "home").mkdir()
        self.work = self.root / "upstream-work"
        self.git(self.root, "init", "-q", "-b", "main", self.work)
        self.write_upstream({"skills/engineering/foo/SKILL.md": SKILL, "skills/engineering/foo/notes.md": NOTES,
                             "skills/engineering/bar/SKILL.md": "---\nname: bar\n---\n", "prose/guide.md": "v1\n"})
        self.base = self.commit("first")
        self.remote = self.root / "remotes" / "acme" / "skills.git"
        self.git(self.root, "clone", "-q", "--bare", self.work, self.remote)
        self.git(self.work, "remote", "add", "origin", self.remote)
        self.mifal = self.root / "mifal"
        local = SKILL.replace("Step one.", "Step one, as Mifal says it.")
        self.write(self.mifal / "skills/foo/SKILL.md", local)
        self.write(self.mifal / "skills/foo/notes.md", NOTES)
        self.provenance({"format": 1, "components": [
            {"name": "acme-skills", "repo": "acme/skills", "licence": "MIT", "skills": {"foo": {
                "upstream": "skills/engineering/foo", "commit": self.base,
                "tree": self.git(self.work, "rev-parse", f"{self.base}:skills/engineering/foo"),
                "modified": {"changed": ["SKILL.md"]}}}},
            {"name": "acme-prose", "repo": "acme/skills", "licence": "MIT", "revision": self.base,
             "paths": ["skills/frontend/guide.md"], "adaptations": "adapted into local prose"}]})

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *map(str, args)], env=self.env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_upstream(self, files):
        for rel, text in files.items():
            if text is None:
                (self.work / rel).unlink()
            else:
                self.write(self.work / rel, text)

    def commit(self, message):
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-q", "-m", message)
        return self.git(self.work, "rev-parse", "HEAD")

    def publish(self, message, files):
        self.write_upstream(files)
        sha = self.commit(message)
        self.git(self.work, "push", "-q", "origin", "main")
        return sha

    def provenance(self, data=None):
        path = self.mifal / "third_party/provenance.json"
        if data is not None:
            self.write(path, json.dumps(data, indent=2) + "\n")
        return json.loads(path.read_text())

    def foo(self):
        return self.provenance()["components"][0]["skills"]["foo"]

    def tool(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args, "--repo", str(self.mifal)], env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)


class Status(Upstream):
    def test_up_to_date_exits_0(self):
        result = self.tool("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("foo: up to date", result.stdout)

    def test_an_upstream_change_is_named_with_its_files_and_exits_1(self):
        self.publish("rework foo", {"skills/engineering/foo/notes.md": "new notes\n",
                                    "skills/engineering/foo/EXTRA.md": "extra\n"})
        result = self.tool("status")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"foo: behind acme/skills by 1 commit")
        self.assertIn("changed notes.md", result.stdout)
        self.assertIn("added EXTRA.md", result.stdout)
        self.assertIn("rework foo", result.stdout)

    def test_upstream_skills_mifal_does_not_ship_are_listed(self):
        self.assertIn("not shipped: skills/engineering/bar", self.tool("status").stdout)

    def test_a_single_revision_component_reports_new_commits_on_its_paths(self):
        self.publish("rewrite the prose", {"prose/guide.md": "v2\n"})
        result = self.tool("status")
        self.assertRegex(result.stdout, r"acme-prose: 1 new upstream commit")

    def test_a_skill_moved_or_removed_upstream_is_said_so(self):
        self.publish("drop foo", {"skills/engineering/foo/SKILL.md": None, "skills/engineering/foo/notes.md": None})
        result = self.tool("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("foo: skills/engineering/foo is gone upstream", result.stdout)


class Pull(Upstream):
    def test_an_upstream_change_is_merged_and_mifals_change_is_kept(self):
        head = self.publish("tighten step three", {
            "skills/engineering/foo/SKILL.md": SKILL.replace("Step three.", "Step three, tightened.")})
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.mifal / "skills/foo/SKILL.md").read_text()
        self.assertIn("Step one, as Mifal says it.", text)
        self.assertIn("Step three, tightened.", text)
        self.assertEqual(self.foo()["commit"], head)
        self.assertEqual(self.foo()["tree"], self.git(self.work, "rev-parse", f"{head}:skills/engineering/foo"))
        self.assertEqual(self.foo()["modified"], {"changed": ["SKILL.md"]})

    def test_when_upstream_takes_mifals_change_the_skill_is_recorded_as_unmodified(self):
        self.publish("take Mifal's wording", {
            "skills/engineering/foo/SKILL.md": (self.mifal / "skills/foo/SKILL.md").read_text()})
        self.assertEqual(self.tool("pull", "foo").returncode, 0)
        self.assertEqual(self.foo()["modified"], {})
        self.assertEqual(self.tool("diff", "foo").stdout, "")

    def test_upstream_additions_and_deletions_of_untouched_files_are_applied(self):
        self.publish("restructure", {"skills/engineering/foo/notes.md": None,
                                     "skills/engineering/foo/STEPS.md": "steps\n"})
        self.assertEqual(self.tool("pull", "foo").returncode, 0)
        self.assertFalse((self.mifal / "skills/foo/notes.md").exists())
        self.assertEqual((self.mifal / "skills/foo/STEPS.md").read_text(), "steps\n")

    def test_a_conflict_leaves_markers_lists_the_file_and_exits_1(self):
        self.publish("reword step one", {
            "skills/engineering/foo/SKILL.md": SKILL.replace("Step one.", "Step one, as upstream says it.")})
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("conflict skills/foo/SKILL.md", result.stdout)
        text = (self.mifal / "skills/foo/SKILL.md").read_text()
        self.assertIn("<<<<<<< mifal", text)
        self.assertIn("Step one, as Mifal says it.", text)
        self.assertIn("Step one, as upstream says it.", text)

    def test_a_file_mifal_changed_and_upstream_deleted_is_kept_and_reported(self):
        (self.mifal / "skills/foo/notes.md").write_text("Mifal's notes\n")
        self.publish("drop notes", {"skills/engineering/foo/notes.md": None})
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflict skills/foo/notes.md: deleted upstream, changed by Mifal", result.stdout)
        self.assertEqual((self.mifal / "skills/foo/notes.md").read_text(), "Mifal's notes\n")

    def test_up_to_date_pull_changes_nothing(self):
        before = (self.mifal / "third_party/provenance.json").read_bytes()
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.mifal / "third_party/provenance.json").read_bytes(), before)

    def test_a_symlink_in_the_skill_is_refused_before_any_write(self):
        foreign = self.root / "foreign.md"
        foreign.write_text(NOTES)
        (self.mifal / "skills/foo/notes.md").unlink()
        (self.mifal / "skills/foo/notes.md").symlink_to(foreign)
        before = (self.mifal / "third_party/provenance.json").read_bytes()
        self.publish("upstream notes", {"skills/engineering/foo/notes.md": "upstream replacement\n",
                                        "skills/engineering/foo/SKILL.md": SKILL + "More.\n"})
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("skills/foo/notes.md is a symlink", result.stderr)
        self.assertEqual(foreign.read_text(), NOTES)
        self.assertNotIn("More.", (self.mifal / "skills/foo/SKILL.md").read_text())
        self.assertEqual((self.mifal / "third_party/provenance.json").read_bytes(), before)

    def test_a_linked_skill_folder_is_refused(self):
        elsewhere = self.root / "elsewhere-foo"
        (self.mifal / "skills/foo").rename(elsewhere)
        (self.mifal / "skills/foo").symlink_to(elsewhere)
        self.publish("tighten step three", {
            "skills/engineering/foo/SKILL.md": SKILL.replace("Step three.", "Step three, tightened.")})
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("tightened", (elsewhere / "SKILL.md").read_text())

    def test_a_symlink_upstream_is_refused_before_any_write(self):
        (self.work / "skills/engineering/foo/link.md").symlink_to("notes.md")
        self.write_upstream({"skills/engineering/foo/SKILL.md": SKILL + "More.\n"})
        self.commit("add a link")
        self.git(self.work, "push", "-q", "origin", "main")
        before = (self.mifal / "third_party/provenance.json").read_bytes()
        result = self.tool("pull", "foo")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("link.md is a symlink", result.stderr)
        self.assertNotIn("More.", (self.mifal / "skills/foo/SKILL.md").read_text())
        self.assertEqual((self.mifal / "third_party/provenance.json").read_bytes(), before)

    def test_an_upstream_executable_arrives_executable(self):
        upstream = self.work / "skills/engineering/foo/run.sh"
        self.write(upstream, "#!/bin/sh\nexit 0\n")
        upstream.chmod(0o755)
        self.commit("executable helper")
        self.git(self.work, "push", "-q", "origin", "main")
        self.assertEqual(self.tool("pull", "foo").returncode, 0)
        self.assertEqual(stat.S_IMODE((self.mifal / "skills/foo/run.sh").stat().st_mode), 0o755)

    def test_an_unknown_skill_is_refused(self):
        result = self.tool("pull", "nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("nope", result.stdout + result.stderr)


class Diff(Upstream):
    def test_diff_shows_mifals_change_against_the_recorded_upstream(self):
        result = self.tool("diff", "foo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--- a/skills/engineering/foo/SKILL.md", result.stdout)
        self.assertIn("-Step one.", result.stdout)
        self.assertIn("+Step one, as Mifal says it.", result.stdout)
        self.assertNotIn("notes.md", result.stdout)

    def test_a_file_without_a_final_newline_gives_a_patch_git_applies(self):
        path = self.mifal / "skills/foo/SKILL.md"
        path.write_text(path.read_text().rstrip("\n"))
        result = self.tool("diff", "foo")
        check = subprocess.run(["git", "apply", "--check", "-"], cwd=self.work, env=self.env, input=result.stdout,
                               text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr + result.stdout)


class RealProvenance(unittest.TestCase):
    def test_every_vendored_skill_names_a_github_repo_and_a_folder_the_tool_can_fetch(self):
        data = json.loads((REPO / "third_party/provenance.json").read_text())
        for c in data["components"]:
            for name, s in c.get("skills", {}).items():
                with self.subTest(skill=name):
                    self.assertRegex(c["repo"], r"^[\w.-]+/[\w.-]+$")
                    self.assertTrue((REPO / "skills" / name).is_dir())
                    self.assertFalse(s["upstream"].startswith("/"))

    def test_the_tool_is_not_shipped_to_users(self):
        sys.path.insert(0, str(REPO / "lib"))
        from mifal import install
        self.assertNotIn("maint", install.ENGINE_SOURCES)


if __name__ == "__main__":
    unittest.main()
