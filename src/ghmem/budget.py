"""Triage batch budget — the policy half of the session loop.

`triage.serve_next` is the mechanism; this module owns only what it
cannot know, how much one batch is allowed to cost before it ends and
hands control back to the orchestrator.

Both constants are PROVISIONAL, calibrated in subproject 4, mirroring
`heat.py`'s `HEAT_WEIGHTS`. Twelve items comes from the spec's own
reasoning: fixed per-spawn overhead amortizes out somewhere around
5-12 items. The character cap is softer — hot threads run 8-11k
tokens each, so a hot batch ends on this gate after four or five
items, while a cold one runs to the item cap instead. That asymmetry
is intended: heat order serves the biggest threads first.
"""

BATCH_ITEMS = 12  # PROVISIONAL — calibrated in subproject 4
BATCH_CHARS = 200_000  # PROVISIONAL — calibrated in subproject 4
