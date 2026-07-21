"""Heat weights — the policy half of deterministic corpus ranking.

The ranking itself is one aggregate query, `Store.heat_scores`; this
module owns only what that query cannot know, the relative weight of
each metric. Weights pass in as parameters, so recalibrating them in
subproject 4 changes the constants below and no SQL.
"""

from ghmem.models import HeatScore
from ghmem.store import Store

HEAT_WEIGHTS: dict[str, float] = {
    "comments": 1.0,  # PROVISIONAL — calibrated in subproject 4
    "reactions": 0.5,  # PROVISIONAL — calibrated in subproject 4
    "participants": 2.0,  # PROVISIONAL — calibrated in subproject 4
    "related_items": 3.0,  # PROVISIONAL — calibrated in subproject 4
    "span_years": 1.0,  # PROVISIONAL — calibrated in subproject 4
}


def rank(store: Store) -> list[HeatScore]:
    """Rank the whole corpus by heat under the calibrated weights."""
    return store.heat_scores(HEAT_WEIGHTS)
