# Compare a documented rule with code

Open the cited official section for the target version and read its conditions.
Locate the actual use in the finished code. Compare the behavior in context,
including configuration, runtime, and interactions that could change the rule.

| Relationship | Action |
|---|---|
| The source explicitly supports this use | Record the applicable section and any conditions. |
| The use follows from several documented rules | Cite each and explain the inference; check the connecting assumptions. |
| The source does not address this use | Research versioned docs, release notes, official source, or shipped types. Keep the gap visible if unresolved. |
| The source contradicts the code | Correct the authorized change, rerun affected checks, and compare again. |

Review every relevant documented API use in the change. Prioritize risky
interactions first, but do not replace the remaining comparison with sampling.
Check the statement itself: a real URL or a nearby example is insufficient.
Distinguish a documented guarantee from an inference or an observed test result.

Record findings in the task's Documentation basis. A review-only request reports
corrections for the user; an implementation request applies authorized fixes.
An unresolved relevant mismatch prevents a clean completion claim. This is an
agent comparison, not automated semantic proof or repository-wide conformance.
