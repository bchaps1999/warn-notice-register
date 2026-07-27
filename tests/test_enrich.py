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


def test_rejected_candidate_grants_nothing_and_stops_resurfacing(tmp_path):
    """A rejection records that candidates were examined and refused. It
    carries no identity, and it keeps the employer out of the next review
    file — but it does not veto a rule that later finds the right one."""
    import csv

    from warnlive.enrich import review
    from warnlive.enrich.annotate import Annotator

    path = tmp_path / "identity_overrides.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=review.OVERRIDE_FIELDS)
        w.writeheader()
        w.writerow({"normalized_name": "midway airlines", "decision": "reject",
                    "decided_by": "test", "decided_at": "2026-07-27",
                    "note": "the 1991 carrier is not the 1997 one"})

    loaded = review.load_overrides(path)
    assert "midway airlines" in loaded  # remembered, so review skips it

    a = Annotator()
    a.overrides = loaded
    got = a.annotate("Midway Airlines, Inc", "1991-01-01", None)
    assert got["cik"] is None
    assert got["identity_source"] is None


def _places_fixture(tmp_path):
    """A miniature Census roster: the cases the resolver has to get right."""
    import csv
    import gzip

    from warnlive.enrich.places import FIELDS, fold

    rows = [
        # Ohio files "Cincinnati (Hamilton)" and also has a city called
        # Hamilton, so the two have to be told apart.
        ("OH", "county", "Hamilton County", "", "39061", "Hamilton County", "1"),
        ("OH", "county", "Butler County", "", "39017", "Butler County", "1"),
        ("OH", "place", "Cincinnati city", "3915000", "39061", "Hamilton County", "1"),
        ("OH", "place", "Hamilton city", "3933012", "39017", "Butler County", "1"),
        # Two Springfields in one state, with nothing to choose between them.
        ("OH", "place", "Springfield city", "3974608", "39023", "Clark County", "1"),
        ("OH", "place", "Springfield city", "3974609", "39035", "Cuyahoga County", "1"),
        # An incorporated city and a CDP sharing a name.
        ("CA", "county", "Los Angeles County", "", "06037", "Los Angeles County", "1"),
        ("CA", "place", "Burbank city", "0608954", "06037", "Los Angeles County", "1"),
        ("CA", "place", "Burbank CDP", "0608955", "06085", "Santa Clara County", ""),
        ("IL", "place", "Chicago city", "1714000", "17031", "Cook County", "1"),
        # "urbana" is a status word only inside Puerto Rico's "zona urbana".
        ("IL", "place", "Urbana city", "1777005", "17019", "Champaign County", "1"),
        ("IL", "place", "Decatur city", "1718563", "17115", "Macon County", "1"),
        # A city sharing its name with a county it is not in.
        ("TX", "county", "Houston County", "", "48225", "Houston County", "1"),
        ("TX", "county", "Harris County", "", "48201", "Harris County", "1"),
        ("TX", "place", "Houston city", "4835000", "48201", "Harris County", "1"),
        ("TX", "place", "Flower Mound town", "4826232", "48121", "Denton County", "1"),
        # A township, which is a county subdivision rather than a place.
        ("NJ", "cousub", "Edison township", "", "34023", "Middlesex County", ""),
    ]
    path = tmp_path / "places.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for state, kind, name, place_fips, county_fips, county, inc in rows:
            writer.writerow({
                "state": state, "kind": kind, "key": fold(name), "name": name,
                "place_fips": place_fips, "county_fips": county_fips,
                "county_name": county, "lat": "40.0", "lon": "-83.0",
                "incorporated": inc,
            })
    return path


