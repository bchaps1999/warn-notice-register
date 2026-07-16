"""Kentucky — subclass of warn-transformer's KY transformer.

Upstream maps notice_date to `date_effective` and effective_date to
`date_received` — swapped. KY's raw columns are unambiguous (a 2026-06-12
received date with a 2026-08-30 effective date was showing up here as a
notice filed in the future).
"""

from __future__ import annotations

from warn_transformer.transformers.ky import Transformer as UpstreamTransformer


class Transformer(UpstreamTransformer):
    """Transform Kentucky raw data for consolidation."""

    fields = dict(
        UpstreamTransformer.fields,
        notice_date="date_received",
        effective_date="date_effective",
    )
