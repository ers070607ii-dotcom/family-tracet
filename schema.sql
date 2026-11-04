-- Family Budget Tracker: SQLite schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS households (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    household_id  INTEGER NOT NULL REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS limits (
    household_id  INTEGER NOT NULL REFERENCES households(id),
    category      TEXT NOT NULL CHECK (category IN ('Groceries','Transport','Utilities','Health','Other')),
    monthly_limit INTEGER NOT NULL CHECK (monthly_limit > 0),
    PRIMARY KEY (household_id, category)
);

-- Data dictionary rules from Task 7 are enforced here as CHECK constraints
CREATE TABLE IF NOT EXISTS expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,      -- shown as E-(1000+id)
    household_id  INTEGER NOT NULL REFERENCES households(id),
    user_id       INTEGER NOT NULL REFERENCES users(id),
    purchase_date TEXT NOT NULL
                  CHECK (purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    amount        INTEGER NOT NULL CHECK (amount > 0),
    category      TEXT NOT NULL CHECK (category IN ('Groceries','Transport','Utilities','Health','Other')),
    store         TEXT,
    note          TEXT,
    receipt_path  TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Speeds up the duplicate check (same date + amount + category)
CREATE INDEX IF NOT EXISTS idx_expenses_dup
    ON expenses (household_id, purchase_date, amount, category);

-- Example queries
-- Monthly spending per category vs limit:
--   SELECT l.category, l.monthly_limit, COALESCE(SUM(e.amount),0) AS spent
--   FROM limits l
--   LEFT JOIN expenses e ON e.household_id = l.household_id
--        AND e.category = l.category
--        AND strftime('%Y-%m', e.purchase_date) = '2026-10'
--   WHERE l.household_id = 1
--   GROUP BY l.category;
--
-- Find duplicate purchases (uniqueness audit):
--   SELECT purchase_date, amount, category, COUNT(*) AS copies
--   FROM expenses GROUP BY household_id, purchase_date, amount, category
--   HAVING COUNT(*) > 1;
