import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("MIFAL_TEST_PYTHON", shutil.which("python3"))
ROOT_VARIABLES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PI_CODING_AGENT_DIR", "FACTORY_HOME_OVERRIDE",
                  "OPENCODE_CONFIG_DIR", "OPENCODE_TEST_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                  "XDG_CACHE_HOME", "XDG_STATE_HOME", "HERDR_INSTALL_DIR")
# OpenCode config inputs that can add instruction files; a test never sets them.
OPENCODE_CONFIG_INPUTS = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT")
MANIFEST = ".agents/mifal/install-manifest.json"


class RefusedEnvironment(Exception):
    pass


def snapshot(home):
    """Every entry under home: files by sha256, symlinks by target, directories by name."""
    home = Path(home)
    state = {}
    for root, dirs, files in os.walk(home, followlinks=False):
        for name in dirs + files:
            path = Path(root, name)
            rel = str(path.relative_to(home))
            if path.is_symlink():
                state[rel] = "link:" + os.readlink(path)
            elif path.is_dir():
                state[rel] = "dir"
            else:
                state[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def changed(before, after):
    return {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


class Sandbox(unittest.TestCase):
    """A disposable HOME and a disposable clone of the repo, with PATH limited to what a test puts there."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mifal-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.fakebin = self.root / "fakebin"
        self.fakebin.mkdir()
        os.symlink(PYTHON, self.fakebin / "python3")
        self.calls = self.root / "calls.log"
        self.fake("herdr", 'if [ "$1" = "--version" ]; then echo "herdr 0.9.5"; fi')
        self.clone = self.root / "clone"
        shutil.copytree(REPO, self.clone, ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def fake(self, name, body):
        """A fake command on PATH that logs its arguments, then runs body."""
        path = self.fakebin / name
        path.write_text(f'#!/bin/bash\necho "{name} $*" >> "{self.calls}"\n{body}\n')
        path.chmod(0o755)

    def unfake(self, name):
        (self.fakebin / name).unlink()

    def called(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def run_in_terminal(self, *args, answer, **env):
        """Run with stdin attached to a pseudo-terminal, typing answer."""
        env = self.env(**env)
        primary, secondary = os.openpty()
        os.write(primary, answer.encode())
        try:
            return subprocess.run([str(a) for a in args], cwd=self.root, env=env, stdin=secondary,
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.close(primary)
            os.close(secondary)

    def env(self, unchecked=(), outside=(), **extra):
        """The command's whole environment. A root variable must be an absolute path inside the test HOME;
        one named in unchecked may be empty or relative, so a test can prove the installer refuses it,
        and then resolves under the disposable test root; one named in outside may lie in the disposable
        test root outside the HOME."""
        env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.fakebin), "/usr/bin", "/bin"]),
            "LANG": "C",
        }
        env.update(extra)
        for name in OPENCODE_CONFIG_INPUTS:
            if name in env:
                raise RefusedEnvironment(f"{name} can add instructions from outside the test HOME")
        home = os.path.realpath(self.home)
        for name in ROOT_VARIABLES:
            if name not in env:
                continue
            value = env[name]
            if name in unchecked and not os.path.isabs(value):
                continue
            if value == "" and name == "XDG_CONFIG_HOME":
                continue
            if not os.path.isabs(value):
                raise RefusedEnvironment(f"{name}={value!r} is empty or relative")
            if name in outside and os.path.realpath(value).startswith(os.path.realpath(self.root) + os.sep):
                continue
            if not (os.path.realpath(value) + os.sep).startswith(home + os.sep):
                raise RefusedEnvironment(f"{name}={value} lies outside the test HOME {home}")
        return env

    def run_cmd(self, *args, input_text=None, check=True, unchecked=(), outside=(), **env):
        env = self.env(unchecked, outside, **env)
        result = subprocess.run(
            [str(a) for a in args], cwd=self.root, env=env, input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        if check and result.returncode != 0:
            self.fail(f"{args} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def harness_fakes(self, *names):
        for name in names:
            self.fake(name, "")

    def install(self, *args, **kw):
        return self.run_cmd("/bin/bash", self.clone / "install.sh", *args, **kw)

    def mifal(self, *args, **kw):
        return self.run_cmd("/bin/bash", self.home / ".local/bin/mifal", *args, **kw)

    def clone_mifal(self, *args, **kw):
        return self.run_cmd("/bin/bash", self.clone / "bin/mifal", *args, **kw)

    def manifest(self):
        return json.loads((self.home / MANIFEST).read_text())

    def release(self, version, **files):
        """Turn the clone into another release: a new VERSION, and grilling files added, changed or (None) removed."""
        (self.clone / "VERSION").write_text(version + "\n")
        for name, text in files.items():
            path = self.clone / "skills/grilling" / name
            if text is None:
                path.unlink()
            else:
                path.write_text(text)

    def their_grilling(self):
        theirs = self.home / ".agents/skills/grilling/SKILL.md"
        theirs.parent.mkdir(parents=True)
        theirs.write_text("a different grilling skill\n")
        return theirs