def test_places_resolve_the_shapes_states_actually_file(tmp_path):
    """Locations arrive as a bare city, a city and county, or a street
    address, and all three name the same kind of thing."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    got = r.resolve("OH", "Cincinnati (Hamilton)")
    assert got["place_name"] == "Cincinnati city"
    assert got["county_fips"] == "39061"
    assert got["geo_basis"] == "place+county"

    # The county in parentheses must not read as a second place, even though
    # Ohio has a city of the same name.
    assert r.resolve("OH", "Cincinnati (Hamilton)")["place_fips"] == "3915000"

    # A street address names its city after the street; a five-digit house
    # number must not be mistaken for a ZIP.
    address = r.resolve("IL", "1900 NORTH AUSTIN AVENUE CHICAGO, IL 60639-5079")
    assert address["place_name"] == "Chicago city"
    assert r.resolve("IL", "13800 Gentilly Road Chicago")["place_name"] == "Chicago city"

    # A trailing state abbreviation is not part of the name.
    assert r.resolve("IL", "Chicago IL")["place_name"] == "Chicago city"


def test_a_city_is_not_placed_in_the_county_that_shares_its_name(tmp_path):
    """Houston is in Harris County; Texas also has a Houston County. One
    segment matching both is a coincidence of names, not a filed county."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    houston = r.resolve("TX", "Houston")
    assert houston["county_name"] == "Harris County"
    assert houston["geo_basis"] == "place"

    # A county named in its own segment is a filed county and does count.
    assert r.resolve("OH", "Cincinnati (Hamilton)")["geo_basis"] == "place+county"


def test_a_place_name_is_not_eaten_by_the_address_stripper(tmp_path):
    """Suite and floor markers are stripped from addresses, but only as
    whole words — "fl" must not match the front of "Flower Mound"."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    assert r.resolve("TX", "Flower Mound")["place_name"] == "Flower Mound town"
    assert r.resolve("TX", "Houston, Suite 400")["place_name"] == "Houston city"


def test_a_street_address_gives_up_its_city(tmp_path):
    """A US address ends "<street> <City>, <ST> <ZIP>". Where the street
    carries a type word it can be cut off; where it does not, the city is
    still the thing at the end."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    # The street names itself, so it can simply be removed.
    assert (
        r.resolve("IL", "1900 North Austin Avenue Chicago, IL 60639-5079")["place_name"]
        == "Chicago city"
    )
    # Illinois writes streets with no type word at all; only position helps.
    decatur = r.resolve("IL", "2200 E. Eldorado Decatur, IL 62521")
    assert decatur["place_name"] == "Decatur city"
    assert decatur["geo_basis"] == "address"
    # A floor or suite sits between the street and the city.
    assert (
        r.resolve("IL", "200 East Randolph Street 70th Floor Chicago, IL 60601")[
            "place_name"
        ]
        == "Chicago city"
    )
    # Position is only mined from things that look like addresses, so a
    # phrase naming no place stays unresolved rather than donating a word.
    assert r.resolve("IL", "Various locations in Chicago area")["geo_basis"] != "address"


def test_urbana_is_a_city_not_a_status_word(tmp_path):
    """"Zona urbana" is Puerto Rican boilerplate; "Urbana" on its own is a
    city in Illinois, Ohio and Iowa."""
    from warnlive.enrich.places import Resolver, fold

    assert fold("Urbana city") == "urbana"
    assert fold("Zona Urbana Rio Grande") == "riogrande"

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")
    assert r.resolve("IL", "1710 PHILO ROAD URBANA, IL 61801")["place_name"] == (
        "Urbana city"
    )


