"""What a model says is a proposal; these are the tests that it stays one.

Nothing here calls an API. Every test hands a recorded answer — the shape a
model does return, including the shapes a wrong one returns — to the gate that
judges it, and checks what the gate concluded. That is the whole safety
argument for this layer: an answer is worth what the evidence behind it is
worth, and the evidence is checked by code that already existed.
"""

import csv
import gzip
import json
import os

import pytest

from warnlive.adjudicate import identity as adj_identity
from warnlive.adjudicate import industry as adj_industry
from warnlive.adjudicate import places as adj_places
from warnlive.adjudicate import queue as queue_mod
from warnlive.adjudicate.client import Model, resolve, shape_problem
from warnlive.adjudicate.ledger import Entry, Ledger
from warnlive.enrich.places import FIELDS as PLACE_FIELDS
from warnlive.enrich.places import Resolver, fold

MODEL = "deepseek/deepseek-v4-flash"


# -- fixtures ---------------------------------------------------------------

def _places(tmp_path):
    """A miniature gazetteer: Chicago exists, Chicagoville does not."""
    rows = [
        ("IL", "place", "Chicago city", "1714000", "17031", "Cook County", "1"),
        ("IL", "county", "Cook County", "", "17031", "Cook County", "1"),
        ("KS", "place", "Wichita city", "2079000", "20173", "Sedgwick County", "1"),
    ]
    path = tmp_path / "places.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLACE_FIELDS)
        writer.writeheader()
        for state, kind, name, place_fips, county_fips, county, inc in rows:
            writer.writerow({
                "state": state, "kind": kind, "key": fold(name), "name": name,
                "place_fips": place_fips, "county_fips": county_fips,
                "county_name": county, "lat": "41.8", "lon": "-87.6",
                "incorporated": inc,
            })
    return path


def _places_worker(tmp_path, threshold=0.8):
    resolver = Resolver(path=_places(tmp_path), alias_path=tmp_path / "none.csv")
    return adj_places.Places(threshold=threshold, resolver=resolver)


def _place_item(state, location, workers=1000, reason="no matching place"):
    return {"state": state, "location": location, "notices": 3,
            "workers": workers, "reason": reason}


def _matcher(tmp_path, rows):
    from warnlive.enrich.edgar import Matcher

    path = tmp_path / "names.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["normalized_name", "cik", "first_year", "last_year", "ticker"])
        w.writerows(rows)
    return Matcher(path)


def _identity_item(name, **kw):
    item = {
        "normalized_name": name.lower(), "employer_name": name,
        "states": ["CA"], "years": ["2015"], "notices": 4, "workers": 900,
        "source_naics": [], "candidates": [],
    }
    item.update(kw)
    return item


# -- places: the gate is the resolver ---------------------------------------

def test_a_place_the_gazetteer_does_not_have_is_not_written(tmp_path):
    """The hallucination gate. An invented city fails because the resolver
    still refuses it, not because anything here inspected the name."""
    worker = _places_worker(tmp_path)
    item = _place_item("IL", "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666")

    invented = worker.decide(item, {
        "kind": "place", "census_name": "Chicagoville", "confidence": 0.99,
        "note": "invented",
    })
    assert invented.outcome == queue_mod.STAGED
    assert "does not resolve" in invented.note
    assert invented.row.get("alias") is None


def test_a_real_place_resolves_the_string_and_is_written(tmp_path):
    """An airport names no place a rule will ever find, and names one a
    person recognises at once. The alias settles the whole filed string."""
    worker = _places_worker(tmp_path)
    item = _place_item("IL", "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666")

    got = worker.decide(item, {
        "kind": "place", "census_name": "Chicago", "confidence": 0.97,
        "note": "O'Hare is within Chicago city limits",
    })
    assert got.outcome == queue_mod.ACCEPTED
    assert got.row["alias"]["census_name"] == "Chicago"
    assert got.row["alias"]["filed_name"] == item["location"]


def test_a_county_answer_is_never_written_on_the_models_word(tmp_path):
    """Re-running the resolver proves a city exists and proves nothing about
    a county: every real county resolves. New Jersey filed "Salisbury" and
    the model returned Middlesex County at 0.99 as an unincorporated
    community. There is no Salisbury in New Jersey — but Middlesex County is
    real, so the gate passed it. These go to a person."""
    worker = _places_worker(tmp_path)
    got = worker.decide(_place_item("IL", "Salisbury"), {
        "kind": "county", "census_name": "Cook County", "confidence": 0.99,
        "note": "unincorporated community",
    })
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "county-level"
    assert got.row.get("alias") is None
    # It still records what it would have resolved to, so review is cheap.
    assert got.row["resolved_to"] == "Cook County"

    # A city answer is evidenced, and still written.
    city = worker.decide(_place_item("IL", "Chicagoo"), {
        "kind": "place", "census_name": "Chicago", "confidence": 0.95, "note": "typo",
    })
    assert city.outcome == queue_mod.ACCEPTED


