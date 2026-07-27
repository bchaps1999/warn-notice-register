from warnlive.enrich.edgar import _edgar_name
from warnlive.enrich.industry import extract_industry
from warnlive.enrich.nonprofits import naics_from_ntee
from warnlive.normalize.engine import normalized_employer


def test_edgar_name_strips_incorporation_markers():
    """EDGAR appends the state of incorporation to registrant names; it is
    filing metadata and blocks the exact match a notice deserves."""
    assert _edgar_name("BANK OF AMERICA CORP /DE/") == "BANK OF AMERICA CORP"
    assert _edgar_name("WELLS FARGO & COMPANY/MN") == "WELLS FARGO & COMPANY"
    assert _edgar_name("WALT DISNEY CO/CA/TA/") == "WALT DISNEY CO"  # chained
    assert _edgar_name("BOEING CO") == "BOEING CO"  # nothing to strip


def test_normalized_employer_drops_leading_article():
    """States file "The Boeing Company"; the SEC registers "BOEING CO"."""
    assert normalized_employer("The Boeing Company") == "boeing"
    assert normalized_employer("Boeing Company") == "boeing"
    assert normalized_employer("The Kroger Co.") == "kroger"
    assert normalized_employer("The") == "the"  # nothing left to keep


def test_industry_prefers_source_naics():
    industry, naics, basis = extract_industry(
        {"Industry": "Food Service Contractors", "NAICS Code": "722310.0"}
    )
    assert (industry, naics, basis) == ("Food Service Contractors", "722310", "source")


def test_industry_rejects_sic_mislabelled_as_naics():
    """WI's 2001 log labels its column NAICS but fills it with SIC codes.
    34 is not a NAICS sector, so the code goes through the concordance."""
    _, naics, basis = extract_industry(
        {"Industry": "stainless steel tanks", "NAICS Code": "3443"}
    )
    assert basis == "sic-crosswalk"
    assert naics and naics.startswith("33")


def test_industry_falls_back_to_sector_name():
    _, naics, basis = extract_industry({"Industry": "Retail Trade"})
    assert (naics, basis) == ("44-45", "sector-name")


def test_industry_ignores_sic_lookalike_keys():
    """'sic' must match as a word: 'Music Venue' is not an industry code."""
    industry, naics, _ = extract_industry({"Music Classification": "1234"})
    assert naics is None and industry is None


def test_industry_drops_short_unusable_codes():
    """CO prints a 2-digit code that is neither a NAICS sector nor a SIC;
    padding it to '0079' would invent an unrelated industry."""
    assert extract_industry({"NAICS": "79"}) == (None, None, None)


def test_ntee_maps_to_naics():
    assert naics_from_ntee("E22") == "622"    # hospital
    assert naics_from_ntee("B43") == "6113"   # university
    assert naics_from_ntee("P270") == "62"    # human services, sector only
    assert naics_from_ntee("") is None
    assert naics_from_ntee("Z99") is None     # unclassified


def _matcher(tmp_path, rows):
    """A Matcher over a synthetic reference: (name, cik, y0, y1, ticker)."""
    import csv
    import gzip

    from warnlive.enrich.edgar import Matcher

    path = tmp_path / "names.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["normalized_name", "cik", "first_year", "last_year", "ticker"])
        w.writerows(rows)
    return Matcher(path)


def test_matcher_prefers_listed_registrant_when_name_is_shared(tmp_path):
    """A holding company and its post-IPO successor share a name; the
    listed one is the company a WARN notice means."""
    m = _matcher(tmp_path, [
        ("aramark", 7032, 1994, 2020, ""),
        ("aramark", 1584509, 2013, 2026, "ARMK"),
    ])
    assert m.match("ARAMARK", 2014) == (1584509, "ARMK", "exact:listed")


