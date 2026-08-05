"""Washington — subclass of warn-transformer's WA transformer.

Upstream maps both notice_date and effective_date to `Layoff Start Date`,
discarding the `Received Date` column entirely — so every WA notice showed
a "notice date" equal to its layoff date, including dates months in the
future. The raw feed carries both columns on every row.
"""

from __future__ import annotations

from warn_transformer.transformers.wa import Transformer as UpstreamTransformer


class Transformer(UpstreamTransformer):
    """Transform Washington raw data for consolidation."""

    fields = dict(
        UpstreamTransformer.fields,
        notice_date="Received Date",
    )
