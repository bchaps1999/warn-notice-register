"""One place where a notice picks up everything derived about its employer.

The database stores only what a state published. Identity (SEC CIK, IRS
EIN, LEI, Wikidata QID), industry codes, and the employer key that groups
notices across spelling variants are all derived at export time from the
reference files under data/reference, so a better match on the next
refresh improves every export without touching a stored row.

Identity is tiered by how certain the join is: CIK (era-aware EDGAR name
match), then EIN (IRS roster, name and state), then LEI (GLEIF, name).
The tiers are complementary — public companies, nonprofits, and large
private firms respectively — so an employer usually reaches at most one.

Industry falls back in order of directness: the source's own NAICS code,
an official sector name it printed, the SIC the SEC assigned the CIK
through the Census concordance, then the NTEE activity code the IRS
assigned the EIN. Each is labelled with its basis so consumers can tell a
published code from an inferred one.
"""

from __future__ import annotations

from warnlive.enrich import gleif, nonprofits
from warnlive.enrich.edgar import REFERENCE_PATH, Matcher, load_sic
from warnlive.enrich.industry import industry_from_fields_json, load_sic_naics
from warnlive.enrich.wikidata import load_labels, load_orgs
from warnlive.normalize.engine import normalized_employer

FIELDS = [
    "normalized_name",
    "industry",
    "naics",
    "naics_basis",
    "cik",
    "ticker",
    "cik_match",
    "sic",
    "sic_description",
    "ein",
    "ntee",
    "lei",
    "wikidata_qid",
    "wikidata_match",
    "parent_company",
    "employer_key",
]


class Annotator:
    """Loads the reference files once; annotates any number of notices."""

    def __init__(self) -> None:
        self.matcher = Matcher() if REFERENCE_PATH.exists() else None
        self.sic_by_cik = load_sic()
        self.naics_by_sic = load_sic_naics()
        self.wikidata_by_cik = load_orgs()
        self.wikidata_by_name = load_labels()
        self.nonprofit_by_name = nonprofits.load()
        self.gleif_by_name = gleif.load()

    def annotate(
        self,
        employer_name: str | None,
        date: str | None,
        fields_json: str | None,
    ) -> dict:
        """Derived fields for one notice; every key in FIELDS is present."""
        out = dict.fromkeys(FIELDS)
        norm = normalized_employer(employer_name)
        out["normalized_name"] = norm
        out["industry"], out["naics"], out["naics_basis"] = industry_from_fields_json(
            fields_json
        )

        if self.matcher is not None:
            hit = self.matcher.match(employer_name, int(date[:4]) if date else None)
            if hit:
                out["cik"], out["ticker"], out["cik_match"] = hit[0], hit[1] or None, hit[2]
                sic = self.sic_by_cik.get(out["cik"])
                if sic:
                    out["sic"], out["sic_description"] = sic[0] or None, sic[1] or None
                wd = self.wikidata_by_cik.get(out["cik"])
                if wd:
                    out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "cik"
                    out["parent_company"] = (
                        wd["parents"].split("||")[0] if wd["parents"] else None
                    )

        if norm and not out["cik"]:
            org = self.nonprofit_by_name.get(norm)
            if org:
                out["ein"], out["ntee"] = org["ein"], org["ntee"] or None
            entity = self.gleif_by_name.get(norm)
            if entity:
                out["lei"] = entity["lei"]

        if norm and not out["wikidata_qid"]:
            wd = self.wikidata_by_name.get(norm)
            if wd:
                out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "label"
                out["parent_company"] = (
                    wd["parents"].split("||")[0] if wd["parents"] else None
                )

        if out["naics"] is None and out["sic"] in self.naics_by_sic:
            out["naics"], out["naics_basis"] = self.naics_by_sic[out["sic"]], "sic-crosswalk"
        if out["naics"] is None and out["ntee"]:
            naics = nonprofits.naics_from_ntee(out["ntee"])
            if naics:
                out["naics"], out["naics_basis"] = naics, "ntee"

        # Identity key for employer-level aggregation and /employers pages:
        # the strongest available identity wins (a QID survives renames, a
        # CIK/EIN/LEI survives spelling, a normalized name unifies the rest).
        for prefix, value in (
            ("qid", out["wikidata_qid"]), ("cik", out["cik"]),
            ("ein", out["ein"]), ("lei", out["lei"]),
        ):
            if value:
                out["employer_key"] = f"{prefix}:{value}"
                break
        else:
            out["employer_key"] = f"n:{norm or (employer_name or '').lower()}"
        return out
