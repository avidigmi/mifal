import codecs
import fnmatch
import hashlib
import importlib.util
import json
import re
import subprocess
import unittest

from support import REPO

# The shipped set, plan D9: the 27 Ask Matt skills, the local attachments, the factory core and herdr.
ASK_MATT = (
    "ask-matt", "grill-with-docs", "grill-me", "grilling", "domain-modeling", "prototype", "handoff", "to-spec",
    "to-tickets", "implement", "implement-spec", "tdd", "code-review", "pr", "retro", "triage", "diagnosing-bugs",
    "wayfinder", "improve-codebase-architecture", "codebase-design", "research", "to-questionnaire", "wizard",
    "wait-what", "teach", "writing-for-agents", "setup-matt-pocock-skills")
SHIPPED = set(ASK_MATT) | {
    "receiving-code-review", "ddd", "ddd-scope", "ddd-ground", "ddd-verify", "frontend-design", "agent-browser",
    "engineering-workflow", "team-lead", "setup-pre-commit", "verification-before-completion", "herdr"}
# Skills that exist elsewhere and do not ship; a shipped body must not depend on them.
UNSHIPPED = (
    "cloudflare", "codebase-hygiene", "commit", "compact-safe", "deploy-to-vercel", "diff-driven-docs",
    "find-skills", "github", "last30days", "mongodb-atlas-stream-processing", "mongodb-connection",
    "mongodb-mcp-setup", "mongodb-natural-language-querying", "mongodb-query-optimizer", "mongodb-schema-design",
    "mongodb-search-and-ai", "octocode", "security-review", "vercel-cli-with-tokens", "vercel-optimize",
    "writing-awesome-readmes", "design-accessibility-audit", "ddd-book", "ddd-exception")
# Optional integrations and path rules that name an unshipped skill on purpose (task 2 N2).
ALLOWED = {
    ("skills/ddd/SKILL.md", "ddd-book"): "optional integration, used only on explicit request",
    ("skills/ddd/SKILL.md", "ddd-exception"): "optional integration, used only on explicit request",
    ("skills/team-lead/references/review-routing.md", "security-review"): "a self-policy path rule, not a dependency",
}
# Shipped-set names that are not skill folders, plan D9.
SHIPPED_PATHS = {
    "herdr-adapter": "tools/herdr_adapter",
    "validate-contracts": "skills/team-lead/scripts/validate_contracts.py",
}
LEFT_OUT = (
    "skills/team-lead/recruitment/pitches", "skills/team-lead/recruitment/onboarded",
    "skills/team-lead/recruitment/examples", "skills/team-lead/recruitment/demo.sh",
    "skills/team-lead/recruitment/gaps.md", "skills/team-lead/scripts/test_demo.py")
LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"(?ms)^```.*?^```")
# The engine's own folders; the factory's private ones do not exist on a Mifal machine.
UNSHIPPED_PATHS = re.compile(r"\.agents/(scripts|tools)\b")
# Personal identifiers, kept as sha256 prefixes so this file does not ship them: the author's name and
# handle, private projects, profile and launcher names, the factory's owner variable, a private brief and tool.
DENIED = {
    "1a0806c20104d346", "776f49007a01981a", "7ff8d3807b844b13", "e5253ccc111e7310", "dd4bc119ea05e489",
    "84138e3e9c86337d", "9daa7e32e4afe10a", "0c46c1c0dcb05434", "515f6ce2a28a11a1", "6b39a32839317b67",
    "040fce9abddf536b", "35bc2a33cb1d075d",
}
# The author's name and handle may appear only on a credit line of a credit file, and in the project's own
# repository URL where a user is told how to clone it.
CREDIT_TOKENS = {"1a0806c20104d346", "776f49007a01981a", "7ff8d3807b844b13"}
CREDIT_FILES = {"LICENSE", "NOTICE", "third_party/provenance.json", "skills/team-lead/SKILL.md",
                "lib/mifal/update.py", "README.md"}
CREDIT_LINE = re.compile(r'Copyright|"copyright"|^\s*author:|github\.com/|"repo":')
TOKEN = re.compile(r"[a-z0-9_-]+")
# Shipped by plan D9 but not rows of the workflow map: the implementer role names one, the lead uses the other.
OUTSIDE_THE_MAP = {"verification-before-completion", "herdr"}


def tracked():
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.splitlines() if (REPO / p).is_file())


def prose():
    """Shipped Markdown, without test fixtures, which are data and may be broken on purpose."""
    return [p for p in tracked() if p.endswith(".md") and "/fixtures/" not in p]


