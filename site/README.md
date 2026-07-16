# WARN Notice Register — site

Static React SPA exploring the consolidated WARN dataset. Fully client-side;
data is pre-built JSON emitted from the SQLite DB.

## Local development

```bash
# from the repo root
gunzip -kf data/warn.sqlite.gz          # if you don't have the raw DB locally
.venv/bin/warnlive build-site           # writes site/public/data/ (gitignored)
cd site
npm install
npm run dev
```

`?theme=dark` / `?theme=light` on any URL forces the theme (persisted).

## Deploys

- Scheduled scrapes (daily/weekly) rebuild the data and deploy automatically
  via `.github/actions/deploy-site` → `wrangler pages deploy`.
- Pushing changes under `site/**` triggers `.github/workflows/deploy-site.yml`,
  which rebuilds from the last committed database.
- One-time setup: `wrangler login && npx wrangler pages project create warn-live`,
  then add `CLOUDFLARE_API_TOKEN` (Pages:Edit) and `CLOUDFLARE_ACCOUNT_ID`
  as GitHub Actions secrets. Until the secrets exist, CI builds the site but
  skips the deploy step.

## Data contract

Produced by `warnlive/store/site_export.py`:
`/data/meta.json`, `/data/national.json`, `/data/states/{xx}.json`,
`/data/index.json` (columnar, all notices), `/data/notices/{pp}.json`
(256 detail shards by dedupe_key prefix; URLs use the 8-char key prefix).