def test_a_name_that_is_a_place_in_other_states_is_flagged_for_review(tmp_path):
    """Not a gate — most unincorporated communities share a name with some
    incorporated place elsewhere, so refusing on it would throw away far more
    than it caught. It ranks the review file instead."""
    worker = _places_worker(tmp_path)
    # Wichita is a place in KS in the fixture, and not in IL.
    assert worker.attested_elsewhere("IL", "Wichita") == ["KS"]
    # Chicago is a place in IL, so filing it in IL flags nothing.
    assert worker.attested_elsewhere("IL", "Chicago") == []


def test_a_place_in_another_state_is_refused(tmp_path):
    """A New Jersey notice filed against "Chicago" is a data error, and the
    resolver never reaches across a state line to rescue it."""
    worker = _places_worker(tmp_path)
    got = worker.decide(_place_item("KS", "Chicago"), {
        "kind": "place", "census_name": "Chicago", "confidence": 0.95, "note": "",
    })
    assert got.outcome == queue_mod.STAGED


def test_a_workforce_area_is_recorded_as_naming_no_place(tmp_path):
    """Kansas files against workforce investment areas. They are not places
    and never will be, so the answer is a rejection — which grants no
    geography and stops the string returning to the review file forever."""
    worker = _places_worker(tmp_path)
    got = worker.decide(_place_item("KS", "4 - Workforce Investment Area IV", 76853), {
        "kind": "unresolvable", "census_name": "", "confidence": 0.99,
        "note": "a Kansas workforce investment area",
    })
    assert got.outcome == queue_mod.REJECTED
    assert got.row["alias"]["decision"] == "reject"
    assert got.row["alias"]["census_name"] == ""


def test_a_rejection_removes_a_string_from_the_review_queue(tmp_path):
    """The review file is rebuilt from the database every refresh, so a
    rejection is only worth writing if the rebuild honours it."""
    from warnlive.enrich.places import load_rejections

    path = tmp_path / "aliases.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=adj_places.ALIAS_FIELDS)
        w.writeheader()
        w.writerow({"state": "KS", "filed_name": "4 - Workforce Investment Area IV",
                    "decision": "reject", "census_name": "", "kind": "", "note": ""})

    assert ("KS", fold("4 - Workforce Investment Area IV")) in load_rejections(path)
    # A rejection carries no geography, so it must not become an alias.
    from warnlive.enrich.places import load_aliases
    assert load_aliases(path) == {}


def test_two_sites_in_one_string_are_never_resolved_to_one(tmp_path):
    """A notice covering two addresses is a field being asked to hold two
    answers. Picking one would be a silent loss, so it is staged."""
    worker = _places_worker(tmp_path)
    got = worker.decide(_place_item("IL", "1 Main St Chicago / 9 Oak St Wichita"), {
        "kind": "multiple", "census_name": "", "confidence": 0.9, "note": "two sites",
    })
    assert got.outcome == queue_mod.STAGED
    assert got.row.get("alias") is None


