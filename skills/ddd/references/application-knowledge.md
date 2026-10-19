# Two worked application-knowledge examples

These are illustrative records, not executed validation results or required
file layouts. Keep only sections that help the actual task. A small explanation
can live directly in the existing entry point; links below show the relationships,
not files that every project must create.

## Small change: a settings default

An existing Python application's README already links `docs/settings.md`.
Its short map says: the startup command loads settings, then passes them to the
worker; the Python version is pinned in `.python-version`; error handling is
explained in `docs/settings.md`; deployment has not been checked.

Task: use 30 seconds when `timeout` is absent, preserving an explicit zero.
Read the existing explanation and `settings.py:load_settings`, check the actual
Python version, and consult the applicable shipped or official documentation for
[`dict.get`](https://docs.python.org/3/library/stdtypes.html#dict.get). It returns
the default only when the key is absent; truthiness-based fallback would change zero.
The existing loader suffices: no dependency, helper abstraction, hierarchy, or
integration snippet is needed.

After implementing and running focused checks, the current explanation might be:

> An absent timeout defaults to 30 seconds so startup has a bounded wait;
> explicit zero retains its accepted meaning. Python's `dict.get` contract
> supports absence-only fallback. See `settings.py:load_settings` and
> `test_settings.py:test_timeout_default` / `test_explicit_zero`. Record the
> actual checked revision and result here; deployment remains unverified.
> Investigation: `.ddd/notes/settings-default.md`.

The task note preserves the decision and returned check results. A fresh task
adding a timeout range check discovers this explanation through the README,
rechecks applicability, and adds only the new rule and evidence. A characterization
test for already-correct explicit-zero handling does not need an artificial bug.

## Integration: client filtering and an API

An existing guide links the browser list, HTTP handler, and storage query.
The map points to the lockfile for actual runtime/framework versions and to the
accepted API contract. Task: filter records by `eventId`. Accepted intent says
an absent parameter means all public records; empty or malformed values must be
rejected, and private records must never be returned.

Read both ends: client query construction and server validation, plus storage
permissions. Check version-matched URL parsing, handler error propagation, and
query-binding documentation. The [URL Standard's URLSearchParams
methods](https://url.spec.whatwg.org/#interface-urlsearchparams) distinguish
presence from value; the accepted API contract, not that standard, establishes
which values are valid and what error response to return.

A consequential adapted snippet in the task plan may show presence handling:

```js
// Proposed code (untested); adapt to the project's actual handler contract.
const url = new URL(request.url);
const hasEvent = url.searchParams.has('eventId');
const eventId = url.searchParams.get('eventId');
```

This snippet establishes neither validation nor authorization. Trace both through
the existing handler and query. If an unfamiliar runtime's conversion remains
unclear after source inspection, probe absent, empty, and repeated values in a
disposable directory. Record runtime/configuration, inputs, returned values, and
limits. Those observations do not define the product's repeated-value policy.
If that policy matters and lacks authority, keep dependent work incomplete while
finishing independent public-record checks.

Once built and checked, retain a compact current explanation:

| Behavior and rationale | Applicable basis | Implementation | Check and limit |
|---|---|---|---|
| Absent filter preserves browsing; empty is rejected | Accepted API decision + applicable URL parsing contract | `client/list:buildQuery`, `api/list:parseFilter` | Absent/empty/malformed request tests; record actual results and revision |
| Filtering cannot expose private records | Accepted access rule + version-matched query API | `storage/records:listPublic` | Public/private integration fixture; production permissions not checked |

Link the investigation note instead of copying its probes. A new export task can
reuse the public-record explanation after checking current code and versions.
If a later accepted requirement changes empty to mean all, update the rule,
handler, tests, and explanation together; preserve the original note as history.
If filtering is later removed, retire its current guidance and links, preserving
historical evidence. A read-only review reports stale guidance without editing it.
