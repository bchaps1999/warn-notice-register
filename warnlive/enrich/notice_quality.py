"""Typed, source-backed facts that supplement an agency's listing fields.

The listing's employer, date, and location remain intact because they may be
part of its stable key.  ``quality_evidence`` in source_details records what a
specific official document establishes, including the role of each place and
date.  Export and site builders use the same projection.
"""

from __future__ import annotations

import json
from datetime import date

from warnlive.enrich.site_address import basis as site_address_basis

FIELDS = (
    "affected_site_address", "affected_site_city", "site_role",
    "employer_mailing_address",
    "employer_name_verbatim", "letter_date", "agency_received_date",
    "agency_notification_date", "agency_processed_date",
    "source_record_urls", "source_locators", "source_status", "timing_qc",
    "site_address_basis",
)

_AFFECTED_ROLES = {"affected_worksite", "remote_worker_location"}


def _details(value: str | dict | None) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _quality(row: dict) -> dict:
    quality = _details(row.get("source_details")).get("quality_evidence")
    return quality if isinstance(quality, dict) else {}


def _one(values: list[str]) -> str | None:
    unique = {value for value in values if value}
    return next(iter(unique)) if len(unique) == 1 else None


def affected_site(row: dict) -> dict | None:
    """One supported site, or none when the source has multiple/unknown sites."""
    sites = _quality(row).get("sites") or []
    affected = [site for site in sites if isinstance(site, dict)
                and site.get("role") in _AFFECTED_ROLES]
    if len(affected) != 1:
        return None
    return affected[0]


def geo_location(row: dict) -> str | None:
    """Location to resolve; a known mailing city is never an affected site."""
    site = affected_site(row)
    if site:
        return site.get("address") or site.get("city")
    quality = _quality(row)
    if (quality.get("location_role") == "employer_mailing"
            or quality.get("status") == "site_address_ambiguous"):
        return None
    return row.get("location")


def display_location(row: dict) -> str | None:
    """Readable affected place for the site, retaining the raw field elsewhere."""
    site = affected_site(row)
    if site:
        return site.get("city") or site.get("address")
    quality = _quality(row)
    if (quality.get("location_role") == "employer_mailing"
            or quality.get("status") == "site_address_ambiguous"):
        return None
    return row.get("location")


def resolve_geo(resolver, row: dict, fields_json: str | dict | None = None) -> dict:
    """Place and county for one notice; the CSV export and the site share it.

    A row with typed site evidence resolves the affected site (or nothing,
    for a known mailing city or an ambiguous site).  When the site's address
    names a community the Census roster lacks, the county printed on the same
    agency report row still places it (``county:filed``).  Every other row
    resolves its listing location, then the state's own city/county columns,
    then an address typed into the employer name.
    """
    state = row.get("state")
    quality = _quality(row)
    if (quality.get("sites") or quality.get("location_role") == "employer_mailing"
            or quality.get("status") == "site_address_ambiguous"):
        got = resolver.resolve(state, geo_location(row))
        site = affected_site(row)
        # A county-only alias read from the address is weaker than the
        # county printed on the same report row.
        if (got.get("geo_basis") in (None, "county_alias") and site
                and (site.get("county") or site.get("city"))):
            filed = resolver.resolve(state, None, {"raw_extra": {
                "city": site.get("city") or "", "county": site.get("county") or "",
            }})
            if filed.get("geo_basis"):
                got = filed
        return got
    return resolver.resolve(state, row.get("location"), fields_json, row.get("employer_name"))


def project(row: dict) -> dict[str, str | None]:
    details = _details(row.get("source_details"))
    quality = _quality(row)
    site = affected_site(row)
    mailing = [item.get("address") for item in quality.get("sites", [])
               if isinstance(item, dict) and item.get("role") == "employer_mailing"]
    dates = [item for item in (quality.get("dates") or details.get("dates") or [])
             if isinstance(item, dict)]
    sources = [item for item in quality.get("sources", []) if isinstance(item, dict)]
    urls = sorted({item["url"] for item in sources if item.get("url")})
    locators = [{key: item[key] for key in
                 ("url", "artifact", "sha256", "source_row_sha256", "page", "row", "sheet", "retrieved_on")
                 if item.get(key) is not None} for item in sources]
    locators.sort(key=lambda item: json.dumps(item, sort_keys=True))

    timing = None
    if (row.get("notice_date_precision") == "day"
            and row.get("notice_date_basis") in
            {"reported", "derived_from_reported_components"}
            and row.get("effective_date_precision") == "day"
            and row.get("effective_date_basis") in
            {"reported", "derived_from_reported_components"}):
        try:
            days = (date.fromisoformat(row["effective_date"])
                    - date.fromisoformat(row["notice_date"])).days
        except (TypeError, ValueError):
            pass
        else:
            timing = ("negative" if days < 0 else "same_day" if days == 0
                      else "long_300" if days >= 300 else None)
    # A notice date later than the day this pipeline first saw the listing
    # cannot be when that listing's notice was given; the cell is kept as
    # filed ("10/30/26" seen on 2026-09-24) and flagged, never corrected.
    notice, seen = row.get("notice_date"), (row.get("first_seen") or "")[:10]
    if notice and len(seen) == 10 and notice > seen:
        timing = "notice_after_first_seen"

    return {
        "affected_site_address": site.get("address") if site else None,
        "affected_site_city": site.get("city") if site else None,
        "site_role": site.get("role") if site else None,
        "employer_mailing_address": _one(mailing),
        "employer_name_verbatim": quality.get("employer_name_verbatim"),
        "letter_date": _one([item.get("date") for item in dates
                             if item.get("role") == "letter_date"]),
        "agency_received_date": (_one([item.get("date") for item in dates
                                       if item.get("role") == "agency_received"])
                                 or details.get("agency_received_date")),
        "agency_notification_date": (_one([item.get("date") for item in dates
                                           if item.get("role") == "agency_notification"])
                                     or details.get("agency_notification_date")),
        "agency_processed_date": _one([item.get("date") for item in dates
                                       if item.get("role") == "agency_processed"]),
        "source_record_urls": json.dumps(urls, separators=(",", ":")) if urls else None,
        "source_locators": json.dumps(locators, sort_keys=True, separators=(",", ":"))
                           if locators else None,
        "source_status": quality.get("status"),
        "timing_qc": timing,
        "site_address_basis": site_address_basis(row),
    }
