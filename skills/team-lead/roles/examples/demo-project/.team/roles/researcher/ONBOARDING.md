# Onboarding — researcher (demo-project override)

This file **replaces** the shipped researcher ONBOARDING.md for this project.
Everything in the shipped folder still applies unless this file says
otherwise — except this file wins wherever they conflict.

Demo-project specifics:

1. Read `docs/research-standards.md` in this repo before your first task —
   this project requires citations in a fixed format.
2. Every research return ends with a **Recommendation** section; that is why
   this project overrides `defaultLatitude` to `propose`.
3. The domain is poorly indexed; the stop rule allows **three** failed search
   strategies, not the shipped two.

Doorbell, brief, and receipt shapes are unchanged — the shared contracts in
`~/.agents/skills/team-lead/references/contracts/` still govern.
