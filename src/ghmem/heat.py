"""Heat weights — the policy half of deterministic corpus ranking.

The ranking itself is one aggregate query, `Store.heat_scores`; this
module owns only what that query cannot know, the relative weight of
each metric. Weights pass in as parameters, so changing the constants
below never touches SQL.

Calibrated 2026-07-25 against 198 read-only signal labels, and retained:
no fitted vector ranked better than this one, and the fitted vector lost
to it under cross-validation. The defaults were fitted on the micro
corpus, and the fit lives in the micro-craft repo — the result is a
negative one, and the reasoning for keeping numbers that started out as
guesses lives there rather than here.
"""

from ghmem.models import HeatScore
from ghmem.store import Store

# Only `comments` has a coefficient the calibration could resolve. The
# other four sit inside their own minimum detectable effect, so these
# are values the evidence declines to improve on — not values it
# confirms. Reweighting reorders topic-name minting, which
# `record_verdict` makes permanent, so the bar for changing one is a
# demonstrated gain and not a better point estimate.
HEAT_WEIGHTS: dict[str, float] = {
    "comments": 1.0,
    "reactions": 0.5,
    "participants": 2.0,
    "related_items": 3.0,
    "span_years": 1.0,
}


def rank(store: Store) -> list[HeatScore]:
    """Rank the whole corpus by heat under the calibrated weights."""
    return store.heat_scores(HEAT_WEIGHTS)
