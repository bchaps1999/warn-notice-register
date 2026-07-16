"""New York — subclass of warn-transformer's NY transformer.

Upstream maps location to the county alone, which collapses distinct sites
of one employer filing on one day (Knight Facilities: several addresses per
county, all deduped away). Include the street address when present; fall
back to county for older/backfill layouts that lack it.
"""

from __future__ import annotations

from warn_transformer.transformers.ny import Transformer as UpstreamTransformer


class Transformer(UpstreamTransformer):
    """Transform New York raw data for consolidation."""

    fields = dict(
        UpstreamTransformer.fields,
        location=lambda row: ", ".join(
            part
            for part in (
                (row.get("Impacted Site Address") or "").strip(),
                (row.get("Impacted Site County") or row.get("County") or "").strip(),
            )
            if part
        ),
    )