def validator():
    spec = importlib.util.spec_from_file_location("validate_contracts",
                                                  REPO / SHIPPED_PATHS["validate-contracts"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShippedSet(unittest.TestCase):
    def test_exactly_the_planned_skills_ship(self):
        folders = {p.name for p in (REPO / "skills").iterdir() if p.is_dir() and p.name != "__pycache__"}
        self.assertEqual(folders, SHIPPED)
        self.assertEqual(len(folders), 39)

    def test_left_out_parts_of_team_lead_are_absent(self):
        self.assertEqual([p for p in LEFT_OUT if (REPO / p).exists()], [])

    def test_every_shipped_set_name_has_a_path(self):
        names = [n for _, _, column, _, shipped in validator().WORKFLOW_MAP_TABLES if column == "Skill"
                 for n in shipped]
        missing = [n for n in names if not (REPO / SHIPPED_PATHS.get(n, "skills/" + n)).exists()]
        self.assertEqual(missing, [])
        self.assertEqual(set(names) - set(SHIPPED_PATHS), SHIPPED - OUTSIDE_THE_MAP)

    def test_the_workflow_map_matches_the_shipped_set(self):
        v = validator()
        self.assertEqual(v.workflow_map_errors(REPO / "skills", lambda p: p.read_text(encoding="utf-8")), [])


class Closure(unittest.TestCase):
    def test_no_shipped_body_names_an_unshipped_skill(self):
        found = []
        for rel in prose():
            text = (REPO / rel).read_text(encoding="utf-8")
            for name in UNSHIPPED:
                if re.search(r"(?<![\w./-])[/`]" + re.escape(name) + r"(?![\w-])", text) \
                        and (rel, name) not in ALLOWED:
                    found.append(f"{rel}: {name}")
        self.assertEqual(found, [])

    def test_every_relative_link_resolves(self):
        broken = []
        for rel in prose():
            text = FENCE.sub("", (REPO / rel).read_text(encoding="utf-8"))
            for target in LINK.findall(text):
                path = target.split("#", 1)[0]
                if not path or re.match(r"^[a-z][a-z0-9+.-]*:", path) or path.startswith(("~", "/")):
                    continue
                if not ((REPO / rel).parent / path).exists():
                    broken.append(f"{rel} -> {target}")
        self.assertEqual(sorted(set(broken)), [])

    def test_no_shipped_file_names_a_private_factory_folder(self):
        found = [f"{rel}:{n}" for rel in tracked() if rel.startswith(("skills/", "tools/", "lib/", "bin/"))
                 for n, line in enumerate((REPO / rel).read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if UNSHIPPED_PATHS.search(line)]
        self.assertEqual(found, [])


def denied(rel, line):
    found = {hashlib.sha256(t.encode()).hexdigest()[:16] for t in TOKEN.findall(line.lower())} & DENIED
    if found and found <= CREDIT_TOKENS and rel in CREDIT_FILES and CREDIT_LINE.search(line):
        return set()
    return found


class Denylist(unittest.TestCase):
    def test_nothing_personal_ships(self):
        found = [f"{rel}:{n}" for rel in tracked()
                 for n, line in enumerate((REPO / rel).read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if denied(rel, line)]
        self.assertEqual(found, [])

    def test_the_check_sees_a_planted_path_and_a_handle_off_a_credit_line(self):
        plant = lambda text: codecs.decode(text, "rot13")
        self.assertTrue(denied("skills/tdd/SKILL.md", plant("frr /Hfref/ebz.vyhm/abgrf")))
        self.assertTrue(denied("skills/team-lead/SKILL.md", plant("nfx ebzvyhm13 svefg")))
        self.assertFalse(denied("LICENSE", plant("Pbclevtug (p) 2026 Ebz Vyhm")))


class Provenance(unittest.TestCase):
    def setUp(self):
        self.components = json.loads((REPO / "third_party/provenance.json").read_text())["components"]

    def test_every_vendored_file_has_a_component(self):
        uncovered = [p for p in tracked() if p.startswith(("skills/", "tools/"))
                     and not any(fnmatch.fnmatch(p, g) for c in self.components for g in c["paths"])]
        self.assertEqual(uncovered, [])

    def test_every_component_names_repo_revision_and_licence_text(self):
        for c in self.components:
            with self.subTest(component=c.get("name")):
                self.assertTrue(c.get("repo") and c.get("revision") and c.get("licence"))
                self.assertTrue(c["paths"])
                self.assertTrue((REPO / c["licence_file"]).read_text().strip())

    def test_every_vendored_skill_names_its_upstream_commit_and_tree(self):
        recorded = {name: s for c in self.components for name, s in c.get("skills", {}).items()}
        own = {"engineering-workflow", "team-lead"}
        self.assertEqual(set(recorded), SHIPPED - own)
        for name, s in recorded.items():
            with self.subTest(skill=name):
                self.assertRegex(s["commit"], r"^[0-9a-f]{40}$")
                self.assertRegex(s["tree"], r"^[0-9a-f]{40}$")

    def test_every_changed_apache_file_says_so(self):
        for c in self.components:
            if c["licence"] != "Apache-2.0":
                continue
            changed = [f"skills/{name}/{f}" for name, s in c.get("skills", {}).items()
                       for f in s.get("modified", {}).get("changed", [])]
            changed += [p for p in c["paths"] if p.endswith(".md") and "/*" not in p and "SKILL.md" not in p]
            for rel in changed:
                with self.subTest(file=rel):
                    self.assertIn("Modified by Mifal", (REPO / rel).read_text())


if __name__ == "__main__":
    unittest.main()