def test_places_refuse_rather_than_guess(tmp_path):
    """A wrong place poisons every join built on it, so ambiguity resolves
    to nothing — and never across a state line."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    # Two Springfields, no county to choose between them.
    assert r.resolve("OH", "Springfield")["county_fips"] is None
    assert "more than one place" in r.refusals[("OH", "Springfield")]

    # Chicago is in Illinois; a New Jersey notice naming it resolves to
    # nothing rather than reaching into another state.
    assert r.resolve("NJ", "Chicago")["geo_basis"] is None

    # A workforce investment area is not a place and must not become one.
    assert r.resolve("OH", "4 - Workforce Investment Area IV")["geo_basis"] is None


def test_places_prefer_the_municipality_and_fall_back_to_the_township(tmp_path):
    """Where a city and a CDP share a name an employer files from the city;
    where a state files from townships there is no place at all."""
    from warnlive.enrich.places import Resolver

    r = Resolver(path=_places_fixture(tmp_path), alias_path=tmp_path / "none.csv")

    burbank = r.resolve("CA", "Burbank")
    assert burbank["place_name"] == "Burbank city"
    assert burbank["county_fips"] == "06037"

    edison = r.resolve("NJ", "Edison")
    assert edison["place_name"] is None
    assert edison["county_fips"] == "34023"
    assert edison["geo_basis"] == "subdivision"


def test_place_aliases_can_name_a_county_when_there_is_no_city(tmp_path):
    """Brooklyn is a borough, not a Census place; aliasing it to a city
    would be wrong, so the alias names its county instead."""
    import csv

    from warnlive.enrich.places import Resolver

    alias_path = tmp_path / "aliases.csv"
    with open(alias_path, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["state", "filed_name", "census_name", "kind", "note"]
        )
        writer.writeheader()
        writer.writerow({
            "state": "CA", "filed_name": "Chatsworth",
            "census_name": "Burbank", "kind": "", "note": "a neighbourhood",
        })
        writer.writerow({
            "state": "CA", "filed_name": "Universal City",
            "census_name": "Los Angeles County", "kind": "county",
            "note": "unincorporated",
        })
    r = Resolver(path=_places_fixture(tmp_path), alias_path=alias_path)

    assert r.resolve("CA", "Chatsworth")["place_name"] == "Burbank city"
    unincorporated = r.resolve("CA", "Universal City")
    assert unincorporated["place_name"] is None
    assert unincorporated["county_fips"] == "06037"


def test_ohio_column_labels_come_from_the_values(tmp_path):
    """Ohio's yearly PDFs centre their headers over left-aligned data, so
    columns are identified by what they contain, not by a header row."""
    from warnlive.backfill.state_archives import _oh_label_columns

    rows = [
        ["12/28/01", "Southern Ohio Coal", "Langsville", "80", "2/28/02", "53-01-070"],
        ["12/11/01", "TRW Automotive", "Cleveland", "500", "12/10/01", "3-01-068"],
        ["12/10/01", "Getronics", "Cleveland", "70", "12/12/01", "3-01-067"],
    ]
    labels = _oh_label_columns(rows)
    assert labels["company"] == 1
    assert labels["city"] == 2
    assert labels["jobs"] == 3
    assert labels["layoff"] == 4
    assert labels["warn_id"] == 5


def test_ohio_count_survives_the_neighbouring_column_bleeding_in():
    """Where the layoff-date text wraps, its first word lands in the count
    column. The count still leads its own column — but a second number means
    the columns are confused, and guessing would invent a figure."""
    from warnlive.backfill.state_archives import _oh_count

    assert _oh_count("124") == 124
    assert _oh_count("1,240") == 1240
    assert _oh_count("124 Begin") == 124  # "Begin 2/14/11 until 11/18/11"
    assert _oh_count("303 3/6/10 Begins") == 303
    # "213" broken up by irregular spacing, not a count of 2
    assert _oh_count("2 1 3 2/28/11") is None
    # the count landed in the city column instead
    assert _oh_count("- 63") is None
    assert _oh_count("") is None


def test_ohio_date_is_found_rather_than_read_off_the_front():
    """Both neighbouring columns bleed words into a date cell, and a wrapped
    fragment can sort ahead of the date it belongs to."""
    from warnlive.backfill.state_archives import _oh_date

    assert _oh_date("12/18/2009") == "2009-12-18"
    assert _oh_date("Begins 2/12/10 until 3/31/10") == "2010-02-12"
    assert _oh_date("Youngstown 12/18/2009") == "2009-12-18"
    assert _oh_date("unknown") is None


def test_ohio_quality_bar_rejects_mangled_years():
    """A year is ingested only if it parses well; a mangled employer name is
    worse than an absent one."""
    from warnlive.backfill.state_archives import _oh_quality

    clean = [{"employer_name": "Southern Ohio Coal Company"}] * 90
    ok, _ = _oh_quality(clean, dated_rows=95, expected=100)
    assert ok

    # rows lost to bad line grouping (2012 collapses 101 rows into one)
    ok, _ = _oh_quality(clean, dated_rows=20, expected=100)
    assert not ok

    # a column split mid-name leaves implausibly short employers
    split = [{"employer_name": "Penske"}] * 90
    ok, _ = _oh_quality(split, dated_rows=95, expected=100)
    assert not ok

    # two fields merged into one leaves implausibly long employers
    merged = [{"employer_name": "ABX Air, Inc. (Clinton) Grove City Franklin"}] * 90
    ok, _ = _oh_quality(merged, dated_rows=95, expected=100)
    assert not ok
