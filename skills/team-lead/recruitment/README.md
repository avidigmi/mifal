# Recruitment flow (spec 0001, ticket 08)

The lead notices the fleet lacks a capability, searches the skill pool, and
pitches a new specialist to the human: who, why, what it enables, cost, and a
recommendation. The human approves or declines. **Hiring is human-reserved,
always** — the tooling structurally enforces it (see "The gate" below).

## Layout

- `recruit.py` — the tool (Python stdlib only): `gaps` / `pitch` / `onboard` /
  `record`
- `pitches/` — durable pitch artifacts, one per gap, versioned by pitch id
  (`p-<date>-<gap-slug>`, suffixed on collision)
- `gaps.md` — append-only ledger of known capability gaps; the current state
  of a gap is its LAST row. A declined or unfilled gap stays visible here as
  OPEN — it is information, not a failure
- `onboarded/<member>/` — effective onboarding output from
  `scripts/resolve_role.py` (ONBOARDING.md, DOORBELL.md, tools.md,
  starter-brief.md, RESOLUTION.md) plus the capability brief that was
  installed into the stewardship registry

## The model

A **mission file** names the capabilities it requires (frontmatter
`capabilities:` comma list, optional `mission:` title and `role:` hire role;
body is free text). Coverage is the FLEET: member capability briefs
(`stewardship/briefs/`, read live via `steward.py`'s loader) plus the
tool ledger. A capability is covered when its tokens are contained in a
claimed capability's or ledgered tool's tokens (or an exact name match). The
skill **pool** (`stewardship/generated/skill-inventory.json`) is the candidate
search space, never coverage — otherwise nothing would ever be a gap. Run
`steward.py build` to refresh the pool view before pitching.

## Commands

```sh
python3 recruitment/recruit.py gaps --mission MISSION.md     # detect; read-only
python3 recruitment/recruit.py pitch --mission MISSION.md    # write pitches for gaps only
python3 recruitment/recruit.py onboard recruitment/pitches/PITCH.md [--project DIR]
python3 recruitment/recruit.py record recruitment/pitches/PITCH.md
```

Exit codes (family convention): 0 success, 1 refusal/failure, 2 usage.

## The pitch artifact

New record type, fixed here (the fleet's other shapes stay with
`references/contracts/`). Markdown with plain frontmatter:

| key | writer | meaning |
|-----|--------|---------|
| `pitch`, `created`, `mission`, `mission-file`, `gap` | lead (tool) | identity and the named gap |
| `candidate-skill`, `proposed-member`, `role` | lead (tool) | who: pool skill, member name, role folder |
| `status` | lead (tool) | `pitched` → `hired` \| `declined` (tool-set AFTER the human decides) |
| `decision` | **human only** | `undecided` → `approved` \| `declined` |
| `decision-reason` | **human only** | the human's words; REQUIRED when declining |
| `decided-by` | **human only** | who decided |

The body carries why (the gap, with fleet coverage evidence), what it enables,
cost (honest, qualitative, in this fleet's terms), the lead's recommendation,
and instructions for deciding.

## The gate

`onboard` and `record` read the decision from the human-edited pitch file —
there is no `--approve` flag and never will be. Both refuse (exit 1) when the
pitch is `undecided`; `onboard` refuses a declined pitch and points at
`record`, and vice versa; a settled pitch (`hired`/`declined`) cannot be
re-run. `record` additionally refuses a reason-less decline: the gap stays on
the ledger WITH the human's reason attached. An approval also requires a
non-empty `decided-by:` — the record names the human who approved.

Trust boundary: the gate guarantees that an explicit approval exists in the
pitch file and that no flag can decide in the human's place. It cannot
authenticate WHO wrote the decision line — this is a local, version-controlled
tree, so edits to `pitches/` are trusted as the human's edits (git history is
the attribution trail).

## On approval

1. `scripts/resolve_role.py` resolves the member's effective onboarding from
   the role folder (shipped defaults + project overrides via `--project`;
   RESOLUTION.md names every source; unknown roles fall back to `_generic`
   with a FLAG).
2. The member's capability brief (claims: the gap + the pool skill) is
   installed through `steward.py brief add` — the steward's validated path,
   including member-name validation. Claims surface in the generated inventory
   on the next `steward.py build`.
3. The pitch's `status:` becomes `hired` with an Outcome section; `gaps.md`
   records the gap FILLED.

## On decline

`record` sets the pitch's `status:` to `declined`, appends the human's reason
verbatim to the pitch's Outcome and to `gaps.md`, and the gap remains listed
as OPEN. Nothing is onboarded, nothing is installed, and the gap stays
visible for the next mission.
