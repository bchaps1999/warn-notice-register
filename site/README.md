# WARN Notice Register — site

Static React SPA exploring the consolidated WARN dataset. Fully client-side;
data is pre-built JSON emitted from the SQLite DB.

The checked-in exports and deployed site still represent the older release.
The latest unpublished agency-only candidate is documented in
[`docs/agency-mo-expansion-2026-09-24.md`](../docs/agency-mo-expansion-2026-09-24.md).
Build a candidate preview from its SQLite file explicitly; do not mix JSON
from one database with counts from another.

## Local development

```bash
# from the repo root
.venv/bin/warnlive unpack-db            # restore data/warn.sqlite from data/warn.sql.gz
.venv/bin/warnlive build-site           # writes site/public/data/ (gitignored)
cd site
npm install
npm run dev
```

`?theme=dark` / `?theme=light` on any URL forces the theme (persisted).

For a candidate preview, run `warnlive build-site --db PATH --out
site/public/data --as-of YYYY-MM-DD` from the repository root, then run the
Vite site locally. This replaces only ignored local site JSON. The `--as-of`
date keeps trailing-period charts reproducible.

## Deploys

Deployed to GitHub Pages at https://bchaps1999.github.io/warn-notice-register/.

- Scheduled scrapes (daily/weekly) rebuild the data and deploy automatically
  via `.github/actions/deploy-site` (configure-pages → upload-pages-artifact →
  deploy-pages; no secrets needed).
- Pushing changes under `site/**` triggers `.github/workflows/deploy-site.yml`,
  which rebuilds from the last committed database.
- CI builds with `SITE_BASE=/warn-notice-register/` (Vite `base`); local dev serves at `/`.
  `dist/index.html` is copied to `404.html` so deep links work on Pages.

## Data contract

Produced by `warnlive/store/site_export.py`:
`/data/meta.json`, `/data/national.json`, `/data/states/{xx}.json`,
`/data/index.json` (columnar, all notices), `/data/notices/{pp}.json`
(256 detail shards by dedupe-key prefix; URL key length is recorded in
`meta.json` as `key_prefix_len`). The build also emits
`/data/employers/index.json` and employer detail shards. The columnar index
includes date precision and basis arrays; `date` is a timeline fallback
(notice date, then action date), not always a legal notice date.
