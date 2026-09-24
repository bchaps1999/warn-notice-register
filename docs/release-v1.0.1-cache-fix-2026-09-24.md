# WARN Notice Register v1.0.1 — site cache fix

The source-only database and exports are unchanged from v1.0.0: 71,490
notices, 79,261 versions, 7,482,177 reported affected workers, and zero
inferred links. The [v1.0.0 release notes](release-v1-2026-09-24.md) cover
provenance, excluded rows, and remaining coverage gaps.

The site now adds a version derived from the deployed database dump to every
JSON request. GitHub Pages can cache files at the same URL across deployments;
this ensures a returning browser requests the new data immediately after an
updated site build rather than temporarily showing the previous totals. The
site still caches each fetched response for the current page session.
