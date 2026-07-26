from warnlive.enrich.edgar import _edgar_name
from warnlive.enrich.industry import extract_industry
from warnlive.enrich.nonprofits import naics_from_ntee


def test_edgar_name_strips_incorporation_markers():
    """EDGAR appends the state of incorporation to registrant names; it is
    filing metadata and blocks the exact match a notice deserves."""
    assert _edgar_name("BANK OF AMERICA CORP /DE/") == "BANK OF AMERICA CORP"
    assert _edgar_name("WELLS FARGO & COMPANY/MN") == "WELLS FARGO & COMPANY"
    assert _edgar_name("WALT DISNEY CO/CA/TA/") == "WALT DISNEY CO"  # chained
    assert _edgar_name("BOEING CO") == "BOEING CO"  # nothing to strip


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
