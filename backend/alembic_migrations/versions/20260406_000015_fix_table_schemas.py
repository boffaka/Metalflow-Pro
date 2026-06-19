"""Fix table schemas — align decisions, test_campaigns, working_capital with route code

Revision ID: 20260406_000015
Revises: 20260405_000014
Create Date: 2026-04-06
"""
from alembic import op

revision = '20260406_000015'
down_revision = '20260405_000014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── decisions ─────────────────────────────────────────────────────────────
    # Old schema (schema.sql): decision_text, justification, date, decided_by
    # Route expects: title, description, status, gate_id, decided_at, updated_at
    op.execute("""
        DO $$
        BEGIN
            -- Add missing columns if they don't exist
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='title') THEN
                ALTER TABLE decisions ADD COLUMN title TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='description') THEN
                ALTER TABLE decisions ADD COLUMN description TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='status') THEN
                ALTER TABLE decisions ADD COLUMN status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','accepted','rejected','deferred'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='gate_id') THEN
                ALTER TABLE decisions ADD COLUMN gate_id UUID REFERENCES stage_gates(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='decided_at') THEN
                ALTER TABLE decisions ADD COLUMN decided_at TIMESTAMPTZ;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='decisions' AND column_name='updated_at') THEN
                ALTER TABLE decisions ADD COLUMN updated_at TIMESTAMPTZ DEFAULT now();
            END IF;

            -- Migrate data: copy decision_text → title if column exists
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='decisions' AND column_name='decision_text') THEN
                UPDATE decisions SET title = decision_text WHERE title IS NULL;
                ALTER TABLE decisions DROP COLUMN IF EXISTS decision_text;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='decisions' AND column_name='justification') THEN
                UPDATE decisions SET description = justification WHERE description IS NULL;
                ALTER TABLE decisions DROP COLUMN IF EXISTS justification;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='decisions' AND column_name='date') THEN
                ALTER TABLE decisions DROP COLUMN IF EXISTS date;
            END IF;

            -- Ensure title is NOT NULL (set default if empty)
            UPDATE decisions SET title = 'Décision sans titre' WHERE title IS NULL OR title = '';
            ALTER TABLE decisions ALTER COLUMN title SET NOT NULL;
        END $$;
    """)

    # ── test_campaigns ────────────────────────────────────────────────────────
    # Old schema: campaign_name, test_type CHECK(...), laboratory, start_date, end_date, cost_usd, results_summary
    # Route expects: name, description, status CHECK(planned/active/complete/cancelled), started_at, completed_at
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='test_campaigns' AND column_name='name') THEN
                ALTER TABLE test_campaigns ADD COLUMN name TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='test_campaigns' AND column_name='description') THEN
                ALTER TABLE test_campaigns ADD COLUMN description TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='test_campaigns' AND column_name='status') THEN
                ALTER TABLE test_campaigns ADD COLUMN status TEXT NOT NULL DEFAULT 'planned';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='test_campaigns' AND column_name='started_at') THEN
                ALTER TABLE test_campaigns ADD COLUMN started_at TIMESTAMPTZ;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='test_campaigns' AND column_name='completed_at') THEN
                ALTER TABLE test_campaigns ADD COLUMN completed_at TIMESTAMPTZ;
            END IF;

            -- Migrate data
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='campaign_name') THEN
                UPDATE test_campaigns SET name = campaign_name WHERE name IS NULL;
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS campaign_name;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='results_summary') THEN
                UPDATE test_campaigns SET description = results_summary WHERE description IS NULL;
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS results_summary;
            END IF;

            -- Migrate status from test_type to status
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='test_type') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS test_type;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='laboratory') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS laboratory;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='start_date') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS start_date;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='end_date') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS end_date;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='cost_usd') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS cost_usd;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='ore_types') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS ore_types;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='test_campaigns' AND column_name='protocol') THEN
                ALTER TABLE test_campaigns DROP COLUMN IF EXISTS protocol;
            END IF;

            UPDATE test_campaigns SET name = 'Campagne sans nom' WHERE name IS NULL OR name = '';
            ALTER TABLE test_campaigns ALTER COLUMN name SET NOT NULL;

            -- Add status constraint (drop and re-add)
            ALTER TABLE test_campaigns DROP CONSTRAINT IF EXISTS test_campaigns_test_type_check;
            ALTER TABLE test_campaigns DROP CONSTRAINT IF EXISTS test_campaigns_status_check;
            ALTER TABLE test_campaigns ADD CONSTRAINT test_campaigns_status_check
                CHECK (status IN ('planned','active','complete','cancelled'));
        END $$;
    """)

    # Create campaign_samples if it doesn't exist
    op.execute("""
        CREATE TABLE IF NOT EXISTS campaign_samples (
          campaign_id  UUID REFERENCES test_campaigns(id) ON DELETE CASCADE,
          sample_id    UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
          added_at     TIMESTAMPTZ DEFAULT now(),
          PRIMARY KEY (campaign_id, sample_id)
        );
    """)

    # ── working_capital ───────────────────────────────────────────────────────
    # Old schema: category, description, amount_usd, timing_months
    # Route expects: receivable_days, inventory_days, payable_days,
    #                other_current_assets, other_current_liabilities, currency, updated_at, UNIQUE(project_id)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='working_capital' AND column_name='receivable_days') THEN
                -- Drop the old table and recreate with correct schema
                DROP TABLE IF EXISTS working_capital;
                CREATE TABLE working_capital (
                  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  project_id                UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                  receivable_days           INTEGER NOT NULL DEFAULT 30 CHECK (receivable_days >= 0),
                  inventory_days            INTEGER NOT NULL DEFAULT 45 CHECK (inventory_days >= 0),
                  payable_days              INTEGER NOT NULL DEFAULT 30 CHECK (payable_days >= 0),
                  other_current_assets      NUMERIC(14,2) NOT NULL DEFAULT 0,
                  other_current_liabilities NUMERIC(14,2) NOT NULL DEFAULT 0,
                  currency                  TEXT NOT NULL DEFAULT 'USD',
                  updated_at                TIMESTAMPTZ DEFAULT now(),
                  UNIQUE (project_id)
                );
            END IF;
        END $$;
    """)


def downgrade() -> None:
    pass
