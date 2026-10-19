---
member: <member-name>
reported: <YYYY-MM-DD>
capabilities: <skill-name, skill-name, ...>
tools: <mcp-or-connector-name, ...>
evidence: <what the member demonstrably did, with artifact/commit references>
status: <active | example>
---

Free-text notes: what this member is demonstrably good at, where it struggled,
what the lead should know before assigning. Claims merge into the inventory as
`selfReportedClaims` — they never overwrite the curated view; `steward.py who`
annotates every match with its source.
