"""Rhode Island — subclass of warn-transformer's RI transformer.

Upstream maps the WARN date to `date="WARN Date"`, but the schema only ever
reads `fields["notice_date"]`, so every RI notice lands with a null
notice_date. Re-map the same source column under the key the schema uses.
"""

from __future__ import annotations

from warn_transformer.transformers.ri import Transformer as UpstreamTransformer


class Transformer(UpstreamTransformer):
    """Transform Rhode Island raw data for consolidation."""

    fields = dict(
        UpstreamTransformer.fields,
        notice_date="WARN Date",
    )
