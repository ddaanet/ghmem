# Recall Artifact: unroute event

Read each file listed below — do not rely on inline summaries.

## Entries

memory/ghmem-project.md — the project spine; §"Routing-state architecture" fixes the append-ledger-then-reduce contract every new event must follow, and the 2026-07-26 "#1445 re-routed" entry records the existing correction path (append a `route` event + `rebuild`) that this event is an alternative to.
memory/ddaanet/green-is-not-evidence.md — names the exact test shapes this work risks: "idempotence standing in for teardown" (an unroute test that rebuilds twice proves nothing about the DELETE) and "satisfied by birth state" (a topic is created dirty, so asserting dirty-after-unroute passes with the dirtying branch gone).
memory/ddaanet/instruction-names-absent-data.md — the root cause of the 22 mis-routed items being re-triaged; explains why the packet's topic list had to be rendered inside the budgeted body.
memory/ddaanet/guardrail-must-permit-real-commands.md — the allowlist hook must be re-verified against a real `ghmem unroute` string expecting ALLOW, and against a must-block case expecting DENY.
memory/ddaanet/frontmatter-hooks-two-silent-gates.md — dispatching the re-triage workers afterwards: spawn by `subagent_type` with no custom `name`, or the containment hook registers nothing.
memory/ddaanet/plan-contracts-not-full-code.md — constrains the downstream /design and /runbook artifacts to contract-style, overriding superpowers:writing-plans.
