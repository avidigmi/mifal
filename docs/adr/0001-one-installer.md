# One installer owns every write; no plugin manifests in v1

Mifal ships as one installer and one `mifal` CLI that write the Pool, the engine, each Harness's Guide wiring and Herdr, and record all of it in the Manifest. We considered native plugins (Claude, Codex) plus a smaller installer. We chose one owner because a Claude plugin cannot deliver a global Guide (a plugin-root CLAUDE.md is not loaded), Droid, Pi and OpenCode have no plugin route for the Guide or Herdr, and two owners of one workflow (a plugin cache and the Pool) can drift apart in versions and leave one path active after the other is uninstalled.

Consequence: no native `/plugin` front door in v1. Reopen if Claude-only users cannot get from the README to a green doctor without help; a launcher that delegates to the installer can add the front door.