def test_matcher_relaxes_era_forward_only(tmp_path):
    """A registrant that went private keeps its identity afterwards, but a
    name not yet adopted may have belonged to someone EDGAR never saw."""
    m = _matcher(tmp_path, [("davids bridal", 1080187, 1999, 2007, "")])
    assert m.match("Davids Bridal", 2023) == (1080187, "", "exact:post-era")
    assert m.match("Davids Bridal", 1995) is None


def test_matcher_matches_name_plus_generic_token(tmp_path):
    """Two listed companies extend "Amazon"; the one that adds only a
    generic word is the same company under its legal name."""
    m = _matcher(tmp_path, [
        ("amazon com", 1018724, 1997, 2026, "AMZN"),
        ("amazon holdco", 2011286, 2024, 2024, "AMTM"),
    ])
    assert m.match("Amazon", 2023)[:2] == (1018724, "AMZN")


def test_matcher_prefers_listed_extension_over_dormant_namesake(tmp_path):
    """"Capital One" means COF, not the 2005 vehicle named for it."""
    m = _matcher(tmp_path, [
        ("capital one", 1313664, 2005, 2005, ""),
        ("capital one financial", 927628, 1994, 2026, "COF"),
    ])
    assert m.match("Capital One", 2020) == (927628, "COF", "listed-extension")


def test_matcher_rejects_common_word_names(tmp_path):
    """A filer registered as "American" is a shell; a notice from an
    employer called "American" identifies nothing."""
    rows = [("american", 1188212, 2008, 2012, "")]
    rows += [(f"american {i}", 900000 + i, 2000, 2026, "") for i in range(60)]
    m = _matcher(tmp_path, rows)
    assert m.match("American", 2010) is None


def test_annotator_inherits_industry_across_an_employers_notices(tmp_path):
    """States publish an industry on some of an employer's filings and not
    others; the industry belongs to the employer, not the filing."""
    import json
    import sqlite3

    from warnlive.enrich.annotate import Annotator
    from warnlive.store import db as db_mod

    conn = db_mod.connect(tmp_path / "t.sqlite")
    db_mod.init_db(conn)
    for i, raw in enumerate([{"NAICS Code": "722310"}, {}]):
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, employer_name, notice_date, "
            "layoff_type, current_version, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (f"k{i}", "WI", "Acme Catering", "2020-01-01", "closure", "2020", "2020"),
        )
        conn.execute(
            "INSERT INTO notice_versions (notice_id, version, raw_record_hash, "
            "fields_json, observed_at) VALUES (?,1,?,?,?)",
            (conn.execute("SELECT last_insert_rowid()").fetchone()[0], f"h{i}",
             json.dumps({"raw_extra": json.dumps(raw)}), "2020"),
        )
    conn.commit()

    a = Annotator()
    assert a.annotate("Acme Catering", "2020-01-01", json.dumps(
        {"raw_extra": json.dumps({})})) [ "naics"] is None
    a.prime(conn)
    got = a.annotate("Acme Catering", "2020-01-01", json.dumps({"raw_extra": "{}"}))
    assert (got["naics"], got["naics_basis"]) == ("722310", "employer")


def test_annotator_leaves_conflicting_industries_alone(tmp_path):
    """Disagreement means a misparse or a diversified filer; neither is
    resolved by picking one code arbitrarily."""
    import json

    from warnlive.enrich.annotate import Annotator
    from warnlive.store import db as db_mod

    conn = db_mod.connect(tmp_path / "t.sqlite")
    db_mod.init_db(conn)
    for i, code in enumerate(["722310", "541511"]):
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, employer_name, notice_date, "
            "layoff_type, current_version, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (f"k{i}", "WI", "Acme", "2020-01-01", "closure", "2020", "2020"),
        )
        conn.execute(
            "INSERT INTO notice_versions (notice_id, version, raw_record_hash, "
            "fields_json, observed_at) VALUES (?,1,?,?,?)",
            (conn.execute("SELECT last_insert_rowid()").fetchone()[0], f"h{i}",
             json.dumps({"raw_extra": json.dumps({"NAICS Code": code})}), "2020"),
        )
    conn.commit()

    a = Annotator()
    a.prime(conn)
    assert a.annotate("Acme", "2020-01-01", json.dumps({"raw_extra": "{}"}))["naics"] is None


