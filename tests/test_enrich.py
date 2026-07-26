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
