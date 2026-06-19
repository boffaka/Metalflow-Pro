"""
Add generic audit trail trigger for strict NI 43-101 data versioning.

This migration creates a trigger function that automatically records
INSERT, UPDATE, and DELETE operations on critical engineering tables
(like lims_* and design_criteria) into the audit_events table.
"""
revision = "000034"
down_revision = "000033"
revises = "000033"

from alembic import op


def upgrade():
    op.execute("""
        -- 1. Create a generic audit trigger function
        CREATE OR REPLACE FUNCTION process_audit_trigger()
        RETURNS TRIGGER AS $$
        DECLARE
            v_old_data JSONB;
            v_new_data JSONB;
            v_action VARCHAR(10);
            v_project_id UUID;
            v_entity_id VARCHAR;
        BEGIN
            IF (TG_OP = 'DELETE') THEN
                v_action := 'DELETE';
                v_old_data := row_to_json(OLD)::JSONB;
                v_new_data := NULL;
                v_project_id := OLD.project_id;
                v_entity_id := OLD.id::VARCHAR;
            ELSIF (TG_OP = 'UPDATE') THEN
                v_action := 'UPDATE';
                v_old_data := row_to_json(OLD)::JSONB;
                v_new_data := row_to_json(NEW)::JSONB;
                v_project_id := NEW.project_id;
                v_entity_id := NEW.id::VARCHAR;
            ELSIF (TG_OP = 'INSERT') THEN
                v_action := 'INSERT';
                v_old_data := NULL;
                v_new_data := row_to_json(NEW)::JSONB;
                v_project_id := NEW.project_id;
                v_entity_id := NEW.id::VARCHAR;
            END IF;

            -- We don't have the user_id context inside the trigger easily without
            -- session variables, so we log it as a systemic change if not passed.
            -- In a real production environment, we'd use current_setting('myapp.user_id', true).

            INSERT INTO audit_events (
                project_id,
                entity_type,
                entity_id,
                action,
                old_value,
                new_value,
                source,
                checksum
            ) VALUES (
                v_project_id,
                TG_TABLE_NAME,
                v_entity_id::UUID,
                v_action,
                v_old_data,
                v_new_data,
                'db_trigger',
                encode(digest(TG_TABLE_NAME || v_entity_id || v_action || COALESCE(v_new_data::TEXT, ''), 'sha256'), 'hex')
            );

            IF (TG_OP = 'DELETE') THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;

        -- 2. Apply the trigger to critical LIMS tables
        CREATE TRIGGER audit_lims_a1_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_a1 FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
        CREATE TRIGGER audit_lims_b1_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_b1 FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
        CREATE TRIGGER audit_lims_c2_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_c2 FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
        CREATE TRIGGER audit_lims_d1_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_d1 FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
        CREATE TRIGGER audit_lims_e1_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_e1 FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
        CREATE TRIGGER audit_lims_flotation_trigger AFTER INSERT OR UPDATE OR DELETE ON lims_flotation FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();

        -- 3. Apply the trigger to Design Criteria
        CREATE TRIGGER audit_design_criteria_trigger AFTER INSERT OR UPDATE OR DELETE ON design_criteria FOR EACH ROW EXECUTE FUNCTION process_audit_trigger();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER IF EXISTS audit_design_criteria_trigger ON design_criteria;
        DROP TRIGGER IF EXISTS audit_lims_flotation_trigger ON lims_flotation;
        DROP TRIGGER IF EXISTS audit_lims_e1_trigger ON lims_e1;
        DROP TRIGGER IF EXISTS audit_lims_d1_trigger ON lims_d1;
        DROP TRIGGER IF EXISTS audit_lims_c2_trigger ON lims_c2;
        DROP TRIGGER IF EXISTS audit_lims_b1_trigger ON lims_b1;
        DROP TRIGGER IF EXISTS audit_lims_a1_trigger ON lims_a1;
        DROP FUNCTION IF EXISTS process_audit_trigger();
    """)
