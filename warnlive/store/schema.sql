-- Canonical consolidated WARN store.
-- Amendments are stored as versions in notice_versions; the notices table
-- always reflects the latest version, denormalized for easy export.

CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,       -- sha1(state|employer|notice_date|location), normalized
    state TEXT NOT NULL,                   -- 2-letter postal, uppercase
    employer_name TEXT,
    location TEXT,
    notice_date TEXT,                      -- ISO-8601 date or NULL
    effective_date TEXT,
    effective_date_end TEXT,              -- last known date of a reported interval
    effective_date_precision TEXT,        -- day | month | unknown
    effective_date_basis TEXT,            -- source-specific interpretation
    effective_date_end_precision TEXT,    -- day | month | unknown
    effective_date_end_basis TEXT,        -- source-specific interpretation
    notice_date_precision TEXT,           -- day | month | unknown
    notice_date_basis TEXT,               -- reported | inferred_from_effective
    source_identity TEXT,                 -- stable filing id with source namespace
    source_details TEXT,                  -- JSON: dated components, sites, phases, issues
    employees_affected INTEGER,
    layoff_type TEXT,                      -- closure | mass_layoff | unknown
    is_temporary INTEGER,                  -- 1/0/NULL
    is_amendment INTEGER DEFAULT 0,        -- source flagged this filing as an amendment
    source_url TEXT,
    source_notice_id TEXT,                 -- upstream transformer hash_id
    is_amended INTEGER DEFAULT 0,          -- we have observed >1 version
    current_version INTEGER DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT,                        -- NULL = present in the state's latest run;
                                           -- a date = last run that still listed it
    site_address TEXT                      -- enrichment: street address of the layoff
                                           -- site. Kept out of location (and thus the
                                           -- dedupe key) so daily scrapes keep matching.
);
CREATE INDEX IF NOT EXISTS idx_notices_state_date ON notices(state, notice_date);

CREATE TABLE IF NOT EXISTS notice_versions (
    id INTEGER PRIMARY KEY,
    notice_id INTEGER NOT NULL REFERENCES notices(id),
    version INTEGER NOT NULL,
    raw_record_hash TEXT NOT NULL,         -- sha1 of the canonical field values
    fields_json TEXT NOT NULL,             -- full canonical fields + raw_extra at this version
    observed_at TEXT NOT NULL,
    UNIQUE(notice_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL                  -- scheduled | manual | backfill
);

CREATE TABLE IF NOT EXISTS state_runs (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    state TEXT NOT NULL,
    verdict TEXT NOT NULL,                 -- ok | degraded | failed | skipped
    raw_rows INTEGER,
    normalized_rows INTEGER,
    new_notices INTEGER,
    updated_notices INTEGER,
    checks_json TEXT,
    error TEXT,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_runs_state ON state_runs(state, id);

-- Revision/duplicate links between notices. Linking is additive and
-- auditable: notices are never merged or deleted by detection.
CREATE TABLE IF NOT EXISTS notice_links (
    id INTEGER PRIMARY KEY,
    notice_id INTEGER NOT NULL REFERENCES notices(id),   -- the later/derived filing
    related_id INTEGER NOT NULL REFERENCES notices(id),  -- the earlier/base filing
    kind TEXT NOT NULL,                    -- amendment_of | possible_duplicate
    score REAL NOT NULL,                   -- 0..1 confidence
    method TEXT NOT NULL,                  -- marker | declared | amendment | fuzzy
    detail TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(notice_id, related_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_links_notice ON notice_links(notice_id);

-- Retain every official source observation, including excluded rows, separately
-- from admitted notices. Legacy cleaning columns remain in older databases.
CREATE TABLE IF NOT EXISTS source_observations (
    id INTEGER PRIMARY KEY,
    source_bundle_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    observation_kind TEXT NOT NULL,
    source_artifact TEXT NOT NULL,
    source_row TEXT NOT NULL,
    source_row_sha256 TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    deterministic_json TEXT NOT NULL,
    admission_status TEXT NOT NULL CHECK (admission_status IN
        ('admitted', 'event_unresolved', 'rescinded', 'annotation', 'identity_unresolved')),
    notice_id INTEGER REFERENCES notices(id),
    CHECK ((admission_status = 'admitted' AND notice_id IS NOT NULL) OR
           (admission_status <> 'admitted' AND notice_id IS NULL)),
    UNIQUE(source_bundle_sha256, source_artifact, source_row)
);
CREATE INDEX IF NOT EXISTS idx_source_observations_state_status
    ON source_observations(state, admission_status);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