def test_matcher_reports_candidates_only_when_it_declined(tmp_path):
    """A near-miss the gates refused is recorded for adjudication; a name
    that matched needs no second opinion."""
    m = _matcher(tmp_path, [
        ("j c penney", 77182, 1994, 2020, ""),
        ("j c penney", 1166126, 2002, 2026, ""),
        ("boeing", 12927, 1994, 2026, "BA"),
    ])
    assert m.match("J.C. Penney", 2017) is None
    found = m.candidates("J.C. Penney", 2017)
    assert {c["cik"] for c in found} == {77182, 1166126}
    assert {c["rejected_by"] for c in found} == {"ambiguous-exact"}
    assert m.candidates("Boeing", 2017) == []  # matched, nothing to review


def test_matcher_reports_pre_era_candidate(tmp_path):
    """The backward direction the era rule refuses is exactly the case a
    human should look at, not a case to guess."""
    m = _matcher(tmp_path, [("midway airlines", 946323, 1997, 2006, "")])
    assert m.match("Midway Airlines", 1991) is None
    found = m.candidates("Midway Airlines", 1991)
    assert [c["rejected_by"] for c in found] == ["pre-era"]


def test_adjudicated_identity_outranks_automatic_matching(tmp_path):
    """An override is a decision made from evidence the matcher cannot
    see, so it wins — and says so in identity_source."""
    import csv

    from warnlive.enrich import review
    from warnlive.enrich.annotate import Annotator

    path = tmp_path / "identity_overrides.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=review.OVERRIDE_FIELDS)
        w.writeheader()
        w.writerow({"normalized_name": "j c penney", "cik": "1166126",
                    "decided_by": "test", "decided_at": "2026-07-26",
                    "note": "the operating company named on the notice"})

    a = Annotator()
    a.overrides = review.load_overrides(path)
    got = a.annotate("J.C. Penney Corporation, Inc.", "2017-01-01", None)
    assert got["cik"] == 1166126
    assert got["cik_match"] == "override"
    assert got["identity_source"] == "override"
    assert got["employer_key"] == "cik:1166126"


def test_base_employer_separates_company_from_site():
    """WARN forms have one employer field, so states append the plant,
    store or trading name to the company's."""
    from warnlive.normalize.engine import base_employer

    assert base_employer("Ford Motor Co. - Flat Rock") == "Ford Motor Co."
    assert base_employer("KMART - STORE #3671") == "KMART"
    assert base_employer("Tyson Foods, Inc. (Amarillo B-Shift)") == "Tyson Foods, Inc."
    assert base_employer("Acme Holdings dba Speedy Mart") == "Acme Holdings"
    assert base_employer("*Updated* Community Healthlink") == "Community Healthlink"
    # Names that identify a company outright are left alone, hyphens and all
    assert base_employer("SANMINA-SCI CORPORATION") is None
    assert base_employer("Wal-Mart Stores, Inc.") is None
    assert base_employer("Boeing") is None
    # Nothing usable left to match on
    assert base_employer("A - B") is None


def test_base_employer_frees_a_stranded_legal_form():
    """cleanco strips a legal form only at the end of a string, so the
    qualifier costs the suffix too until it is cut."""
    from warnlive.normalize.engine import base_employer, normalized_employer

    assert normalized_employer("Ford Motor Co. - Flat Rock") == "ford motor co flat rock"
    assert normalized_employer(base_employer("Ford Motor Co. - Flat Rock")) == "ford motor"


def test_matcher_retries_without_the_site_qualifier(tmp_path):
    m = _matcher(tmp_path, [("kmart", 56824, 1994, 2005, "")])
    assert m.match("KMART", 2002) == (56824, "", "exact")
    assert m.match("KMART - STORE #3671", 2002) == (56824, "", "exact:base")
    assert m.match("Some Diner - Main St", 2002) is None
