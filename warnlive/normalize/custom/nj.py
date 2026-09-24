"""New Jersey transformer with corrected effective-date parsing.

The source reports a yearless posting month, but no notice date. Keep the
canonical notice date null; source evidence is extracted separately.
"""

from __future__ import annotations

from warn_transformer.transformers.nj import Transformer as UpstreamTransformer

from warnlive.normalize.details import nj_effective_pair


class Transformer(UpstreamTransformer):
    """Transform New Jersey raw data for consolidation."""

    # Upstream's slash formats use ``%M`` (minute) where they intend ``%m``
    # (month). Keep its ISO timestamp format, but replace those broken formats
    # so values such as 4/15/05 do not silently become January 15.
    date_format = [
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
    ]

    def transform_date(self, value: str) -> str | None:
        text = value.strip()
        # Keep explicit upstream corrections authoritative, including its
        # irregular multi-date cells and deliberately null corrections.
        if text not in self.date_corrections:
            pair = nj_effective_pair(text)
            if pair is not None:
                return pair[1] if pair[0] != "review" else None
        return super().transform_date(value)
