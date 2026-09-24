-- Shared source of truth for the Fund Analysis Platform. Replaces
-- per-browser localStorage/IndexedDB: every user hitting the API sees the
-- same live rows. MySQL 8+ (Aiven free tier or equivalent).

CREATE TABLE IF NOT EXISTS companies (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(255) NOT NULL,
  sector        VARCHAR(120) NOT NULL DEFAULT 'Unclassified',
  notes         TEXT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_companies_name (name)
) ENGINE=InnoDB;

-- One row per company per quarter. Fundamental (cat/cat2/rec) and
-- Technical (tech_rec) are independent fields on the same row - Technical
-- is never auto-filled, only ever set by a direct edit or a confirmed
-- override.
CREATE TABLE IF NOT EXISTS quarterly_data (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  company_id      INT NOT NULL,
  quarter         VARCHAR(20) NOT NULL,             -- e.g. 'Q1 2026'
  cat             VARCHAR(40) DEFAULT '',
  cat2            VARCHAR(40) DEFAULT '',
  cat2_reason     VARCHAR(20) DEFAULT '',            -- 'tie' | 'secondary' | ''
  rec             VARCHAR(10) DEFAULT '',            -- 'Buy' | 'Hold' | 'Sell' | ''
  rec_suggested   TINYINT(1) NOT NULL DEFAULT 0,     -- rule-engine suggestion, not yet confirmed
  cat_confirmed   TINYINT(1) NOT NULL DEFAULT 0,     -- locked by a manual override - Sync/Autofill may not touch
  rec_confirmed   TINYINT(1) NOT NULL DEFAULT 0,
  tech_rec        VARCHAR(10) DEFAULT '',            -- purely manual, independent of Fundamental
  tech_rec_confirmed TINYINT(1) NOT NULL DEFAULT 0,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  UNIQUE KEY uq_company_quarter (company_id, quarter)
) ENGINE=InnoDB;

-- Durable manual-correction log (Priority 5). Inserting a row here and
-- applying it is how a correction gets made from now on - never a source
-- edit. `applied` + `applied_at` record whether/when the API has already
-- folded this row into quarterly_data, so re-running the apply step is
-- idempotent and re-sends of the same row are safely skipped.
CREATE TABLE IF NOT EXISTS overrides (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  company           VARCHAR(255) NOT NULL,
  quarter           VARCHAR(20) NOT NULL,
  field             VARCHAR(20) NOT NULL,            -- 'cat' | 'rec' | 'techRec'
  corrected_value   VARCHAR(100) NOT NULL,
  confirmed         TINYINT(1) NOT NULL DEFAULT 1,
  applied           TINYINT(1) NOT NULL DEFAULT 0,
  applied_at        TIMESTAMP NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Fund Factsheets (Priority 8). One row per uploaded fund/scheme.
CREATE TABLE IF NOT EXISTS funds (
  id            VARCHAR(40) PRIMARY KEY,
  amc           VARCHAR(255) DEFAULT '',
  fund_name     VARCHAR(255) NOT NULL,
  upload_date   TIMESTAMP NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Top-15-by-%-AUM holdings for a fund, snapshotted at upload time
-- (fundamental/technical are copied from quarterly_data as of that
-- moment - they do not silently drift if the ledger changes later).
CREATE TABLE IF NOT EXISTS fund_holdings (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  fund_id             VARCHAR(40) NOT NULL,
  name                VARCHAR(255) NOT NULL,          -- raw name as extracted from the PDF
  pct_aum             DECIMAL(6,3) NOT NULL,
  matched_company_id  INT,                            -- NULL if unmatched - never guessed
  fundamental         VARCHAR(10),                     -- snapshot, NULL if unmatched or unset
  technical           VARCHAR(10),
  FOREIGN KEY (fund_id) REFERENCES funds(id) ON DELETE CASCADE,
  FOREIGN KEY (matched_company_id) REFERENCES companies(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE INDEX idx_quarterly_data_quarter ON quarterly_data(quarter);
CREATE INDEX idx_fund_holdings_fund ON fund_holdings(fund_id);
CREATE INDEX idx_fund_holdings_matched ON fund_holdings(matched_company_id);
CREATE INDEX idx_overrides_lookup ON overrides(company, quarter, field);
