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
An employer that is none of these may still be somebody's subsidiary,
which Exhibit 21 of the parent's 10-K reveals.

Where an identity carries an authoritative name — Wikidata's label, the
IRS's registered name, GLEIF's legal name — it is kept as canonical_name,
so a company whose notices are filed as "UNITED" can be shown as United
Airlines without discarding what the state actually wrote.

Industry falls back in order of directness: the source's own NAICS code,
an official sector name it printed, the SIC the SEC assigned the CIK
through the Census concordance, the NTEE activity code the IRS assigned
the EIN, the parent's industry, and finally what the same employer
reported on another notice. Each is labelled with its basis so consumers
can tell a published code from an inferred one.
"""

from __future__ import annotations

from warnlive.enrich import gleif, nonprofits, review, subsidiaries
from warnlive.enrich.edgar import REFERENCE_PATH, Matcher, load_sic
from warnlive.enrich.industry import industry_from_fields_json, load_sic_naics
from warnlive.enrich.wikidata import load_labels, load_orgs
from warnlive.normalize.engine import normalized_employer

FIELDS = [
    "normalized_name",
    "canonical_name",
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
    "parent_cik",
    "identity_source",
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
        self.subsidiaries = subsidiaries.Index()
        self.overrides = review.load_overrides()
        self.naics_by_employer: dict[str, str] = {}

    def prime(self, conn) -> int:
        """Learn each employer's industry from the notices that state one.

        Most states publish an industry on some filings and not others —
        the same employer appears with a NAICS code one year and a blank
        the next. An industry belongs to the employer, not the filing, so
        one notice's code carries to the rest of that employer's notices.
        Employers whose notices disagree are left alone: a conflict means
        either a misparse or a genuinely diversified filer, and neither
        should be resolved by picking one arbitrarily.
        """
        by_key: dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT n.employer_name AS employer_name, "
            "       COALESCE(n.notice_date, n.effective_date) AS d, "
            "       (SELECT v.fields_json FROM notice_versions v "
            "        WHERE v.notice_id = n.id AND v.version = n.current_version"
            "       ) AS fields_json FROM notices n"
        ):
            got = self.annotate(row["employer_name"], row["d"], row["fields_json"])
            if got["naics"]:
                by_key.setdefault(got["employer_key"], set()).add(got["naics"])
        self.naics_by_employer = {
            key: next(iter(codes)) for key, codes in by_key.items() if len(codes) == 1
        }
        return len(self.naics_by_employer)

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

        # An adjudicated identity outranks every automatic tier: it was
        # decided by someone looking at evidence the matcher cannot see.
        decided = self.overrides.get(norm or "")
        if decided:
            out["identity_source"] = "override"
            if decided.get("cik"):
                out["cik"], out["cik_match"] = int(decided["cik"]), "override"
                sic = self.sic_by_cik.get(out["cik"])
                if sic:
                    out["sic"], out["sic_description"] = sic[0] or None, sic[1] or None
                out["ticker"] = (
                    self.matcher.ticker_for(out["cik"]) if self.matcher else None
                )
            for field in ("ein", "lei", "wikidata_qid"):
                if decided.get(field):
                    out[field] = decided[field]
            if out["wikidata_qid"]:
                out["wikidata_match"] = "override"

        if self.matcher is not None and not out["cik"]:
            hit = self.matcher.match(employer_name, int(date[:4]) if date else None)
            if hit:
                out["cik"], out["ticker"], out["cik_match"] = hit[0], hit[1] or None, hit[2]
                sic = self.sic_by_cik.get(out["cik"])
                if sic:
                    out["sic"], out["sic_description"] = sic[0] or None, sic[1] or None
                wd = self.wikidata_by_cik.get(out["cik"])
                if wd:
                    out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "cik"
                    out["canonical_name"] = wd["label"] or None
                    out["parent_company"] = (
                        wd["parents"].split("||")[0] if wd["parents"] else None
                    )

        if norm and not out["cik"]:
            # Not a registrant itself — but Exhibit 21 may show whose
            # subsidiary it is, which supplies both a corporate parent and,
            # failing anything better, that parent's industry.
            owner = self.subsidiaries.parent(norm)
            if owner:
                out["parent_cik"] = int(owner["parent_cik"])
                out["parent_company"] = owner["parent_name"] or None
            org = self.nonprofit_by_name.get(norm)
            if org:
                out["ein"], out["ntee"] = org["ein"], org["ntee"] or None
                out["canonical_name"] = out["canonical_name"] or org["name"] or None
            entity = self.gleif_by_name.get(norm)
            if entity:
                out["lei"] = entity["lei"]
                out["canonical_name"] = (
                    out["canonical_name"] or entity["legal_name"] or None
                )

        if norm and not out["wikidata_qid"]:
            wd = self.wikidata_by_name.get(norm)
            if wd:
                out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "label"
                out["canonical_name"] = wd["label"] or out["canonical_name"]
                out["parent_company"] = (
                    wd["parents"].split("||")[0] if wd["parents"] else None
                )

        if out["naics"] is None and out["sic"] in self.naics_by_sic:
            out["naics"], out["naics_basis"] = self.naics_by_sic[out["sic"]], "sic-crosswalk"
        if out["naics"] is None and out["ntee"]:
            naics = nonprofits.naics_from_ntee(out["ntee"])
            if naics:
                out["naics"], out["naics_basis"] = naics, "ntee"
        if out["naics"] is None and out["parent_cik"]:
            parent_sic = self.sic_by_cik.get(out["parent_cik"], ("", ""))[0]
            if parent_sic in self.naics_by_sic:
                out["naics"] = self.naics_by_sic[parent_sic]
                out["naics_basis"] = "parent-sic"

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

        if out["naics"] is None:
            inherited = self.naics_by_employer.get(out["employer_key"])
            if inherited:
                out["naics"], out["naics_basis"] = inherited, "employer"

        if out["identity_source"] is None and any(
            out[f] for f in ("cik", "ein", "lei", "wikidata_qid")
        ):
            out["identity_source"] = "automatic"
        return out
