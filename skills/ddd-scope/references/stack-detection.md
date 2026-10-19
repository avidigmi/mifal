# Where to look

Follow the task's behavior through code and callers; use this map to spot missing
questions. Select only rows that could change the implementation or its checks.
These are possible sources, not artifacts to create or a required inventory.

| If the change depends on… | Questions to answer | Sources to inspect |
|---|---|---|
| Product or domain behavior | What outcome, permission, state change, or invariant is required? | User request, accepted specification, relevant issue, project policy or decision. |
| Frameworks, packages, services, databases | Which API, setup, limits, lifecycle, or failure behavior applies? | Official versioned docs, contracts, release notes, shipped types/source. |
| Language and standard library | Do async execution, mutation, types, encoding, exceptions, or cleanup affect correctness? | Applicable language/runtime and standard-library reference sections. |
| Connections between components | What crosses the boundary; who owns completion, retries, errors, permissions, and resource lifetime? | Both components' contracts, protocol docs, caller/callee code, existing integration tests. |
| Build, deployment, operations, security | Which environment, configuration, platform limits, or existing policies affect this behavior? | Project config/runbooks, official platform docs, accepted policies, relevant diagnostics. |
| Compatibility and maintainability | Which callers, formats, conventions, or prior decisions must remain supported? | Existing contracts/tests, project guidance, relevant history, documented patterns and known traps. |

For example, “add sign-in” may lead from an authentication provider through the
framework's session handling to storage and deployment. Investigate the actual
connections; do not assume every sign-in task uses the same stack or policy.
Existing code and runtime observations describe current behavior and can expose
contradictions. They do not automatically determine intended behavior.

## Resolve the versions used by this task

Inspect imports/configuration and relevant package/runtime metadata. A manifest
range is a constraint; lockfiles record a resolution; installed metadata identifies
what local checks execute. Resolve from the package that owns the change.

| Ecosystem | Useful local sources |
|---|---|
| JavaScript / TypeScript | package.json, npm/pnpm/yarn/Bun lockfile, installed package.json, declarations, runtime version |
| Python | pyproject.toml or requirements, uv/Poetry lockfile, installed package metadata, Python version |
| Rust | Cargo.toml, Cargo.lock, local crate source, compiler version |
| Go | go.mod/go.work, selected modules, toolchain version; go.sum records checksums, not selected versions |
| Other languages | Native manifest, resolved/installed dependencies, language and runtime/compiler versions |

If installation and lockfile disagree, identify the intended target before choosing
an API. Check official versioned docs, release notes, tagged source, and shipped
material for applicability. Preserve existing versions unless an upgrade is
requested. Record unresolved version questions in the task's Documentation basis.
