"""Checked insight text: sentences from computed numbers, verified twice.

Muse Spark writes a headline, a 3–4 sentence snapshot and per-alert impact
sentences from a JSON of computed facts only (:mod:`app.insight.facts`). Then
two independent gates verify every sentence against the same JSON:

1. a deterministic number matcher (:mod:`app.insight.numbers`) — any figure
   that does not appear in the facts (within rounding tolerance) fails;
2. the Spark Open sentence verifier (:mod:`app.insight.check`), which reuses
   the claims-check provider path from :mod:`app.agent.claims`.

A sentence that fails either gate is dropped and the refusal stored. Results
are cached by input hash in ``credit_insight`` (see :mod:`app.insight.store`).
No investment advice, ratings or buy/sell language — describe, don't recommend.
"""

from app.insight.generate import generate_impact, generate_snapshot

__all__ = ["generate_impact", "generate_snapshot"]
