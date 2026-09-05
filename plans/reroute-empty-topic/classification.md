# Classification — reroute-empty-topic

- **Classification:** Moderate
- **Implementation certainty:** High — `_release_topic` already exists and is
  tested for `unroute`; the candidate change was probe-run over the full suite
- **Requirement stability:** High — my human partner settled the open question
  ("Yes it should delete an empty topic")
- **Behavioral code check:** Yes — changes a logic path in
  `_apply_route_event`, and moves a call across a `session.merge` boundary
- **Work type:** Production
- **Artifact destination:** production (`src/`, `tests/`), plus investigation
  (`docs/ghmem-design.md`, `docs/ghmem-decisions.md`, `docs/ghmem-changelog.md`)
- **Evidence:** `routing_reduce.py:126-140` (route branch), `:239-278`
  (`_release_topic`), `docs/ghmem-decisions.md:120-129`, and a reverted probe
  that enumerated the exact red set (5 tests) rather than trusting the design
  doc's hand-written count
