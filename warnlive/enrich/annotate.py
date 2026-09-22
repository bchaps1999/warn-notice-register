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
from warnlive.enrich.industry import (
    industry_from_fields_json,
    load_industry_overrides,
    load_sic_naics,
    naics_level,
)
from warnlive.enrich.wikidata import load_labels, load_orgs
from warnlive.normalize.engine import base_employer, normalized_employer

FIELDS = [
    "normalized_name",
    "canonical_name",
    "canonical_basis",
    "industry",
    "naics",
    "naics_basis",
    "naics_level",
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
        self.override_rows = review.load_override_rows()
        self.industry_overrides = load_industry_overrides()
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
            "SELECT n.employer_name AS employer_name, n.state AS state, "
            "       n.location AS location, "
            "       COALESCE(n.notice_date, n.effective_date) AS d, "
            "       (SELECT v.fields_json FROM notice_versions v "
            "        WHERE v.notice_id = n.id AND v.version = n.current_version"
            "       ) AS fields_json FROM notices n"
        ):
            got = self.annotate(
                row["employer_name"], row["d"], row["fields_json"],
                state=row["state"], location=row["location"],
            )
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
        *,
        state: str | None = None,
        location: str | None = None,
    ) -> dict:
        """Derived fields for one notice; every key in FIELDS is present."""
        out = dict.fromkeys(FIELDS)
        norm = normalized_employer(employer_name)
        out["normalized_name"] = norm
        # The same retry the CIK matcher makes: a filed name that names a
        # site rather than a company still identifies the company once the
        # site is set aside. Reference files are keyed on the filed name's
        # normalization, so both forms are tried rather than re-keyed.
        base_norm = normalized_employer(base_employer(employer_name))
        names = [n for n in (norm, base_norm) if n]

        def lookup(table: dict):
            for candidate in names:
                hit = table.get(candidate)
                if hit:
                    return hit
            return None
        out["industry"], out["naics"], out["naics_basis"] = industry_from_fields_json(
            fields_json
        )

        # An adjudicated identity outranks every automatic tier: it was
        # decided by someone looking at evidence the matcher cannot see.
        year = date[:4] if date else None
        exact_decision = review.select_override(
            self.override_rows, norm, state, year, location
        )
        base_decision = review.select_override(
            self.override_rows, base_norm, state, year, location
        ) if base_norm and base_norm != norm else None
        id_fields = ("cik", "ein", "lei", "wikidata_qid")
        conflicting_decisions = bool(
            exact_decision and base_decision
            and any((exact_decision.get(f) or "") != (base_decision.get(f) or "")
                    for f in id_fields)
        ) or review.conflicting_override(
            self.override_rows, norm, state, year, location
        ) or review.conflicting_override(
            self.override_rows, base_norm, state, year, location
        )
        decided = None if conflicting_decisions else exact_decision or base_decision
        matcher_hit = (
            self.matcher.match(employer_name, int(year))
            if self.matcher is not None and year and year.isdigit()
            else None
        )
        # Legacy name-wide CIK overrides are preserved but no longer grant
        # identity. If an automatic match contradicts one, quarantine the
        # notice instead of silently flipping it to a different registrant.
        legacy_ciks = {
            int(row["cik"])
            for name in {n for n in (norm, base_norm) if n}
            for row in self.override_rows.get(name, [])
            if row.get("cik")
            and not any(row.get(f) for f in ("scope_state", "scope_year", "scope_location"))
            and str(row["cik"]).isdigit()
        }
        if len(legacy_ciks) > 1 or (
            matcher_hit and legacy_ciks and matcher_hit[0] not in legacy_ciks
        ):
            conflicting_decisions = True
            decided = None
        if decided and decided.get("cik") and matcher_hit and matcher_hit[2].startswith("exact"):
            if int(decided["cik"]) != matcher_hit[0]:
                conflicting_decisions = True
                decided = None
        if conflicting_decisions:
            out["identity_source"] = "conflict"
        # A rejection is recorded so it is not re-decided; it grants nothing.
        if decided and any(
            decided.get(f) for f in ("cik", "ein", "lei", "wikidata_qid")
        ):
            out["identity_source"] = "override"
            if decided.get("cik"):
                out["cik"], out["cik_match"] = int(decided["cik"]), "override"
                sic = self.sic_by_cik.get(out["cik"])
                if sic:
                    out["sic"], out["sic_description"] = sic[0] or None, sic[1] or None
                out["ticker"] = (
                    self.matcher.ticker_for(out["cik"]) if self.matcher else None
                )
                # The same CIK-keyed joins the automatic tier gets. Without
                # them, an adjudicated identity — the strongest kind — was
                # left for the name-keyed label tier to decorate, i.e. the
                # weakest matcher dressing the best-attested employers.
                wd = self.wikidata_by_cik.get(out["cik"])
                if wd:
                    out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "cik"
                    out["canonical_name"] = wd["label"] or None
                    if out["canonical_name"]:
                        out["canonical_basis"] = "wikidata"
                    out["parent_company"] = (
                        wd["parents"].split("||")[0] if wd["parents"] else None
                    )
            for field in ("ein", "lei", "wikidata_qid"):
                if decided.get(field):
                    out[field] = decided[field]
            if decided.get("wikidata_qid"):
                out["wikidata_match"] = "override"

        if self.matcher is not None and not out["cik"] and not conflicting_decisions:
            hit = matcher_hit or self.matcher.match(
                employer_name, int(year) if year and year.isdigit() else None
            )
            if hit:
                out["cik"], out["ticker"], out["cik_match"] = hit[0], hit[1] or None, hit[2]
                sic = self.sic_by_cik.get(out["cik"])
                if sic:
                    out["sic"], out["sic_description"] = sic[0] or None, sic[1] or None
                wd = self.wikidata_by_cik.get(out["cik"])
                if wd:
                    out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "cik"
                    out["canonical_name"] = wd["label"] or None
                    if out["canonical_name"]:
                        out["canonical_basis"] = "wikidata"
                    out["parent_company"] = (
                        wd["parents"].split("||")[0] if wd["parents"] else None
                    )

        if norm and not out["cik"]:
            # Parent links affect both the displayed corporate family and
            # inherited industry. Only an exact SEC filing in the notice era
            # is independent evidence. Older model-written overrides remain
            # on disk for review, but cannot assert ownership at export.
            owners = [self.subsidiaries.sec_by_name[n] for n in names
                      if n in self.subsidiaries.sec_by_name]
            owner = owners[0] if owners and len({o["parent_cik"] for o in owners}) == 1 else None
            try:
                notice_year = int(date[:4]) if date else None
                source_year = int(owner["source_year"]) if owner else None
            except (TypeError, ValueError):
                notice_year = source_year = None
            if notice_year is None or source_year is None or abs(notice_year - source_year) > 2:
                owner = None
            scoped_owners = [
                o for n in names
                if (o := self.subsidiaries.scoped_parent(n, state, year, location))
            ]
            scoped_conflict = any(
                self.subsidiaries.scoped_parent_conflict(n, state, year, location)
                for n in names
            ) or len({o["parent_cik"] for o in scoped_owners}) > 1
            scoped_owner = (
                scoped_owners[0]
                if scoped_owners and len({o["parent_cik"] for o in scoped_owners}) == 1
                else None
            )
            if scoped_conflict or (
                scoped_owner and owner and scoped_owner["parent_cik"] != owner["parent_cik"]
            ):
                owner = None  # conflicting authoritative claims need review
            else:
                owner = scoped_owner or owner
            if owner:
                out["parent_cik"] = int(owner["parent_cik"])
                out["parent_company"] = owner["parent_name"] or None
            org = lookup(self.nonprofit_by_name)
            if org and (not out["ein"] or out["ein"] == org["ein"]):
                out["ein"] = org["ein"]
                out["ntee"] = org["ntee"] or None
                if not out["canonical_name"] and org["name"]:
                    out["canonical_name"], out["canonical_basis"] = org["name"], "irs"
            entity = lookup(self.gleif_by_name)
            if entity and (not out["lei"] or out["lei"] == entity["lei"]):
                out["lei"] = entity["lei"]
                if not out["canonical_name"] and entity["legal_name"]:
                    out["canonical_name"] = entity["legal_name"]
                    out["canonical_basis"] = "gleif"

        # The label tier is the weakest matcher here — an exact-normalized
        # name against Wikidata's org labels — and it must never speak over
        # a CIK. Left unguarded it did: a CIK-identified employer with no
        # wikidata_by_cik row picked up a namesake's QID by name, which then
        # replaced canonical_name, parent_company, and (because the key loop
        # below prefers QIDs) the employer_key the site groups on.
        if norm and not out["wikidata_qid"] and not out["cik"]:
            wd = lookup(self.wikidata_by_name)
            if wd:
                out["wikidata_qid"], out["wikidata_match"] = wd["qid"], "label"
                # A name-only label must not replace a legal name obtained
                # from an EIN or LEI, nor an evidenced corporate parent.
                if wd["label"] and not out["canonical_name"]:
                    out["canonical_name"], out["canonical_basis"] = wd["label"], "wikidata"
                # A label-only QID is a name candidate, not independent
                # ownership evidence. Its parent claim stays out of the
                # asserted parent fields.

        # Every notice gets a company name, whether or not anyone identified
        # the company.
        #
        # A state files the same firm as "Ford Motor Co. - Flat Rock", "FORD
        # MOTOR COMPANY" and "Ford Motor Co", and until now only the third of
        # notices with an identity carried a single name for all three. The
        # rest fall back to the company part of what was filed — the same cut
        # the matcher retries on, and the same one employer_key groups by, so
        # the name shown agrees with the grouping rather than contradicting it.
        # Its casing is the state's, because inventing capitalization is worse
        # than keeping theirs.
        #
        # The basis says which it is. A name from Wikidata, the IRS or GLEIF
        # is an authority's; a cleaned one is this pipeline's reading of a
        # filing, and a consumer joining on company names needs to know the
        # difference.
        if not out["canonical_name"]:
            cleaned = base_employer(employer_name)
            if cleaned:
                out["canonical_name"], out["canonical_basis"] = cleaned, "cleaned"
            elif employer_name:
                out["canonical_name"] = employer_name.strip() or None
                out["canonical_basis"] = "filed" if out["canonical_name"] else None

        if out["naics"] is None and out["sic"] in self.naics_by_sic:
            out["naics"], out["naics_basis"] = self.naics_by_sic[out["sic"]], "sec-sic"
        if out["naics"] is None and out["ntee"]:
            naics = nonprofits.naics_from_ntee(out["ntee"])
            if naics:
                out["naics"], out["naics_basis"] = naics, "ntee"
        if out["naics"] is None and out["parent_cik"]:
            parent_sic = self.sic_by_cik.get(out["parent_cik"], ("", ""))[0]
            if parent_sic in self.naics_by_sic:
                out["naics"] = self.naics_by_sic[parent_sic]
                out["naics_basis"] = "parent-sic"

        # An adjudicated sector ranks below every basis that traces back to an
        # authority — a published code, a registrant's SIC, an IRS activity
        # code — because those say what an employer does on someone's record,
        # and this says what a model concluded from its name. It ranks above
        # inheritance from the employer's other notices, which is an
        # assumption rather than a statement about this employer at all.
        if out["naics"] is None:
            adjudicated = self.industry_overrides.get(norm or "") or (
                self.industry_overrides.get(base_norm) if base_norm else None
            )
            if adjudicated:
                out["naics"], out["naics_basis"] = adjudicated, "adjudicated"

        # Keep CIK-linked QID keys stable for existing public-company URLs.
        # A QID found only by label is weaker than a registered CIK/EIN/LEI
        # and must not outrank one for grouping or industry inheritance.
        keyed_qid = out["wikidata_qid"] if out["wikidata_match"] == "cik" else None
        for prefix, value in (
            ("qid", keyed_qid), ("cik", out["cik"]),
            ("ein", out["ein"]), ("lei", out["lei"]),
            ("qid", out["wikidata_qid"]),
        ):
            if value:
                out["employer_key"] = f"{prefix}:{value}"
                break
        else:
            # Unidentified employers group on the company part of the name,
            # so a firm's Flat Rock and Dearborn filings are one employer
            # rather than two. Their notices stay distinct: dedupe keys are
            # built from the filed name, not from this.
            out["employer_key"] = f"n:{base_norm or norm or (employer_name or '').lower()}"

        if out["naics"] is None:
            inherited = self.naics_by_employer.get(out["employer_key"])
            if inherited:
                out["naics"], out["naics_basis"] = inherited, "employer"

        out["naics_level"] = naics_level(out["naics_basis"])

        if out["identity_source"] is None and any(
            out[f] for f in ("cik", "ein", "lei", "wikidata_qid")
        ):
            out["identity_source"] = "automatic"
        return out