def test_a_confident_answer_below_the_threshold_is_still_staged(tmp_path):
    """Resolving is necessary and not sufficient: an uncertain answer that
    happens to resolve is a person's decision, not an automatic one."""
    worker = _places_worker(tmp_path, threshold=0.8)
    got = worker.decide(_place_item("IL", "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666"), {
        "kind": "place", "census_name": "Chicago", "confidence": 0.42, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert "confidence" in got.note


def test_trying_a_proposal_leaves_the_alias_table_as_it_was(tmp_path):
    """The gate works by writing an alias and asking the resolver again. It
    must put back what it borrowed, or one trial would contaminate the next."""
    worker = _places_worker(tmp_path)
    before = dict(worker.resolver.aliases)
    worker.decide(_place_item("IL", "somewhere"), {
        "kind": "place", "census_name": "Chicago", "confidence": 0.99, "note": "",
    })
    assert worker.resolver.aliases == before


def test_a_decided_string_is_not_decided_again(tmp_path):
    """A later run disagreeing with an earlier one is something to look at,
    not something to apply, so the first decision stands."""
    alias_path = tmp_path / "aliases.csv"
    staging = tmp_path / "staged.csv"
    row = {
        "state": "IL", "location": "AIRPORT", "notices": 1, "workers": 5,
        "reason": "", "kind": "place", "census_name": "Chicago",
        "confidence": 0.99, "note": "",
        "alias": {"state": "IL", "filed_name": "AIRPORT", "decision": "",
                  "census_name": "Chicago", "kind": "place", "note": ""},
    }
    written, _ = adj_places.write([row], alias_path, staging, decided_by="test-model")
    assert written == 1
    again, _ = adj_places.write([dict(row)], alias_path, staging, decided_by="test-model")
    assert again == 0
    with open(alias_path, newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 1


# -- identity: the gate is the matcher, then the corroborators --------------

def test_a_proposed_name_must_clear_the_unmodified_matcher(tmp_path):
    """The matcher is not relaxed because a model suggested the name. A
    guess has to survive the rules that refused the filed name."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    got = worker.decide(_identity_item("Bob's Diner"), {
        "stance": "public", "proposed": ["Bobs Diner Holdings International"],
        "parent_name": "", "parent_cik": 0, "confidence": 0.95, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert "cleared the matcher" in got.note


def test_a_match_with_one_witness_is_not_enough(tmp_path):
    """Clearing the matcher says a name matched. Corroboration says the
    company is the same company, which is the claim actually being made."""
    worker = adj_identity.Identity(min_corroborators=2)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.sic_by_cik = {}
    worker.annotator.naics_by_sic = {}
    worker.annotator.wikidata_by_cik = {}
    got = worker.decide(_identity_item("The Boeing Company"), {
        "stance": "public", "proposed": ["Boeing"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "under-corroborated"
    assert got.row["matched_cik"] == 12927


def test_a_match_two_authorities_agree_on_is_written(tmp_path):
    """Wikidata names the CIK itself and the filing calendar covers the
    notices: an anchored witness plus an independent one is worth keeping."""
    worker = adj_identity.Identity(min_corroborators=2)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.sic_by_cik = {}
    worker.annotator.naics_by_sic = {}
    worker.annotator.wikidata_by_cik = {
        12927: {"qid": "Q66", "label": "Boeing", "parents": ""}
    }
    got = worker.decide(
        _identity_item("The Boeing Company"),
        {"stance": "public", "proposed": ["Boeing"], "parent_name": "",
         "parent_cik": 0, "confidence": 0.99, "note": "aerospace"},
    )
    assert got.outcome == queue_mod.ACCEPTED
    assert got.row["override"]["cik"] == 12927
    assert "covering the notices" in got.note and "Wikidata" in got.note


def test_two_weak_witnesses_are_the_signature_of_a_namesake(tmp_path):
    """An old registrant in roughly the right industry is what a wrong
    same-industry namesake looks like. Without one witness that names the
    CIK itself, the match is staged for confirmation, not written."""
    worker = adj_identity.Identity(min_corroborators=2)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.sic_by_cik = {12927: ("3721", "Aircraft")}
    worker.annotator.naics_by_sic = {"3721": "336411"}
    worker.annotator.wikidata_by_cik = {}
    got = worker.decide(
        _identity_item("The Boeing Company", source_naics=["33"]),
        {"stance": "public", "proposed": ["Boeing"], "parent_name": "",
         "parent_cik": 0, "confidence": 0.99, "note": "aerospace"},
    )
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "under-corroborated"
    assert got.note == "no CIK-anchored corroborator"
    assert "covering the notices" in got.row["corroborated_by"]
    assert "industry agrees" in got.row["corroborated_by"]


def test_an_employer_its_own_registrant_lists_as_a_subsidiary_is_not_that_registrant(tmp_path):
    """A company does not appear in its own subsidiary schedule. Exhibit 21
    saying "X owns this" contradicts "this is X", so the identity is refused
    and a person decides whether it wants a parent link instead."""
    worker = adj_identity.Identity(min_corroborators=1)
    worker.matcher = _matcher(tmp_path, [("textron", 217346, 1994, 2026, "TXT")])
    worker.annotator.wikidata_by_cik = {}
    worker.subsidiaries.by_name["cessna aircraft"] = {
        "normalized_name": "cessna aircraft", "parent_cik": "217346",
        "parent_name": "TEXTRON INC", "source_year": "2015",
    }
    got = worker.decide(_identity_item("Cessna Aircraft"), {
        "stance": "public", "proposed": ["Textron"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "listed as its own subsidiary"


def test_a_shorter_name_matching_a_subsidiary_does_not_refuse_the_parent(tmp_path):
    """The subsidiary index tolerates a notice's shorter name so "Cessna"
    finds Textron. That tolerance must not make Boeing's own schedule argue
    that The Boeing Company is somebody's subsidiary."""
    worker = adj_identity.Identity(min_corroborators=1)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.wikidata_by_cik = {
        12927: {"qid": "Q66", "label": "Boeing", "parents": ""}
    }
    worker.subsidiaries.by_name["boeing aerospace operations"] = {
        "normalized_name": "boeing aerospace operations", "parent_cik": "12927",
        "parent_name": "BOEING CO", "source_year": "2026",
    }
    worker.subsidiaries.by_first["boeing"].append("boeing aerospace operations")
    got = worker.decide(_identity_item("The Boeing Company"), {
        "stance": "public", "proposed": ["Boeing"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert got.outcome == queue_mod.ACCEPTED
    assert got.row["override"]["cik"] == 12927


def test_a_subsidiary_becomes_a_parent_link_and_not_an_identity(tmp_path):
    """First Transit is owned by FirstGroup and is not FirstGroup. Writing
    the parent's CIK as its identity would conflate them in every join."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [("firstgroup", 4321, 1996, 2026, "")])
    worker.annotator.sic_by_cik = {4321: ("4111", "Transit")}
    got = worker.decide(_identity_item("First Transit, Inc."), {
        "stance": "subsidiary", "proposed": [], "parent_name": "FirstGroup plc",
        "parent_cik": 4321, "confidence": 0.95, "note": "FirstGroup's US bus arm",
    })
    assert got.outcome == queue_mod.ACCEPTED
    assert "override" not in got.row
    assert got.row["subsidiary"]["parent_cik"] == 4321


def test_a_parent_cik_no_reference_file_knows_is_refused(tmp_path):
    """A CIK nobody has a record of is a number, not a company."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [("firstgroup", 4321, 1996, 2026, "")])
    worker.annotator.sic_by_cik = {}
    got = worker.decide(_identity_item("First Transit, Inc."), {
        "stance": "subsidiary", "proposed": [], "parent_name": "FirstGroup plc",
        "parent_cik": 99999999, "confidence": 0.95, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert "no reference file" in got.note


def test_no_registrant_to_find_is_recorded_but_never_written(tmp_path):
    """"The model says nothing is there" stops the re-asking (the ledger
    remembers it) and is staged for a person — it is the one claim in this
    file nothing can verify, so it must not become a permanent override."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [])
    got = worker.decide(_identity_item("Fresno Unified School District"), {
        "stance": "government", "proposed": [], "parent_name": "",
        "parent_cik": 0, "confidence": 0.97, "note": "a public school district",
    })
    assert got.outcome == queue_mod.REJECTED
    assert "override" not in got.row
    assert got.row["gate"] == "model-rejected"


def test_proposals_are_tried_whatever_the_stance_said(tmp_path):
    """The stance is the one model output no gate can check. A wrong
    "private" must not keep the matcher from a name it would have matched."""
    worker = adj_identity.Identity(min_corroborators=1)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.sic_by_cik = {}
    worker.annotator.naics_by_sic = {}
    worker.annotator.wikidata_by_cik = {
        12927: {"qid": "Q66", "label": "Boeing", "parents": ""}
    }
    got = worker.decide(
        _identity_item("The Boeing Company"),
        {"stance": "private", "proposed": ["Boeing"], "parent_name": "",
         "parent_cik": 0, "confidence": 0.99, "note": ""},
    )
    assert got.outcome == queue_mod.ACCEPTED
    assert got.row["override"]["cik"] == 12927


def test_a_refused_proposal_still_lets_a_parent_claim_be_tried(tmp_path):
    """One row can carry both a guess at a registration and a parent. The
    matcher refusing the first is no verdict on the second."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [("firstgroup", 4321, 1996, 2026, "")])
    worker.annotator.sic_by_cik = {4321: ("4111", "Transit")}
    got = worker.decide(_identity_item("First Transit, Inc."), {
        "stance": "subsidiary", "proposed": ["First Transit Holdings"],
        "parent_name": "FirstGroup plc", "parent_cik": 4321,
        "confidence": 0.95, "note": "FirstGroup's US bus arm",
    })
    assert got.outcome == queue_mod.ACCEPTED
    assert got.row["subsidiary"]["parent_cik"] == 4321


def test_an_employer_decided_by_hand_is_never_overwritten(tmp_path):
    """Overrides record who decided and why. A model must not quietly
    replace a person's adjudication."""
    worker = adj_identity.Identity()
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.overrides = {"the boeing company": {"cik": "999"}}
    item = _identity_item("The Boeing Company")
    item["normalized_name"] = "the boeing company"
    got = worker.decide(item, {
        "stance": "public", "proposed": ["Boeing"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "existing override"


def test_a_registrant_that_stopped_filing_before_the_notices_is_no_witness(tmp_path):
    """The era corroborator asks the calendar a real question. Read from the
    matcher's reason for matching instead, it would pass for nearly every
    method and quietly turn "two corroborators" into one."""
    worker = adj_identity.Identity(min_corroborators=1)
    worker.matcher = _matcher(tmp_path, [("davids bridal", 1080187, 1999, 2007, "")])
    worker.annotator.sic_by_cik = {}
    worker.annotator.naics_by_sic = {}
    worker.annotator.wikidata_by_cik = {}

    # Notices from 2023 against a registrant that stopped filing in 2007.
    got = worker.decide(
        _identity_item("David's Bridal", years=["2023"]),
        {"stance": "public", "proposed": ["Davids Bridal"], "parent_name": "",
         "parent_cik": 0, "confidence": 0.99, "note": ""},
    )
    assert got.outcome == queue_mod.STAGED
    assert got.row["gate"] == "under-corroborated"
    assert got.row["corroborated_by"] == ""

    # The same registrant, notices inside its filing span: the calendar
    # speaks — as a weak witness, recorded but not sufficient on its own.
    covered = worker.decide(
        _identity_item("David's Bridal", years=["2003"]),
        {"stance": "public", "proposed": ["Davids Bridal"], "parent_name": "",
         "parent_cik": 0, "confidence": 0.99, "note": ""},
    )
    assert covered.outcome == queue_mod.STAGED
    assert "covering the notices" in covered.row["corroborated_by"]


def test_a_third_party_naming_the_registrant_corroborates_a_public_identity(tmp_path):
    """Exhibit 21 cannot speak for a plain public company — a company is not
    in its own subsidiary schedule — so Wikidata agreeing on the name is the
    witness available to one, and it comes from a different source entirely."""
    worker = adj_identity.Identity(min_corroborators=2)
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    worker.annotator.sic_by_cik = {}
    worker.annotator.naics_by_sic = {}
    worker.annotator.wikidata_by_cik = {
        12927: {"qid": "Q66", "label": "Boeing", "parents": ""}
    }
    got = worker.decide(_identity_item("The Boeing Company"), {
        "stance": "public", "proposed": ["Boeing"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert got.outcome == queue_mod.ACCEPTED
    assert "Wikidata" in got.note

    # A Wikidata label for some other company is not agreement.
    worker.annotator.wikidata_by_cik = {
        12927: {"qid": "Q66", "label": "Spirit AeroSystems", "parents": ""}
    }
    disagrees = worker.decide(_identity_item("The Boeing Company"), {
        "stance": "public", "proposed": ["Boeing"], "parent_name": "",
        "parent_cik": 0, "confidence": 0.99, "note": "",
    })
    assert disagrees.outcome == queue_mod.STAGED


def _confirm_item(name, corroborated_by="", **kw):
    item = _identity_item(name, **{k: v for k, v in kw.items() if k != "cik"})
    item.update({
        "matched_cik": kw.get("cik", 12927),
        "matched_name": name.upper(),
        "cik_match": "exact",
        "corroborated_by": corroborated_by,
    })
    return item


def test_confirmation_supplements_a_corroborator_it_never_replaces_one(tmp_path):
    """A yes from the confirm queue is written only where an independent
    witness already spoke. With none, the whole claim would rest on the
    model's word — the exact thing the identity gate refuses."""
    from warnlive.adjudicate import confirm as adj_confirm

    worker = adj_confirm.Confirm()
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    answer = {"same": True, "confidence": 0.95, "note": "same company"}

    bare = worker.decide(_confirm_item("Boeing"), answer)
    assert bare.outcome == queue_mod.STAGED
    assert bare.row["gate"] == "no corroborator"

    witnessed = worker.decide(
        _confirm_item("Boeing",
                      corroborated_by="registrant filed 1994-2026, covering the notices"),
        answer,
    )
    assert witnessed.outcome == queue_mod.ACCEPTED
    assert "covering the notices" in witnessed.row["override"]["note"]
    assert "confirmed by model" in witnessed.row["override"]["note"]


def test_a_refused_confirmation_rejects_the_match_not_the_employer(tmp_path):
    from warnlive.adjudicate import confirm as adj_confirm

    worker = adj_confirm.Confirm()
    worker.matcher = _matcher(tmp_path, [("boeing", 12927, 1994, 2026, "BA")])
    got = worker.decide(
        _confirm_item("Boeing"),
        {"same": False, "confidence": 0.9, "note": "a namesake"},
    )
    assert got.outcome == queue_mod.REJECTED
    assert "override" not in got.row


def test_sectors_compare_at_the_level_a_warn_form_is_worth():
    assert adj_identity._same_sector("3345", "31-33")
    assert adj_identity._same_sector("44-45", "445110")
    assert not adj_identity._same_sector("62", "23")


# -- industry: no authority to check against, so a measured threshold -------

def test_a_sector_is_read_in_whichever_spelling_it_arrives():
    assert adj_industry.sector_of("325412") == "31-33"
    assert adj_industry.sector_of("45") == "44-45"
    assert adj_industry.sector_of("62") == "62"
    assert adj_industry.sector_of("nonsense") == ""
    assert adj_industry.sector_of("") == ""


def test_an_unrecognised_employer_abstains_rather_than_guessing():
    worker = adj_industry.Industry(threshold=0.9)
    item = {"normalized_name": "x", "employer_name": "X", "states": ["OH"],
            "notices": 1, "workers": 10}
    got = worker.decide(item, {"naics": "", "confidence": 0.1, "note": "no idea"})
    assert got.outcome == queue_mod.ABSTAINED
    assert got.row is None


def test_the_industry_threshold_admits_only_what_calibration_justified():
    worker = adj_industry.Industry(threshold=0.9)
    item = {"normalized_name": "crothall healthcare", "employer_name": "Crothall",
            "states": ["PA"], "notices": 2, "workers": 500}
    low = worker.decide(item, {"naics": "62", "confidence": 0.7, "note": ""})
    high = worker.decide(item, {"naics": "62", "confidence": 0.95, "note": ""})
    assert low.outcome == queue_mod.STAGED
    assert high.outcome == queue_mod.ACCEPTED
    assert high.row["override"]["naics"] == "62"


def test_calibration_scores_employers_not_notices(tmp_path):
    """First Transit files forty-nine times. Scoring per notice would
    measure how often big employers file, not how often the model is right."""
    items = [
        {"normalized_name": "a", "employer_name": "A", "workers": 4900, "truth": "62"},
        {"normalized_name": "b", "employer_name": "B", "workers": 100, "truth": "23"},
    ]
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    worker = adj_industry.Industry()
    for key, naics, conf in (("a", "62", 0.95), ("b", "62", 0.95)):
        ledger.record(Entry(
            task="industry", input_key=key, prompt_version=worker.prompt_version,
            model="m", answer={"naics": naics, "confidence": conf}, outcome="",
        ))
    monkey = adj_industry.CALIBRATION_PATH
    adj_industry.CALIBRATION_PATH = tmp_path / "calibration.csv"
    try:
        curve = adj_industry.score(items, worker, ledger, "m")
    finally:
        adj_industry.CALIBRATION_PATH = monkey

    top = [row for row in curve if row["threshold"] == 0.9][0]
    assert top["answered"] == 2
    assert top["precision"] == 0.5              # one employer of two
    assert top["worker_precision"] == 0.98      # the big one was the right one


# -- the runner: replay, and what a bad reply costs -------------------------

def test_a_stored_answer_is_rejudged_rather_than_rebought(tmp_path):
    """Gates get stricter as corroborators are added. When they do, every
    past answer should be re-examined — without paying for it twice."""
    worker = _places_worker(tmp_path)
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    item = _place_item("IL", "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666")
    ledger.record(Entry(
        task=worker.task, input_key=worker.key(item),
        prompt_version=worker.prompt_version, model=MODEL,
        answer={"kind": "place", "census_name": "Chicago", "confidence": 0.97},
        outcome=queue_mod.ACCEPTED,
    ))

    tally = queue_mod.run(worker, [item], client=None, ledger=ledger,
                          dry_run=True, model=MODEL)
    assert tally.replayed == 1 and tally.asked == 0
    assert tally.by_outcome[queue_mod.ACCEPTED] == 1

    # Same answer, stricter gate: now it does not clear, and no call is made.
    strict = _places_worker(tmp_path, threshold=0.99)
    strict.resolver = worker.resolver
    tally = queue_mod.run(strict, [item], client=None, ledger=ledger,
                          dry_run=True, model=MODEL)
    assert tally.replayed == 1 and tally.asked == 0
    assert tally.by_outcome[queue_mod.STAGED] == 1


class _FakeClient:
    """A client that answers from a script and can be made to die mid-run."""

    def __init__(self, replies, die_after=None):
        self.model = MODEL
        self.replies = list(replies)
        self.die_after = die_after
        self.calls = 0
        self.rooms = []

    def complete_json(self, system, user, required=None, max_tokens=0,
                      thinking=True):
        if self.die_after is not None and self.calls >= self.die_after:
            raise KeyboardInterrupt("interrupted mid-run")
        self.rooms.append(max_tokens)
        reply = self.replies[self.calls]
        self.calls += 1
        return reply


def test_answers_are_banked_per_batch_not_at_the_end_of_the_run(tmp_path):
    """A queue is thousands of rows and an hour of calls. Holding answers
    until the run finishes means an interruption throws away everything it
    was paid for, and the next run buys the same answers again."""
    worker = _places_worker(tmp_path)
    worker.batch_size = 2
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    items = [_place_item("KS", f"{i} - Workforce Investment Area") for i in range(6)]
    answer = {"kind": "unresolvable", "census_name": "", "confidence": 0.99,
              "note": "workforce area"}
    replies = [
        {"results": [{"id": 1, **answer}, {"id": 2, **answer}]} for _ in range(3)
    ]
    client = _FakeClient(replies, die_after=2)

    with pytest.raises(KeyboardInterrupt):
        queue_mod.run(worker, items, client=client, ledger=ledger, model=MODEL)

    # Two batches got through before the interruption; both are on disk.
    banked = Ledger(tmp_path / "led.jsonl.gz")
    assert len(banked.entries) == 4

    # And a rerun replays them rather than paying for them a second time.
    resumed = _FakeClient([replies[2]])
    tally = queue_mod.run(worker, items, client=resumed, ledger=banked, model=MODEL)
    assert resumed.calls == 1
    assert tally.replayed == 4


def test_a_batch_is_given_room_for_the_models_thinking_too(tmp_path):
    """Reasoning is billed as output and spent from the same allowance as
    the answer. Budgeting for the answer alone truncates the JSON mid-string
    and loses the whole batch, which is how this first failed in the wild:
    2,867 output tokens came back, 2,260 of them reasoning, against a
    ceiling of 2,896."""
    worker = _places_worker(tmp_path)
    worker.batch_size = 12

    with_thinking = worker.room_for(12)
    worker.thinking = False
    without = worker.room_for(12)

    assert with_thinking > 2867      # what the wild batch actually needed
    assert without < with_thinking   # and no reasoning budget when it is off

    # The runner asks for that room, rather than a fixed guess.
    worker.thinking = True
    client = _FakeClient([{"results": []}])
    queue_mod.run(worker, [_place_item("IL", "x")], client=client,
                  ledger=Ledger(tmp_path / "l.jsonl.gz"), model=MODEL)
    assert client.rooms == [worker.room_for(1)]


def test_a_dry_run_replays_answers_a_real_model_gave(tmp_path):
    """A dry run has no client, and the ledger is keyed by model. Taking the
    model name from the client would make --dry-run match nothing and report
    an empty queue as though the work were already done."""
    worker = _places_worker(tmp_path)
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    item = _place_item("IL", "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666")
    ledger.record(Entry(
        task=worker.task, input_key=worker.key(item),
        prompt_version=worker.prompt_version, model=MODEL,
        answer={"kind": "place", "census_name": "Chicago", "confidence": 0.97},
        outcome=queue_mod.ACCEPTED,
    ))

    named = queue_mod.run(worker, [item], client=None, ledger=ledger,
                          dry_run=True, model=MODEL)
    assert named.replayed == 1

    # Without the name there is nothing to replay against, and the run must
    # not pretend the row was handled.
    anonymous = queue_mod.run(worker, [item], client=None, ledger=ledger,
                              dry_run=True)
    assert anonymous.replayed == 0


def test_a_row_the_model_did_not_answer_is_left_unanswered(tmp_path):
    """Batching means one reply carries many rows. A reply that omits a row
    is not evidence about it, and position is not an identifier."""
    answers = queue_mod._answers_by_id(
        {"results": [{"id": 2, "kind": "place"}, {"id": 99, "kind": "place"},
                     {"id": "x"}, "junk"]},
        size=3,
    )
    assert set(answers) == {2}


def test_an_answer_missing_what_was_asked_for_is_a_failure_not_a_guess():
    assert shape_problem({"kind": "place"}, {"kind": str, "confidence": float}) \
        == "missing key 'confidence'"
    assert shape_problem({"results": "no"}, {"results": list}) \
        == "key 'results' is str, expected list"
    assert shape_problem(["a"], {}) == "expected a JSON object, got list"
    assert shape_problem({"results": []}, {"results": list}) is None


def test_editing_a_prompt_and_bumping_its_version_re_asks(tmp_path):
    """Bumping the version is how you say the question changed, so an answer
    to the old one must not stand in for one to the new. Scoped any looser,
    a version bump is a silent no-op: the run reports nothing to do, spends
    nothing, and hands back the old answers under the new version's name."""
    worker = _places_worker(tmp_path)
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    item = _place_item("IL", "somewhere odd")
    ledger.record(Entry(
        task=worker.task, input_key=worker.key(item),
        prompt_version="places-v1", model=MODEL,
        answer={"kind": "unresolvable", "census_name": "", "confidence": 0.9},
        outcome=queue_mod.REJECTED,
    ))

    # Same prompt: settled, not re-bought.
    worker.prompt_version = "places-v1"
    client = _FakeClient([{"results": [{"id": 1, "kind": "unresolvable",
                                        "census_name": "", "confidence": 0.9}]}])
    assert queue_mod.run(worker, [item], client=client, ledger=ledger,
                         model=MODEL).asked == 0
    assert client.calls == 0

    # Different prompt: a different question, so it gets asked.
    worker.prompt_version = "places-v2"
    client = _FakeClient([{"results": [{"id": 1, "kind": "place",
                                        "census_name": "Chicago", "confidence": 0.95}]}])
    tally = queue_mod.run(worker, [item], client=client, ledger=ledger, model=MODEL)
    assert client.calls == 1 and tally.asked == 1


def test_a_sweep_comparing_two_models_asks_both(tmp_path):
    """`answered` is blind to the model on purpose, so production does not
    re-buy a queue when a default changes. A sweep comparing models is the
    one case where that protection is wrong: the second model would find the
    first's answers, skip every row, and be scored on nothing."""
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    ledger.record(Entry(task="industry", input_key="a", prompt_version="p",
                        model="vendor/flash", answer={"naics": "62"},
                        outcome="accepted"))

    assert ledger.has_any("industry", "p", "vendor/flash")
    assert not ledger.has_any("industry", "p", "vendor/pro")
    # ...and the older, model-blind question still says the row is settled,
    # which is what made the sweep skip it.
    assert ledger.answered("industry", "a", "p")


def test_settings_that_change_the_answer_are_part_of_the_key(tmp_path):
    """A sweep varies batch size and thinking without touching the prompt
    text. Left out of the key, the second configuration finds the first's
    answers already banked, skips every row, and gets scored on them — the
    same mistake as reusing a prompt name after editing it, one level down."""
    from warnlive.adjudicate.sweep import Config

    versions = {
        Config("p", batch_size=b, thinking=t).version(20)
        for b in (10, 20) for t in (True, False)
    }
    assert len(versions) == 4

    # The default combination keeps the bare prompt name, so answers already
    # bought under it replay rather than being purchased twice.
    assert Config("p").version(20) == "p"


def test_a_calibration_that_graded_nothing_says_so(tmp_path):
    """It once produced a full precision curve off a previous prompt's
    answers and reported it as the new prompt's. An empty grading is a
    failure, not a result."""
    worker = adj_industry.Industry()
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    items = [{"normalized_name": "a", "employer_name": "A",
              "workers": 10, "truth": "62"}]
    with pytest.raises(RuntimeError, match="nothing to grade"):
        adj_industry.score(items, worker, ledger, MODEL)


def test_two_prompt_versions_are_never_graded_as_one(tmp_path):
    """Both live in the ledger by design, so the scorer has to pick one or
    it counts every employer twice and blends two curves into a third that
    describes neither."""
    worker = adj_industry.Industry()
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    items = [{"normalized_name": "a", "employer_name": "A",
              "workers": 10, "truth": "62"}]
    for version, naics in (("industry-v2", "23"), (worker.prompt_version, "62")):
        ledger.record(Entry(task="industry", input_key="a", prompt_version=version,
                            model=MODEL, answer={"naics": naics, "confidence": 0.95},
                            outcome="accepted"))

    monkey = adj_industry.CALIBRATION_PATH
    adj_industry.CALIBRATION_PATH = tmp_path / "cal.csv"
    try:
        curve = adj_industry.score(items, worker, ledger, MODEL)
    finally:
        adj_industry.CALIBRATION_PATH = monkey
    # One employer graded, against the current prompt's answer, which is right.
    assert curve[0]["answered"] == 1
    assert curve[0]["precision"] == 1.0


def test_a_question_already_asked_is_not_asked_again(tmp_path):
    """The queues are rebuilt from the database each refresh, so without
    this every run would re-buy every answer it already has."""
    worker = _places_worker(tmp_path)
    ledger = Ledger(tmp_path / "led.jsonl.gz")
    item = _place_item("KS", "4 - Workforce Investment Area IV")
    ledger.record(Entry(
        task=worker.task, input_key=worker.key(item),
        prompt_version="places-v0", model="some-other-model",
        answer={"kind": "unresolvable", "census_name": "", "confidence": 0.99},
        outcome=queue_mod.REJECTED,
    ))
    tally = queue_mod.run(worker, [item], client=None, ledger=ledger, dry_run=True)
    assert tally.seen == 0 and tally.asked == 0


def test_the_ledger_survives_a_run_that_was_interrupted(tmp_path):
    """An append-only file written by a killed run ends mid-line. That
    costs one re-ask; it must not cost the whole file."""
    path = tmp_path / "led.jsonl.gz"
    ledger = Ledger(path)
    ledger.record(Entry(task="places", input_key="a", prompt_version="v1",
                        model="m", answer={"kind": "place"}, outcome="accepted"))
    ledger.flush()
    with gzip.open(path, "at") as fh:
        fh.write('{"task": "places", "input_key": "b", "promp')

    reopened = Ledger(path)
    assert reopened.get("places", "a", "v1", "m") is not None
    assert reopened.get("places", "b", "v1", "m") is None


# -- configuration ----------------------------------------------------------

def test_the_model_is_chosen_by_flag_then_environment_then_file(monkeypatch):
    monkeypatch.delenv("WARNLIVE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("WARNLIVE_LLM_MODEL", raising=False)
    assert resolve().provider == "deepseek"

    monkeypatch.setenv("WARNLIVE_LLM_PROVIDER", "openrouter")
    assert resolve().provider == "openrouter"
    assert resolve(provider="deepseek").provider == "deepseek"

    with pytest.raises(KeyError):
        resolve(provider="nowhere")
    with pytest.raises(KeyError):
        resolve(model="enormous")


def test_a_model_with_no_prices_reports_tokens_rather_than_a_wrong_cost():
    """A stale price is worse than no price: it reads as a measurement."""
    from warnlive.adjudicate.client import Usage

    priced = resolve("deepseek", "flash")
    # Built here rather than taken from providers.yaml: the point is what
    # happens when a provider has no rates on file, not which ones do today.
    unpriced = Model(provider="elsewhere", alias="flash", slug="m",
                     base_url="https://example.invalid", api_key_env="NO_KEY")
    body = {"usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 900,
                      "prompt_cache_miss_tokens": 100, "completion_tokens": 500}}

    metered = Usage()
    metered.add(priced, body)
    assert metered.cost == pytest.approx(
        (900 * 0.0028 + 100 * 0.14 + 500 * 0.28) / 1_000_000
    )
    assert not metered.unpriced

    guessed = Usage()
    guessed.add(unpriced, body)
    assert guessed.unpriced and guessed.cost == 0.0
    assert guessed.input == 1000 and "cost unknown" in guessed.summary()


def test_a_budget_stops_a_run_before_it_spends_past_it():
    from warnlive.adjudicate.client import BudgetExceeded, Client

    client = Client(resolve("deepseek", "flash"), budget=0.01)
    client.usage.cost = 0.011
    with pytest.raises(BudgetExceeded):
        client.check_budget()

    # A budget on a model that cannot be metered is refused, not ignored.
    unmetered = Client(
        Model(provider="elsewhere", alias="flash", slug="m",
              base_url="https://example.invalid", api_key_env="NO_KEY"),
        budget=1.0,
    )
    with pytest.raises(BudgetExceeded):
        unmetered.check_budget()


def test_a_missing_key_stops_the_run_rather_than_half_producing_a_file(monkeypatch):
    from warnlive.adjudicate.client import Client

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        Client(resolve("deepseek", "flash")).key()


def test_a_key_in_the_env_file_is_read_without_overriding_the_shell(tmp_path, monkeypatch):
    """The .env file is configuration; a shell that forgot to source it is not.

    Every command reads credentials from os.environ, so before this the file
    was inert unless somebody remembered to source it — and a run that
    forgot queued two hundred rows, called nothing, and reported two hundred
    failures. What is already exported still wins, so a deliberate override
    on the command line is not quietly replaced by the file.
    """
    from warnlive.cli import _load_env

    env = tmp_path / ".env"
    env.write_text(
        '# a comment\n'
        'DEEPSEEK_API_KEY="sk-from-file"\n'
        '\n'
        "OTHER_KEY='plain'\n"
        "not a pair\n"
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OTHER_KEY", "from-the-shell")

    _load_env(env)

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-file"
    assert os.environ["OTHER_KEY"] == "from-the-shell"

    # No file is not an error; it is the ordinary case in CI.
    _load_env(tmp_path / "absent")


def test_confirm_stages_to_its_own_file_not_identitys():
    """Identity's write() rewrites the staging file it is given; confirm
    pointed at identity's would replace two hundred review rows with its
    handful."""
    from warnlive.adjudicate import confirm as adj_confirm

    assert adj_confirm.STAGING_PATH != adj_identity.STAGING_PATH
