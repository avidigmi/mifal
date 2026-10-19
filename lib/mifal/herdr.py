"""Herdr is required: find it, check its version, and install it only through a route the user can see.

Herdr is an external dependency. Mifal records how it was found or installed, and never removes it.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MINIMUM = (0, 9, 5)
SCRIPT_URL = "https://herdr.dev/install.sh"
TAMPER = "tamper-herdr-script"


@dataclass
class Requirement:
    status: str  # "ok", "old", "broken" or "missing"
    version: tuple | None = None
    path: str | None = None

    def describe(self, show=str) -> str:
        if self.status == "ok":
            where = "" if shutil.which("herdr") else " (not on your PATH)"
            return f"herdr {_dotted(self.version)} found at {show(self.path)}{where}"
        if self.status == "old":
            return (f"herdr {_dotted(self.version)} at {show(self.path)} is older than {_dotted(MINIMUM)}; "
                    f"upgrade it with: {upgrade_command(self.path)}")
        if self.status == "broken":
            return (f"{show(self.path)} does not report a Herdr version; Herdr's script would overwrite it. "
                    "Move it aside or fix it, then run the installer again.")
        return "herdr not found"


def _dotted(version) -> str:
    return ".".join(str(n) for n in version)


def script_target() -> Path:
    """Where Herdr's official script installs the binary."""
    return Path(os.environ.get("HERDR_INSTALL_DIR") or Path.home() / ".local" / "bin") / "herdr"


def check() -> Requirement:
    path = shutil.which("herdr")
    if not path:
        if not os.path.lexists(script_target()):
            return Requirement("missing")
        path = str(script_target())
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
    except OSError:
        return Requirement("broken", path=path)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
    if not m:
        return Requirement("broken", path=path)
    version = tuple(int(n) for n in m.groups())
    return Requirement("ok" if version >= MINIMUM else "old", version, path)


def upgrade_command(path: str) -> str:
    real = os.path.realpath(path)
    if "/Cellar/" in real or real.startswith(("/opt/homebrew/", "/usr/local/Homebrew/")):
        return "brew upgrade herdr"
    if "/mise/" in real:
        return "mise upgrade herdr"
    return "herdr update"


def install_route(allow_script: bool) -> str:
    """How a missing Herdr gets installed: 'brew', 'script', 'ask' or 'refuse'."""
    if shutil.which("brew"):
        return "brew"
    if allow_script:
        return "script"
    if sys.stdin.isatty():
        return "ask"
    return "refuse"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure(allow_script: bool, dry_run: bool, out, err, previous=None, show=str):
    """The Manifest's record of Herdr, installing it on a real run when missing; None means the install must stop."""
    req = check()
    if req.status == "ok":
        out(req.describe(show))
        return _found(req, previous)
    if req.status != "missing":
        err(f"mifal: {req.describe(show)}")
        return None
    route = install_route(allow_script)
    script_words = (f"download Herdr's official install script from {SCRIPT_URL}, show its sha256, "
                    f"and run that same file (it installs Herdr's latest release into {show(script_target())})")
    if dry_run:
        out({"brew": "herdr not found: would run: brew install herdr",
             "script": f"herdr not found: would {script_words}",
             "ask": f"herdr not found: would ask, then {script_words}",
             "refuse": "herdr not found: a real install stops here; Homebrew is absent, so pass --herdr-script to "
                       + script_words}[route])
        return {}
    if route == "refuse":
        err("mifal: Herdr is required and Homebrew is not installed. To install Herdr with its official script, "
            f"rerun with --herdr-script, which will {script_words}.")
        return None
    if route == "brew":
        out("installing herdr: brew install herdr")
        status = subprocess.run(["brew", "install", "herdr"]).returncode
        if status != 0:
            err(f"mifal: brew install herdr failed (exit {status}); nothing of Mifal's was written.")
            return None
        record = {"route": "brew"}
    else:
        record = _run_script(route == "ask", out, err, show)
        if record is None:
            return None
    after = check()
    if after.status != "ok":
        err(f"mifal: after installing Herdr, {after.describe(show)}")
        return None
    out(after.describe(show))
    return dict(record, installed_by_mifal=True, version=_dotted(after.version), path=after.path)


def _run_script(ask: bool, out, err, show):
    target = script_target()
    with tempfile.TemporaryDirectory(prefix="mifal-herdr-") as tmp:
        script = Path(tmp) / "install.sh"
        if subprocess.run(["curl", "-fsSL", "-o", str(script), SCRIPT_URL]).returncode != 0 or not script.is_file():
            err(f"mifal: could not download {SCRIPT_URL}; nothing was changed.")
            return None
        digest = _sha256(script)
        out(f"Herdr is required and Homebrew is not installed. Herdr's official install script was downloaded from "
            f"{SCRIPT_URL}\n  sha256 {digest}\nIt installs Herdr's latest release into {show(target)}.")
        if ask:
            out("Run it now? [y/N] ")
            if sys.stdin.readline().strip().lower() not in ("y", "yes"):
                err("mifal: Herdr was not installed; nothing was changed. Install Herdr, then run ./install.sh again.")
                return None
        if os.environ.get("MIFAL_TEST_FAIL_AT") == TAMPER:
            with script.open("a") as f:
                f.write("\n# changed after it was shown\n")
        if _sha256(script) != digest:
            err("mifal: Herdr's install script changed after it was shown, so it was not run; nothing was changed.")
            return None
        out(f"running Herdr's install script (sha256 {digest})")
        status = subprocess.run(["/bin/sh", str(script)]).returncode
    if status != 0:
        left = f" It left {show(target)}, which Mifal did not remove." if os.path.lexists(target) else ""
        err(f"mifal: Herdr's install script failed (exit {status}); nothing of Mifal's was written.{left}")
        return None
    return {"route": "script", "script_sha256": digest}


def _found(req: Requirement, previous):
    if previous and previous.get("installed_by_mifal"):
        return dict(previous, version=_dotted(req.version), path=req.path)
    return {"route": "found", "installed_by_mifal": False, "version": _dotted(req.version), "path": req.path}


def removal_hint(record, show=str):
    """What uninstall says about Herdr, which it never removes."""
    if not (record and record.get("installed_by_mifal")):
        return None
    if record["route"] == "brew":
        return "Herdr stays installed. Mifal installed it with Homebrew; to remove it: brew uninstall herdr"
    return f"Herdr stays installed. Mifal installed it with its official script; to remove it: rm {show(record['path'])}"
