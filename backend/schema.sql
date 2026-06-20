-- =============================================================================
-- MetalFlow Pro — Baseline Database Schema (migration 000001 only)
-- MPDPMS v4: Comprehensive mining project lifecycle management
-- =============================================================================
-- WARNING: This file captures the BASELINE schema only. Everything added by
-- subsequent Alembic migrations (tables, columns, indexes) is NOT in here.
-- To reconstruct the full current schema, first load this file then run:
--     python scripts/rebuild_db_from_migrations.py
-- That script replays every migration's DDL idempotently (safe to re-run).
-- See `scripts/rebuild_db_from_migrations.py` for details.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- CORE: Users & Projects
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT,
    token_version INTEGER NOT NULL DEFAULT 0,
    role          TEXT DEFAULT 'Read-only'
                  CHECK (role IN (
                      'Process Engineer', 'Metallurgist', 'Project Manager',
                      'Cost Engineer', 'Reviewer', 'Read-only'
                  )),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_name    TEXT NOT NULL,
    project_code    TEXT UNIQUE NOT NULL,
    target_tph      NUMERIC,
    gold_grade_g_t  NUMERIC,
    status          TEXT DEFAULT 'SCOPING',
    capex_musd      NUMERIC,
    project_owner   TEXT,
    commodity       TEXT DEFAULT 'Au',
    location        TEXT,
    capacity_mtpa   NUMERIC,
    process_options TEXT,
    -- Economic parameters (user-configurable)
    gold_price_usd_oz   NUMERIC DEFAULT 2340,
    discount_rate_pct   NUMERIC DEFAULT 5,
    mine_life_years     INTEGER DEFAULT 10,
    operating_hours_day NUMERIC DEFAULT 24,
    availability_pct    NUMERIC DEFAULT 92,
    electricity_rate    NUMERIC DEFAULT 0.075,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Project membership (Lot C Phase 1, F2): multi-user access to a project.
-- Owners are backfilled from projects.user_id by migration 000073.
CREATE TABLE IF NOT EXISTS project_members (
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);

CREATE TABLE IF NOT EXISTS refresh_sessions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash     TEXT NOT NULL UNIQUE,
    expires_at     TIMESTAMPTZ NOT NULL,
    revoked_at     TIMESTAMPTZ,
    replaced_by_id UUID REFERENCES refresh_sessions(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_user ON refresh_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_token_hash ON refresh_sessions(token_hash);

-- =============================================================================
-- LIMS: Samples & Test Results
-- =============================================================================

CREATE TABLE IF NOT EXISTS lims_samples (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id_display       TEXT NOT NULL,
    phase                   TEXT,
    sample_type             TEXT,
    lithology               TEXT,
    provenance              TEXT,
    mass_kg                 NUMERIC,
    representativity        TEXT,
    waste_rock_dilution_pct NUMERIC,
    depth_interval          TEXT,
    mass_sent_kg            NUMERIC,
    sampling_date           DATE,
    lab_receipt_date         DATE,
    sampling_method         TEXT,
    qaqc_protocol           TEXT,
    crm_standard            TEXT,
    duplicate_frequency     TEXT,
    blank_frequency         TEXT,
    packaging               TEXT,
    oxidation_state         TEXT,
    geomet_domain           TEXT,
    sample_status           TEXT,
    observations            TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_a1 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id       UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    au_g_t          NUMERIC, ag_g_t NUMERIC, cu_pct NUMERIC,
    fe_pct          NUMERIC, s_total_pct NUMERIC, s_sulfide_pct NUMERIC,
    as_ppm          NUMERIC, c_organic_pct NUMERIC, sb_ppm NUMERIC,
    hg_ppm          NUMERIC, te_ppm NUMERIC, se_ppm NUMERIC,
    sio2_pct NUMERIC, al2o3_pct NUMERIC, cao_pct NUMERIC,
    mgo_pct NUMERIC, na2o_pct NUMERIC, k2o_pct NUMERIC,
    tio2_pct NUMERIC, mno_pct NUMERIC, loi_pct NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_b1 (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id           UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    bwi_kwh_t           NUMERIC, rwi_kwh_t NUMERIC, cwi_kwh_t NUMERIC,
    p80_target_um NUMERIC, f80_um NUMERIC,
    axb NUMERIC, ta NUMERIC, dwi_kwh_m3 NUMERIC,
    mia_kwh_t NUMERIC, mib_kwh_t NUMERIC, mic_kwh_t NUMERIC, mih_kwh_t NUMERIC,
    smc_scse_kwh_t NUMERIC,
    abrasion_index_ai   NUMERIC, ucs_mpa NUMERIC,
    sg NUMERIC,
    sag_classification TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_c2 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id       UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    au_recovery_pct NUMERIC, mass_pull_pct NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_c3 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id       UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    grg_value       NUMERIC, f80_um NUMERIC,
    stage1_rec_pct  NUMERIC, stage2_rec_pct NUMERIC,
    stage3_rec_pct  NUMERIC, total_rec_pct  NUMERIC,
    k80_um NUMERIC, au_conc_g_t NUMERIC, recovery_pct NUMERIC,
    cumul_recovery_pct NUMERIC, au_recalc_g_t NUMERIC,
    au_measured_g_t NUMERIC, au_residue_g_t NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_d1 (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    test_type               TEXT,
    au_feed_g_t             NUMERIC, au_tail_g_t NUMERIC, au_recovery_pct NUMERIC,
    p80_um                  NUMERIC, pct_solids NUMERIC,
    nacn_initial_ppm        NUMERIC, nacn_residual_ppm NUMERIC,
    nacn_consumption_kg_t   NUMERIC, cao_consumption_kg_t NUMERIC,
    ph_initial              NUMERIC, ph_final NUMERIC,
    do_mg_l                 NUMERIC, o2_consumption_kg_t NUMERIC,
    temperature_c           NUMERIC,
    leach_time_h            NUMERIC,
    carbon_g_l              NUMERIC, sg NUMERIC,
    preg_robbing_index      NUMERIC,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_e1 (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                      UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id                       UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    unit_area_m2_t_d                NUMERIC, flocculant_dosage_g_t NUMERIC,
    underflow_density_pct_solids    NUMERIC,
    isr_m_h NUMERIC, fsr_m_h NUMERIC,
    underflow_sg NUMERIC, overflow_turbidity_ntu NUMERIC,
    mass_flux_t_m2_d NUMERIC, underflow_viscosity_mpa_s NUMERIC,
    created_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_e2 (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                  UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id                   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    filtration_rate_kg_m2_h     NUMERIC, cake_moisture_pct NUMERIC,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_flotation (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    au_recovery_pct         NUMERIC, sulphide_recovery_pct NUMERIC,
    concentrate_grade_g_t   NUMERIC, mass_pull_pct NUMERIC,
    frother_dosage_g_t      NUMERIC, collector_dosage_g_t NUMERIC,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_elution (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                  UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id                   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    elution_efficiency_pct      NUMERIC, temperature_c NUMERIC,
    nacn_concentration_g_l      NUMERIC, naoh_concentration_g_l NUMERIC,
    cycle_time_h                NUMERIC, carbon_loading_g_t NUMERIC,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_environmental (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    wad_cn_mg_l             NUMERIC, total_cn_mg_l NUMERIC,
    arsenic_mg_l            NUMERIC, mercury_mg_l NUMERIC,
    sulphate_mg_l           NUMERIC, acid_drainage_risk TEXT,
    ph_final                NUMERIC,
    cu_mg_l                 NUMERIC,
    fe_mg_l                 NUMERIC,
    zn_mg_l                 NUMERIC,
    so2_consumption_kg_t    NUMERIC,
    cn_wad_rebound_24h_mg_l NUMERIC,
    s_total_pct             NUMERIC,
    sulfide_s_pct           NUMERIC,
    ap_kg_caco3_t           NUMERIC,
    np_kg_caco3_t           NUMERIC,
    nnp_kg_caco3_t          NUMERIC,
    npr_ratio               NUMERIC,
    nag_kg_h2so4_t          NUMERIC,
    ph_nag                  NUMERIC,
    ph_paste                NUMERIC,
    pag_classification      TEXT,
    ard_classification      TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Idempotent migration for existing databases
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS s_total_pct NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS sulfide_s_pct NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ap_kg_caco3_t NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS np_kg_caco3_t NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS nnp_kg_caco3_t NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS npr_ratio NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS nag_kg_h2so4_t NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ph_nag NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ph_paste NUMERIC;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS pag_classification TEXT;
ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ard_classification TEXT;

CREATE TABLE IF NOT EXISTS lims_kinetics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    rec_2h      NUMERIC, rec_4h NUMERIC, rec_8h NUMERIC,
    rec_12h     NUMERIC, rec_24h NUMERIC, rec_48h NUMERIC,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_granulometry (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id           UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    p80_um              NUMERIC, d50_um NUMERIC,
    pct_plus_500um      NUMERIC, pct_plus_212um NUMERIC,
    pct_plus_150um      NUMERIC, pct_plus_106um NUMERIC,
    pct_plus_75um       NUMERIC, pct_plus_53um NUMERIC,
    pct_plus_38um       NUMERIC, pct_minus_38um NUMERIC,
    au_head_g_t         NUMERIC,
    au_plus_212um_g_t   NUMERIC, au_plus_75um_g_t NUMERIC, au_minus_38um_g_t NUMERIC,
    dist_au_plus_212um_pct NUMERIC, dist_au_plus_75um_pct NUMERIC, dist_au_minus_38um_pct NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_liberation (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id           UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    p80_grind_um        NUMERIC,
    au_free_pct         NUMERIC,
    au_sulphide_pct     NUMERIC,
    au_silicate_pct     NUMERIC,
    au_oxide_pct        NUMERIC,
    au_occluded_pct     NUMERIC,
    au_preg_rob_pct     NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lims_mineralogy (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    k80_um                  NUMERIC,
    pyrite_pct              NUMERIC, pyrrhotite_pct NUMERIC, other_sulphides_pct NUMERIC,
    quartz_pct              NUMERIC, plagioclase_pct NUMERIC, k_feldspar_pct NUMERIC,
    argilite_kaolinite_pct  NUMERIC, kaolinite_pct NUMERIC,
    other_silicates_pct NUMERIC, k_other_pct NUMERIC,
    muscovite_illite_pct    NUMERIC, ca_o_minerals_pct NUMERIC, ca_minerals_pct NUMERIC,
    fe_oxides_pct           NUMERIC, ilmenite_pct NUMERIC, ti_oxides_pct NUMERIC,
    other_oxides_pct        NUMERIC, carbonates_pct NUMERIC, apatite_pct NUMERIC,
    other_pct               NUMERIC, au_free_gold_pct NUMERIC, au_free_pct NUMERIC,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- a2: Granulométrique (HTML frontend)
CREATE TABLE IF NOT EXISTS lims_a2 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    p80_um NUMERIC, d50_um NUMERIC,
    ret_plus500_pct NUMERIC, ret_plus212_pct NUMERIC, ret_plus150_pct NUMERIC,
    ret_plus106_pct NUMERIC, ret_plus75_pct NUMERIC, ret_plus53_pct NUMERIC,
    ret_plus38_pct NUMERIC, ret_minus38_pct NUMERIC,
    au_head_g_t NUMERIC, au_plus212_g_t NUMERIC, au_plus75_g_t NUMERIC,
    au_minus38_g_t NUMERIC, au_dist_plus212_pct NUMERIC, au_dist_plus75_pct NUMERIC,
    au_dist_minus38_pct NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- a3: Libération Or (MLA)
CREATE TABLE IF NOT EXISTS lims_a3 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    p80_broyage_um NUMERIC, au_libre_pct NUMERIC, au_assoc_sulfures_pct NUMERIC,
    au_assoc_silicates_pct NUMERIC, au_assoc_oxydes_pct NUMERIC,
    au_occlus_pct NUMERIC, au_preg_rob_pct NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- c2b: GRG Progressif
CREATE TABLE IF NOT EXISTS lims_c2b (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    p80_alim_um NUMERIC, au_conc_grade_g_t NUMERIC, au_recovery_pct NUMERIC,
    cumul_recovery_pct NUMERIC, au_tail_g_t NUMERIC, mass_pull_pct NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- c2c: Table / MGS
CREATE TABLE IF NOT EXISTS lims_c2c (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    inclinaison_table_deg NUMERIC, freq_vibration_hz NUMERIC,
    debit_eau_lavage_l_min NUMERIC, densite_coupure_t_m3 NUMERIC,
    temps_residence_min NUMERIC, vitesse_mgs_rpm NUMERIC,
    au_alim_g_t NUMERIC, au_conc_g_t NUMERIC, au_tail_g_t NUMERIC,
    mass_pull_pct NUMERIC, recup_au_pct NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- dtx: Détoxification CN
CREATE TABLE IF NOT EXISTS lims_dtx (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    sample_id UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    cn_wad_mg_l NUMERIC, cn_total_mg_l NUMERIC, cn_free_mg_l NUMERIC,
    scn_mg_l NUMERIC, ph_final NUMERIC, cu_mg_l NUMERIC, fe_mg_l NUMERIC,
    ni_mg_l NUMERIC, zn_mg_l NUMERIC, as_mg_l NUMERIC, hg_ug_l NUMERIC,
    pb_mg_l NUMERIC, consomm_so2_kg_t NUMERIC, consomm_h2o2_kg_t NUMERIC,
    consomm_cuso4_kg_t NUMERIC, consomm_cao_kg_t NUMERIC,
    duree_traitement_min NUMERIC, cn_wad_rebound_24h NUMERIC,
    cn_wad_rebound_7d NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- PROCESS: Flowsheets, Design Criteria, Mass Balance, Equipment
-- =============================================================================

CREATE TABLE IF NOT EXISTS flowsheets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    blocks      JSONB DEFAULT '[]',
    connections JSONB DEFAULT '[]',
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- LEGACY: v1 design_criteria table. Kept for data migration reference.
-- New data uses circuit_criteria via design_criteria_v2.py.
CREATE TABLE IF NOT EXISTS design_criteria (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    section     TEXT,
    item        TEXT,
    design      NUMERIC,
    unit        TEXT,
    source      TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- LEGACY: v1 mass_balance_streams table. Kept for data migration reference.
-- New data uses mass_balance_streams_v2 via mass_balance_v2.py.
CREATE TABLE IF NOT EXISTS mass_balance_streams (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    stream      TEXT,
    solids_tph  NUMERIC,
    liquid_m3h  NUMERIC DEFAULT 0,
    pulp_m3h    NUMERIC DEFAULT 0,
    pct_solids  NUMERIC DEFAULT 0,
    sg          NUMERIC DEFAULT 1.0,
    au_gt       NUMERIC,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS water_balance_nodes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    node        TEXT,
    inflow      NUMERIC DEFAULT 0,
    outflow     NUMERIC DEFAULT 0,
    recycle     NUMERIC DEFAULT 0,
    loss        NUMERIC DEFAULT 0,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equipment (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    equipment_tag       TEXT,
    equipment_type      TEXT,
    power_installed_kw  NUMERIC,
    design_capacity_t_h NUMERIC,
    is_long_lead        BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    file_type   TEXT,
    phase       TEXT,
    description TEXT,
    file_path   TEXT NOT NULL,
    uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- STAGE-GATES & CHECKLISTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_gates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    stage_name      TEXT NOT NULL,
    stage_order     INTEGER NOT NULL DEFAULT 0,
    completion_pct  INTEGER NOT NULL DEFAULT 0,
    status          TEXT DEFAULT 'Not started',
    approved_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at     TIMESTAMPTZ,
    description     TEXT,
    objectives      TEXT,
    activities      TEXT,
    deliverables    TEXT,
    gate_criteria   TEXT,
    stakeholders    TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, stage_name)
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stage_id        UUID REFERENCES stage_gates(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL DEFAULT 'General',
    item_name       TEXT NOT NULL,
    is_done         BOOLEAN DEFAULT FALSE,
    proof_link      TEXT,
    status          TEXT DEFAULT 'Not started'
                    CHECK (status IN (
                        'Not started', 'In progress', 'Blocked',
                        'Ready for review', 'Approved'
                    )),
    target_pct      INTEGER DEFAULT 100,
    notes           TEXT,
    assigned_to     UUID REFERENCES users(id) ON DELETE SET NULL,
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TESTWORK: Test Campaigns
-- =============================================================================

CREATE TABLE IF NOT EXISTS test_campaigns (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    campaign_name       TEXT NOT NULL,
    description         TEXT,
    status              TEXT DEFAULT 'planned' CHECK (status IN ('planned', 'active', 'complete', 'cancelled')),
    test_type           TEXT CHECK (test_type IN (
                            'comminution', 'flotation', 'leach', 'gravity',
                            'thickening', 'filtration', 'elution', 'environmental',
                            'mineralogy', 'pilot_plant', 'other'
                        )),
    ore_types           TEXT,
    protocol            TEXT,
    laboratory          TEXT,
    start_date          DATE,
    end_date            DATE,
    cost_usd            NUMERIC,
    results_summary     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE test_campaigns ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE test_campaigns ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'planned';

CREATE TABLE IF NOT EXISTS campaign_samples (
    campaign_id          UUID REFERENCES test_campaigns(id) ON DELETE CASCADE,
    sample_id            UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    added_at             TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (campaign_id, sample_id)
);

CREATE TABLE IF NOT EXISTS geomet_domains (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    domain_code             TEXT NOT NULL,
    domain_name             TEXT NOT NULL,
    lithology               TEXT,
    alteration              TEXT,
    mineralization_style    TEXT,
    oxidation_state         TEXT,
    hardness_class          TEXT,
    variability_index       NUMERIC,
    representative          BOOLEAN DEFAULT TRUE,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, domain_code)
);

CREATE TABLE IF NOT EXISTS sample_geomet_domain (
    sample_id               UUID PRIMARY KEY REFERENCES lims_samples(id) ON DELETE CASCADE,
    domain_id               UUID REFERENCES geomet_domains(id) ON DELETE SET NULL,
    confidence_pct          NUMERIC CHECK (confidence_pct BETWEEN 0 AND 100),
    assigned_by             UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at             TIMESTAMPTZ DEFAULT NOW(),
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS geomet_composites (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    campaign_id             UUID REFERENCES test_campaigns(id) ON DELETE SET NULL,
    composite_code          TEXT NOT NULL,
    composite_name          TEXT NOT NULL,
    purpose                 TEXT NOT NULL,
    domain_id               UUID REFERENCES geomet_domains(id) ON DELETE SET NULL,
    target_mass_kg          NUMERIC,
    actual_mass_kg          NUMERIC,
    blend_method            TEXT,
    representativity_score  NUMERIC CHECK (representativity_score BETWEEN 0 AND 100),
    qa_status               TEXT DEFAULT 'draft' CHECK (qa_status IN ('draft', 'reviewed', 'approved', 'rejected')),
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, composite_code)
);

CREATE TABLE IF NOT EXISTS geomet_composite_samples (
    composite_id            UUID REFERENCES geomet_composites(id) ON DELETE CASCADE,
    sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
    mass_kg                 NUMERIC,
    weight_pct              NUMERIC CHECK (weight_pct BETWEEN 0 AND 100),
    role_in_composite       TEXT,
    PRIMARY KEY (composite_id, sample_id)
);

-- =============================================================================
-- RISKS
-- =============================================================================

CREATE TABLE IF NOT EXISTS risks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    risk_number         TEXT,
    description         TEXT NOT NULL,
    cause               TEXT,
    consequence         TEXT,
    probability         INTEGER CHECK (probability BETWEEN 1 AND 5),
    impact              INTEGER CHECK (impact BETWEEN 1 AND 5),
    criticality         INTEGER GENERATED ALWAYS AS (probability * impact) STORED,
    mitigation          TEXT,
    preventive_actions  TEXT,
    corrective_actions  TEXT,
    alert_indicators    TEXT,
    owner               TEXT,
    status              TEXT DEFAULT 'Open',
    category            TEXT CHECK (category IN (
                            'Technical', 'Metallurgical', 'HSE', 'Financial', 'Schedule',
                            'Permitting', 'Environmental', 'Geotechnical',
                            'Social', 'Process Engineering', 'Other'
                        )),
    phase               TEXT,
    due_date            DATE,
    review_date         DATE,
    stage_id            UUID REFERENCES stage_gates(id) ON DELETE SET NULL,
    is_gate_blocker     BOOLEAN DEFAULT FALSE,
    is_auto_generated   BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- COMMISSIONING
-- =============================================================================

CREATE TABLE IF NOT EXISTS commissioning_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    subsystem       TEXT,
    task_name       TEXT NOT NULL,
    prerequisite    TEXT,
    readiness_pct   INTEGER DEFAULT 0 CHECK (readiness_pct BETWEEN 0 AND 100),
    status          TEXT DEFAULT 'Pending',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS control_variables (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    tag                     TEXT NOT NULL,
    area                    TEXT,
    variable_name           TEXT NOT NULL,
    variable_role           TEXT CHECK (variable_role IN ('controlled', 'manipulated', 'disturbance', 'measured', 'constraint')),
    unit                    TEXT,
    normal_min              NUMERIC,
    normal_target           NUMERIC,
    normal_max              NUMERIC,
    critical_low            NUMERIC,
    critical_high           NUMERIC,
    measurement_source      TEXT,
    control_strategy        TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, tag)
);

CREATE TABLE IF NOT EXISTS control_alarms (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    variable_id             UUID REFERENCES control_variables(id) ON DELETE CASCADE,
    alarm_code              TEXT NOT NULL,
    priority                TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    trigger_condition       TEXT NOT NULL,
    consequence             TEXT,
    operator_action         TEXT,
    shutdown_required       BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, alarm_code)
);

CREATE TABLE IF NOT EXISTS control_interlocks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    interlock_code          TEXT NOT NULL,
    equipment_tag           TEXT,
    cause_condition         TEXT NOT NULL,
    protective_action       TEXT NOT NULL,
    reset_requirement       TEXT,
    criticality             TEXT DEFAULT 'high' CHECK (criticality IN ('medium', 'high', 'critical')),
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, interlock_code)
);

CREATE TABLE IF NOT EXISTS project_scenarios (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    scenario_name           TEXT NOT NULL,
    scenario_type           TEXT DEFAULT 'process' CHECK (scenario_type IN ('process', 'economic', 'flowsheet', 'integrated')),
    status                  TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'candidate', 'selected', 'archived')),
    version                 INTEGER DEFAULT 1,
    base_scenario_id        UUID REFERENCES project_scenarios(id) ON DELETE SET NULL,
    description             TEXT,
    assumptions             JSONB DEFAULT '{}',
    evaluation_notes        TEXT,
    created_by              UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, scenario_name, version)
);

CREATE TABLE IF NOT EXISTS operating_envelopes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    area                    TEXT,
    variable_name           TEXT NOT NULL,
    variable_tag            TEXT,
    unit                    TEXT,
    val_min                 NUMERIC,
    val_target              NUMERIC,
    val_max                 NUMERIC,
    alarm_low               NUMERIC,
    alarm_high              NUMERIC,
    technical_justification TEXT,
    regulatory_reference    TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, area, variable_name)
);

CREATE TABLE IF NOT EXISTS project_env_compliance (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
    parameter               TEXT NOT NULL,
    category                TEXT CHECK (category IN ('effluent', 'air', 'tailings', 'water', 'waste', 'other')),
    regulatory_limit        NUMERIC,
    unit                    TEXT,
    design_target           NUMERIC,
    current_value           NUMERIC,
    compliant               BOOLEAN,
    regulatory_reference    TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, parameter)
);

CREATE TABLE IF NOT EXISTS scenario_simulation_params (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id             UUID REFERENCES project_scenarios(id) ON DELETE CASCADE,
    category                TEXT NOT NULL,
    param_key               TEXT NOT NULL,
    param_value             NUMERIC,
    param_value_text        TEXT,
    source                  TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scenario_id, category, param_key)
);

CREATE TABLE IF NOT EXISTS scenario_flowsheets (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id             UUID REFERENCES project_scenarios(id) ON DELETE CASCADE,
    blocks                  JSONB DEFAULT '[]',
    connections             JSONB DEFAULT '[]',
    source_flowsheet_id     UUID REFERENCES flowsheets(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scenario_id)
);

CREATE TABLE IF NOT EXISTS scenario_evaluations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id             UUID REFERENCES project_scenarios(id) ON DELETE CASCADE,
    recovery_pct            NUMERIC,
    energy_kwh_t            NUMERIC,
    capex_usd               NUMERIC,
    opex_usd_t              NUMERIC,
    geomet_confidence       NUMERIC,
    automation_readiness    NUMERIC,
    environmental_score     NUMERIC,
    safety_score            NUMERIC,
    economic_score          NUMERIC,
    overall_score           NUMERIC,
    results_json            JSONB DEFAULT '{}',
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scenario_id)
);

-- =============================================================================
-- NEW TABLES: Cost Models, CAPEX/OPEX
-- =============================================================================

CREATE TABLE IF NOT EXISTS cost_models (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    model_type  TEXT NOT NULL CHECK (model_type IN ('CAPEX', 'OPEX')),
    version     INTEGER DEFAULT 1,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cost_line_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        UUID REFERENCES cost_models(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    description     TEXT,
    quantity        NUMERIC DEFAULT 1,
    unit            TEXT,
    unit_cost_usd   NUMERIC DEFAULT 0,
    total_cost_usd  NUMERIC GENERATED ALWAYS AS (quantity * unit_cost_usd) STORED,
    source          TEXT,
    wbs_code        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assumptions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    model_id    UUID REFERENCES cost_models(id) ON DELETE SET NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    unit        TEXT,
    version     INTEGER DEFAULT 1,
    changed_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    changed_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vendor_quotes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    supplier            TEXT NOT NULL,
    item_description    TEXT,
    quoted_price_usd    NUMERIC,
    currency            TEXT DEFAULT 'USD',
    validity_date       DATE,
    attachment_path     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mto_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    item            TEXT NOT NULL,
    material        TEXT,
    quantity        NUMERIC,
    unit            TEXT,
    unit_weight_kg  NUMERIC,
    source          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NEW TABLES: Decisions Log
-- =============================================================================

CREATE TABLE IF NOT EXISTS decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    stage_id        UUID REFERENCES stage_gates(id) ON DELETE SET NULL,
    date            DATE DEFAULT CURRENT_DATE,
    decision_text   TEXT NOT NULL,
    justification   TEXT,
    attachment_path TEXT,
    decided_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NEW TABLES: Reviews & Approvals
-- =============================================================================

CREATE TABLE IF NOT EXISTS reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    stage_id        UUID REFERENCES stage_gates(id) ON DELETE SET NULL,
    reviewer_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    review_type     TEXT DEFAULT 'internal',
    status          TEXT DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'in_review', 'correction_requested',
                        'approved', 'rejected'
                    )),
    is_independent  BOOLEAN DEFAULT FALSE,
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS review_comments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID REFERENCES reviews(id) ON DELETE CASCADE,
    author_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    comment_text TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approvals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID REFERENCES reviews(id) ON DELETE CASCADE,
    approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    status      TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    comment     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NEW TABLES: Ramp-up & Working Capital
-- =============================================================================

CREATE TABLE IF NOT EXISTS rampup_periods (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    quarter             INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    year                INTEGER NOT NULL,
    availability_pct    NUMERIC,
    recovery_pct        NUMERIC,
    product_quality_pct NUMERIC,
    throughput_factor   NUMERIC,
    notes               TEXT,
    UNIQUE(project_id, quarter, year)
);

CREATE TABLE IF NOT EXISTS working_capital (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    description     TEXT,
    amount_usd      NUMERIC,
    timing_months   NUMERIC,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NEW TABLES: Audit Trail
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   UUID,
    old_value   JSONB,
    new_value   JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NEW TABLES: Deliverables & Attachments
-- =============================================================================

CREATE TABLE IF NOT EXISTS deliverables (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_item_id   UUID REFERENCES checklist_items(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    document_path       TEXT,
    notes               TEXT,
    status              TEXT DEFAULT 'draft',
    uploaded_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attachments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL,
    entity_id   UUID NOT NULL,
    filename    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- NI 43-101 REPORT SECTIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS ni43101_sections (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID REFERENCES projects(id) ON DELETE CASCADE,
    section_number    INTEGER NOT NULL CHECK (section_number BETWEEN 1 AND 27),
    subsection_key    TEXT NOT NULL,
    title_fr          TEXT NOT NULL DEFAULT '',
    title_en          TEXT NOT NULL DEFAULT '',
    content_fr        TEXT NOT NULL DEFAULT '',
    content_en        TEXT NOT NULL DEFAULT '',
    sort_order        INTEGER NOT NULL DEFAULT 0,
    is_auto_generated BOOLEAN DEFAULT TRUE,
    source_data       JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- INDEXES for performance
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_lims_samples_project ON lims_samples(project_id);
-- LIMS sub-tables: indexes on project_id for efficient per-project queries
CREATE INDEX IF NOT EXISTS idx_lims_a1_project ON lims_a1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_b1_project ON lims_b1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_c2_project ON lims_c2(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_c3_project ON lims_c3(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_d1_project ON lims_d1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_e1_project ON lims_e1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_e2_project ON lims_e2(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_flotation_project ON lims_flotation(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_elution_project ON lims_elution(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_environmental_project ON lims_environmental(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_kinetics_project ON lims_kinetics(project_id);
-- Process tables
CREATE INDEX IF NOT EXISTS idx_flowsheets_project ON flowsheets(project_id);
CREATE INDEX IF NOT EXISTS idx_design_criteria_project ON design_criteria(project_id);
CREATE INDEX IF NOT EXISTS idx_mass_balance_project ON mass_balance_streams(project_id);
CREATE INDEX IF NOT EXISTS idx_water_balance_project ON water_balance_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_equipment_project ON equipment(project_id);
CREATE INDEX IF NOT EXISTS idx_project_reports_project ON project_reports(project_id);
-- Stage-gates & checklists
CREATE INDEX IF NOT EXISTS idx_stage_gates_project ON stage_gates(project_id);
CREATE INDEX IF NOT EXISTS idx_checklist_items_stage ON checklist_items(stage_id);
-- Testwork & Commissioning
CREATE INDEX IF NOT EXISTS idx_test_campaigns_project ON test_campaigns(project_id);
CREATE INDEX IF NOT EXISTS idx_commissioning_project ON commissioning_tasks(project_id);
-- Risks
CREATE INDEX IF NOT EXISTS idx_risks_project ON risks(project_id);
CREATE INDEX IF NOT EXISTS idx_risks_stage ON risks(stage_id);
-- Cost models
CREATE INDEX IF NOT EXISTS idx_cost_models_project ON cost_models(project_id);
CREATE INDEX IF NOT EXISTS idx_cost_line_items_model ON cost_line_items(model_id);
-- Vendor & MTO
CREATE INDEX IF NOT EXISTS idx_vendor_quotes_project ON vendor_quotes(project_id);
CREATE INDEX IF NOT EXISTS idx_mto_items_project ON mto_items(project_id);
-- Decisions, Reviews, Audit
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);
CREATE INDEX IF NOT EXISTS idx_reviews_project ON reviews(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
-- Ramp-up, Working Capital
CREATE INDEX IF NOT EXISTS idx_rampup_project ON rampup_periods(project_id);
CREATE INDEX IF NOT EXISTS idx_working_capital_project ON working_capital(project_id);
-- Attachments & Deliverables
CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_deliverables_checklist ON deliverables(checklist_item_id);
-- NI 43-101
CREATE INDEX IF NOT EXISTS idx_ni43101_sections_project ON ni43101_sections(project_id);
CREATE INDEX IF NOT EXISTS idx_ni43101_sections_order ON ni43101_sections(project_id, section_number, sort_order);

-- =============================================================================
-- SIMULATION PARAMETERS (user-editable per project)
-- =============================================================================

CREATE TABLE IF NOT EXISTS simulation_params (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,       -- e.g. 'process', 'financier', 'optimisation', 'kpi_cible'
    param_key       TEXT NOT NULL,       -- e.g. 'bwi', 'p80_target'
    param_label     TEXT NOT NULL,
    param_value     NUMERIC,
    param_value_text TEXT,               -- for non-numeric values
    unit            TEXT,
    source          TEXT DEFAULT 'Utilisateur',
    notes           TEXT,
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, category, param_key)
);

CREATE INDEX IF NOT EXISTS idx_sim_params_project ON simulation_params(project_id);

CREATE TABLE IF NOT EXISTS block_model_params (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    param_name      TEXT NOT NULL,
    param_value     TEXT DEFAULT '',
    unit            TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, category, param_name)
);

CREATE INDEX IF NOT EXISTS idx_block_model_params_project ON block_model_params(project_id);

-- =============================================================================
-- BLOCK MODEL MODULE (Geological & Metallurgical Block Model)
-- =============================================================================

CREATE TABLE IF NOT EXISTS block_model_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    x_origin NUMERIC DEFAULT 0,
    y_origin NUMERIC DEFAULT 0,
    z_origin NUMERIC DEFAULT 0,
    x_block_size NUMERIC DEFAULT 10,
    y_block_size NUMERIC DEFAULT 10,
    z_block_size NUMERIC DEFAULT 5,
    rotation_angle NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id UUID REFERENCES block_model_configs(id) ON DELETE CASCADE,
    i_index INTEGER, 
    j_index INTEGER, 
    k_index INTEGER,
    x_center NUMERIC, 
    y_center NUMERIC, 
    z_center NUMERIC,
    density NUMERIC,
    volume NUMERIC,
    tonnage NUMERIC GENERATED ALWAYS AS (volume * density) STORED,
    grade_au NUMERIC,
    rock_type TEXT,
    attributes JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blocks_config ON blocks(config_id);
CREATE INDEX IF NOT EXISTS idx_blocks_grade ON blocks(config_id, grade_au);
CREATE INDEX IF NOT EXISTS idx_blocks_z ON blocks(config_id, z_center);

-- =============================================================================
-- DESIGN CRITERIA v2: Circuit Templates & Criteria
-- =============================================================================

CREATE TABLE IF NOT EXISTS unit_operations_catalog (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    op_code          TEXT UNIQUE NOT NULL,
    category         TEXT NOT NULL,
    label            TEXT NOT NULL,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    dependencies     JSONB DEFAULT '[]'::jsonb,
    lims_triggers    JSONB DEFAULT '{}'::jsonb,
    default_criteria JSONB DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_uoc_category ON unit_operations_catalog(category);
CREATE INDEX IF NOT EXISTS idx_uoc_sort_order ON unit_operations_catalog(sort_order);

CREATE TABLE IF NOT EXISTS circuit_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT 'Circuit principal',
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ct_project ON circuit_templates(project_id);
ALTER TABLE IF EXISTS circuit_templates ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE IF EXISTS circuit_templates ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id);
ALTER TABLE IF EXISTS circuit_templates ADD COLUMN IF NOT EXISTS description TEXT;
UPDATE circuit_templates SET is_active = TRUE WHERE is_active IS NULL;

CREATE TABLE IF NOT EXISTS circuit_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    op_code TEXT NOT NULL REFERENCES unit_operations_catalog(op_code),
    enabled BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(template_id, op_code)
);
CREATE INDEX IF NOT EXISTS idx_co_template ON circuit_operations(template_id);
CREATE INDEX IF NOT EXISTS idx_co_opcode ON circuit_operations(template_id, op_code);

CREATE TABLE IF NOT EXISTS circuit_template_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    op_code TEXT NOT NULL,
    instance_label TEXT DEFAULT '',
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cto_template ON circuit_template_operations(template_id);

CREATE TABLE IF NOT EXISTS circuit_criteria (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    operation_id UUID REFERENCES circuit_template_operations(id) ON DELETE CASCADE,
    op_code TEXT NOT NULL,
    ref TEXT,
    item TEXT NOT NULL,
    unit TEXT,
    typ_min NUMERIC,
    typ_max NUMERIC,
    design_value NUMERIC,
    design_value_text TEXT,
    nominal_value NUMERIC,
    nominal_value_text TEXT,
    min_value NUMERIC,
    max_value NUMERIC,
    source_code TEXT,
    revision TEXT,
    author TEXT,
    comments TEXT,
    cascade_key TEXT,
    is_text_field BOOLEAN DEFAULT FALSE,
    sub_section TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    version INT DEFAULT 1,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cc_template ON circuit_criteria(template_id);
CREATE INDEX IF NOT EXISTS idx_cc_opcode ON circuit_criteria(template_id, op_code);

CREATE TABLE IF NOT EXISTS design_criteria_v2 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    op_code TEXT NOT NULL REFERENCES unit_operations_catalog(op_code),
    ref_number TEXT NOT NULL,
    section_title TEXT,
    item TEXT NOT NULL,
    unit TEXT,
    design_value NUMERIC,
    nominal_value NUMERIC,
    min_value NUMERIC,
    max_value NUMERIC,
    source_code TEXT DEFAULT 'X',
    revision TEXT DEFAULT 'A',
    author TEXT,
    comments TEXT,
    lims_value NUMERIC,
    industry_default NUMERIC,
    enabled BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    formula TEXT,
    dag_key TEXT,
    value_kind TEXT DEFAULT 'number',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dc_v2_project ON design_criteria_v2(project_id);
CREATE INDEX IF NOT EXISTS idx_dc_v2_template ON design_criteria_v2(template_id);
CREATE INDEX IF NOT EXISTS idx_dc_v2_opcode ON design_criteria_v2(template_id, op_code);

-- Circuit compilations: snapshot of flowsheet -> circuit_template compilation (v3).
CREATE TABLE IF NOT EXISTS circuit_compilations (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type        TEXT NOT NULL CHECK (source_type IN ('flowsheet','scenario_flowsheet','custom')),
    source_id          UUID,
    template_id        UUID NOT NULL REFERENCES circuit_templates(id),
    blocks_hash        TEXT NOT NULL,
    sections_resolved  JSONB DEFAULT '[]'::jsonb,
    branches_detected  JSONB DEFAULT '[]'::jsonb,
    topo_order         JSONB DEFAULT '[]'::jsonb,
    compile_warnings   JSONB DEFAULT '[]'::jsonb,
    created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_compilations_project ON circuit_compilations(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compilations_hash ON circuit_compilations(project_id, blocks_hash);

-- =============================================================================
-- MIGRATIONS: Add columns to project_reports
-- =============================================================================
ALTER TABLE project_reports ADD COLUMN IF NOT EXISTS title       TEXT;
ALTER TABLE project_reports ADD COLUMN IF NOT EXISTS report_type TEXT DEFAULT 'interne' CHECK (report_type IN ('interne','externe'));
ALTER TABLE project_reports ADD COLUMN IF NOT EXISTS file_size   BIGINT;
ALTER TABLE project_reports ADD COLUMN IF NOT EXISTS author      TEXT;
CREATE INDEX IF NOT EXISTS idx_project_reports_project ON project_reports(project_id, phase);

-- =============================================================================
-- AUDIT FIX: NOT NULL constraints on project_id foreign keys
-- =============================================================================
DO $$ BEGIN
  ALTER TABLE lims_samples      ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_a1           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_b1           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_c2           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_c3           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_d1           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_e1           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_e2           ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_flotation    ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_elution      ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_environmental ALTER COLUMN project_id SET NOT NULL;
  ALTER TABLE lims_kinetics     ALTER COLUMN project_id SET NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- =============================================================================
-- AUDIT FIX: CHECK constraints on cost_line_items
-- =============================================================================
ALTER TABLE cost_line_items DROP CONSTRAINT IF EXISTS chk_quantity_positive;
ALTER TABLE cost_line_items ADD CONSTRAINT chk_quantity_positive CHECK (quantity > 0);
ALTER TABLE cost_line_items DROP CONSTRAINT IF EXISTS chk_unit_cost_non_negative;
ALTER TABLE cost_line_items ADD CONSTRAINT chk_unit_cost_non_negative CHECK (unit_cost_usd >= 0);

-- =============================================================================
-- AUDIT FIX: Missing indexes on LIMS test result tables
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_lims_a1_project      ON lims_a1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_a1_sample       ON lims_a1(sample_id);
CREATE INDEX IF NOT EXISTS idx_lims_b1_project      ON lims_b1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_b1_sample       ON lims_b1(sample_id);
CREATE INDEX IF NOT EXISTS idx_lims_c2_project      ON lims_c2(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_c3_project      ON lims_c3(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_d1_project      ON lims_d1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_e1_project      ON lims_e1(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_e2_project      ON lims_e2(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_flotation_project ON lims_flotation(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_elution_project ON lims_elution(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_environmental_project ON lims_environmental(project_id);
CREATE INDEX IF NOT EXISTS idx_lims_kinetics_project ON lims_kinetics(project_id);

-- Composite indexes for workflow status filtering
CREATE INDEX IF NOT EXISTS idx_stage_gates_project_status ON stage_gates(project_id, status);
CREATE INDEX IF NOT EXISTS idx_checklist_items_status ON checklist_items(stage_id, status);
CREATE INDEX IF NOT EXISTS idx_cost_line_items_project ON cost_line_items(model_id, category);

-- =============================================================================
-- AUDIT FIX: Missing tables referenced by route modules
-- =============================================================================

-- Analytics: Process tags & readings (SCADA/DCS integration)
CREATE TABLE IF NOT EXISTS process_tags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tag_name        TEXT NOT NULL,
    description     TEXT,
    area            TEXT,
    unit            TEXT DEFAULT '',
    data_type       TEXT DEFAULT 'float',
    normal_min      NUMERIC,
    normal_target   NUMERIC,
    normal_max      NUMERIC,
    source          TEXT DEFAULT 'manual',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_process_tags_project ON process_tags(project_id);

CREATE TABLE IF NOT EXISTS tag_readings (
    time            TIMESTAMPTZ NOT NULL,
    tag_id          UUID NOT NULL REFERENCES process_tags(id) ON DELETE CASCADE,
    value           NUMERIC NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tag_readings_tag_time ON tag_readings(tag_id, time DESC);

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    period          TEXT NOT NULL DEFAULT 'shift',
    kpi_data        JSONB DEFAULT '{}',
    snapshot_time   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_project ON kpi_snapshots(project_id, snapshot_time DESC);

CREATE TABLE IF NOT EXISTS data_connectors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    protocol        TEXT,
    config          JSONB DEFAULT '{}',
    poll_interval_s INTEGER DEFAULT 60,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Geochemistry: ABA/NAG results & ARD classification
CREATE TABLE IF NOT EXISTS aba_nag_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sample_id           UUID REFERENCES lims_samples(id) ON DELETE SET NULL,
    total_s_pct         NUMERIC,
    sulfide_s_pct       NUMERIC,
    sulfate_s_pct       NUMERIC,
    ap_kg_caco3_t       NUMERIC,
    np_kg_caco3_t       NUMERIC,
    nnp                 NUMERIC,
    npr                 NUMERIC,
    ph_nag              NUMERIC,
    pag_classification  TEXT,
    test_date           DATE,
    laboratory          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aba_nag_project ON aba_nag_results(project_id);

CREATE TABLE IF NOT EXISTS ard_classifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    domain_code     TEXT NOT NULL DEFAULT 'site',
    pag_count       INTEGER DEFAULT 0,
    non_pag_count   INTEGER DEFAULT 0,
    uncertain_count INTEGER DEFAULT 0,
    pag_pct         NUMERIC,
    ard_risk_level  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, domain_code)
);

-- Geotech: test results, slope stability, TSF design
CREATE TABLE IF NOT EXISTS geotech_tests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sample_id       UUID REFERENCES lims_samples(id) ON DELETE SET NULL,
    test_code       TEXT NOT NULL,
    results         JSONB DEFAULT '{}',
    laboratory      TEXT,
    test_date       DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_geotech_tests_project ON geotech_tests(project_id);

CREATE TABLE IF NOT EXISTS slope_analyses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    location            TEXT,
    slope_angle_deg     NUMERIC,
    slope_height_m      NUMERIC,
    cohesion_kpa        NUMERIC,
    friction_angle_deg  NUMERIC,
    gamma_kn_m3         NUMERIC,
    pore_pressure_ratio NUMERIC,
    method              TEXT DEFAULT 'Bishop',
    fs_static           NUMERIC,
    fs_seismic          NUMERIC,
    is_compliant        BOOLEAN DEFAULT false,
    failure_surface     JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_slope_analyses_project ON slope_analyses(project_id);

CREATE TABLE IF NOT EXISTS tsf_design (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version             INTEGER DEFAULT 1,
    construction_method TEXT,
    total_capacity_m3   NUMERIC,
    annual_deposition_t NUMERIC,
    raise_height_m      NUMERIC,
    embankment_area_ha  NUMERIC,
    fs_static           NUMERIC,
    fs_seismic          NUMERIC,
    is_mac_compliant    BOOLEAN DEFAULT false,
    water_balance       JSONB DEFAULT '{}',
    notes               TEXT,
    gistm_basis_id      UUID,
    consequence_class_at_design TEXT
        CHECK (consequence_class_at_design IS NULL OR consequence_class_at_design
               IN ('low','significant','high','very_high','extreme')),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- GISTM tailings: design basis (versioned), violations, owner-signed overrides
CREATE TABLE IF NOT EXISTS gistm_design_basis (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','active','superseded')),
    par_count                INTEGER NOT NULL CHECK (par_count >= 0),
    env_damage_class         TEXT NOT NULL
                    CHECK (env_damage_class IN ('none','minor','moderate','major','catastrophic')),
    economic_damage_usd_m    NUMERIC NOT NULL CHECK (economic_damage_usd_m >= 0),
    critical_infra_downstream BOOLEAN NOT NULL DEFAULT false,
    consequence_class        TEXT NOT NULL
                    CHECK (consequence_class IN ('low','significant','high','very_high','extreme')),
    idf_return_period_yr     INTEGER NOT NULL CHECK (idf_return_period_yr > 0),
    mde_return_period_yr     INTEGER NOT NULL CHECK (mde_return_period_yr > 0),
    fs_static_min            NUMERIC NOT NULL CHECK (fs_static_min > 0),
    fs_seismic_min           NUMERIC NOT NULL CHECK (fs_seismic_min > 0),
    fs_post_liquefaction_min NUMERIC NOT NULL CHECK (fs_post_liquefaction_min > 0),
    allowed_construction_methods TEXT[] NOT NULL,
    pga_threshold_g          NUMERIC,
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_by    UUID REFERENCES users(id),
    activated_at    TIMESTAMPTZ,
    notes           TEXT,
    UNIQUE (project_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gistm_basis_one_active
    ON gistm_design_basis(project_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS gistm_violations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    basis_id        UUID NOT NULL REFERENCES gistm_design_basis(id),
    tsf_design_id   UUID NOT NULL REFERENCES tsf_design(id) ON DELETE CASCADE,
    rule_code       TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('error','warning')),
    observed_value  JSONB NOT NULL,
    required_value  JSONB NOT NULL,
    message         TEXT NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gistm_violations_tsf ON gistm_violations(tsf_design_id);
CREATE INDEX IF NOT EXISTS idx_gistm_violations_basis ON gistm_violations(basis_id);

CREATE TABLE IF NOT EXISTS gistm_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    violation_id    UUID NOT NULL UNIQUE REFERENCES gistm_violations(id) ON DELETE CASCADE,
    justification   TEXT NOT NULL CHECK (length(justification) >= 50),
    signed_by       UUID NOT NULL REFERENCES users(id),
    signed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tie tsf_design.gistm_basis_id back to gistm_design_basis (deferred FK declaration
-- because tsf_design is created before gistm_design_basis in this file).
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'tsf_design_gistm_basis_id_fkey'
    ) THEN
        ALTER TABLE tsf_design
            ADD CONSTRAINT tsf_design_gistm_basis_id_fkey
            FOREIGN KEY (gistm_basis_id) REFERENCES gistm_design_basis(id);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_tsf_design_basis ON tsf_design(gistm_basis_id);

-- Economics: DCF models, indicators, Monte Carlo
CREATE TABLE IF NOT EXISTS dcf_models (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version         INTEGER DEFAULT 1,
    discount_rate   NUMERIC,
    tax_rate        NUMERIC,
    mine_life_years INTEGER,
    cashflows       JSONB DEFAULT '[]',
    npv             NUMERIC,
    irr             NUMERIC,
    payback_years   NUMERIC,
    aisc            NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dcf_models_project ON dcf_models(project_id);

CREATE TABLE IF NOT EXISTS economic_indicators (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    dcf_model_id    UUID REFERENCES dcf_models(id) ON DELETE CASCADE,
    npv_usd         NUMERIC,
    irr_pct         NUMERIC,
    aisc_usd_oz     NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monte_carlo_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    n_iterations    INTEGER DEFAULT 10000,
    variables       JSONB DEFAULT '{}',
    status          TEXT DEFAULT 'queued',
    results         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Simulation: run history
CREATE TABLE IF NOT EXISTS simulation_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type            TEXT DEFAULT 'rigorous',
    status          TEXT DEFAULT 'queued',
    params          JSONB DEFAULT '{}',
    results         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_project ON simulation_runs(project_id);

CREATE TABLE IF NOT EXISTS simulation_runs_v2 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id     UUID,
    run_type        TEXT DEFAULT 'rigorous',
    params          JSONB,
    results         JSONB,
    created_by      UUID,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    run_mode        TEXT DEFAULT 'global',
    ops_simulated   TEXT[],
    feed_source     TEXT,
    feed_stream     JSONB,
    product_stream  JSONB,
    suggestion_id   TEXT,
    label           TEXT,
    status          TEXT DEFAULT 'queued'  -- FIX: colonne manquante vs migration 000060
);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_v2_project ON simulation_runs_v2(project_id);

CREATE TABLE IF NOT EXISTS scenario_suggestions_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    suggestion_id   TEXT NOT NULL,
    title           TEXT NOT NULL,
    category        TEXT,
    confidence      TEXT,
    reasoning       TEXT,
    lims_basis      JSONB,
    history_basis   JSONB,
    ops_to_add      TEXT[],
    ops_to_remove   TEXT[],
    params_override JSONB,
    estimated_impact JSONB,
    status          TEXT DEFAULT 'proposed',
    accepted_at     TIMESTAMPTZ,
    run_id          UUID REFERENCES simulation_runs_v2(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_suggestions_project ON scenario_suggestions_log(project_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON scenario_suggestions_log(project_id, status);

-- =============================================================================
-- PARAMETER SYSTEM: Hierarchical parameter resolution
-- =============================================================================

CREATE TABLE IF NOT EXISTS parameter_registry (
    key              TEXT PRIMARY KEY,
    category         TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    unit             TEXT,
    value_type       TEXT NOT NULL DEFAULT 'numeric',
    min_value        NUMERIC,
    max_value        NUMERIC,
    default_value    NUMERIC,
    default_value_text TEXT,
    ni43101_stage    TEXT,
    source_reference TEXT,
    description      TEXT
);

CREATE TABLE IF NOT EXISTS project_params (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    param_key       TEXT NOT NULL REFERENCES parameter_registry(key),
    value           NUMERIC,
    value_text      TEXT,
    source          TEXT NOT NULL,
    source_detail   TEXT,
    set_by          UUID REFERENCES users(id),
    set_at          TIMESTAMPTZ DEFAULT NOW(),
    version         INT NOT NULL DEFAULT 1,
    UNIQUE(project_id, param_key, version)
);

CREATE INDEX IF NOT EXISTS idx_project_params_lookup
    ON project_params(project_id, param_key, version DESC);

-- =============================================================================
-- TRACEABILITY: Design criteria versioning
-- =============================================================================

ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS version        INT DEFAULT 1;
ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS changed_by     UUID REFERENCES users(id);
ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS changed_at     TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS change_reason  TEXT;
ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS previous_value NUMERIC;

CREATE INDEX IF NOT EXISTS idx_dc_current
    ON design_criteria(project_id, section, item, version DESC);

CREATE OR REPLACE VIEW design_criteria_current AS
SELECT DISTINCT ON (project_id, section, item) *
FROM design_criteria
ORDER BY project_id, section, item, version DESC;

-- =============================================================================
-- TRACEABILITY: LIMS import audit log
-- =============================================================================

CREATE TABLE IF NOT EXISTS lims_import_log (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES users(id),
    import_type      TEXT NOT NULL,
    filename         TEXT,
    test_type        TEXT NOT NULL,
    samples_count    INT NOT NULL,
    accepted_count   INT NOT NULL,
    rejected_count   INT NOT NULL DEFAULT 0,
    rejected_details JSONB,
    checksum_sha256  CHAR(64),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_import_log_project
    ON lims_import_log(project_id, created_at DESC);

-- =============================================================================
-- TRACEABILITY: Parameter provenance on result tables
-- =============================================================================

ALTER TABLE scenario_evaluations ADD COLUMN IF NOT EXISTS param_sources JSONB;
ALTER TABLE mass_balance_streams ADD COLUMN IF NOT EXISTS param_sources JSONB;

-- =============================================================================
-- INTELLIGENCE: LIMS alerts
-- =============================================================================

CREATE TABLE IF NOT EXISTS lims_alerts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    alert_type       TEXT NOT NULL,
    severity         TEXT NOT NULL,
    test_type        TEXT NOT NULL,
    sample_ids       UUID[],
    message          TEXT NOT NULL,
    is_acknowledged  BOOLEAN DEFAULT FALSE,
    acknowledged_by  UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lims_alerts_active
    ON lims_alerts(project_id, is_acknowledged)
    WHERE NOT is_acknowledged;

-- =============================================================================
-- SMART DC PIPELINE: Snapshots & Manual Override Preservation
-- =============================================================================

CREATE TABLE IF NOT EXISTS dc_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    previous_snapshot_id UUID REFERENCES dc_snapshots(id),
    template_id         UUID REFERENCES circuit_templates(id) ON DELETE CASCADE,
    version_label       TEXT NOT NULL,
    ni43101_stage       TEXT,
    notes               TEXT,
    snapshot_data       JSONB,
    data                JSONB,
    frozen_by           UUID REFERENCES users(id),
    frozen_at           TIMESTAMPTZ DEFAULT NOW(),
    checksum_sha256     CHAR(64),
    UNIQUE(project_id, version_label)
);

CREATE INDEX IF NOT EXISTS idx_dc_snapshots_project
    ON dc_snapshots(project_id, frozen_at DESC);
CREATE INDEX IF NOT EXISTS idx_dcs_project ON dc_snapshots(project_id);

CREATE TABLE IF NOT EXISTS dc_manual_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id     UUID NOT NULL,
    ref_number      TEXT NOT NULL,
    design_value    NUMERIC,
    source_code     TEXT,
    comments        TEXT,
    saved_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, template_id, ref_number)
);

-- Pipeline step status tracking
ALTER TABLE circuit_templates ADD COLUMN IF NOT EXISTS pipeline_state JSONB DEFAULT '{}';

-- ── Mass Balance v2 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mass_balance_streams_v2 (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id     UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    operation_id    UUID REFERENCES circuit_template_operations(id) ON DELETE SET NULL,
    section         TEXT NOT NULL,
    stream_name     TEXT NOT NULL,
    stream_type     TEXT NOT NULL DEFAULT 'product',
    hours_per_day   NUMERIC,
    solids_tpd      NUMERIC,
    solids_tph      NUMERIC,
    solids_m3h      NUMERIC,
    solids_sg       NUMERIC,
    water_tpd       NUMERIC,
    water_tph       NUMERIC,
    water_m3h       NUMERIC,
    water_sg        NUMERIC DEFAULT 1.0,
    slurry_tpd      NUMERIC,
    slurry_tph      NUMERIC,
    slurry_m3h      NUMERIC,
    slurry_pct_solids NUMERIC,
    slurry_sg       NUMERIC,
    process_water_m3h   NUMERIC DEFAULT 0,
    fresh_water_m3h     NUMERIC DEFAULT 0,
    reclaim_water_m3h   NUMERIC DEFAULT 0,
    gland_water_m3h     NUMERIC DEFAULT 0,
    extras          JSONB DEFAULT '{}',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mb_v2_project ON mass_balance_streams_v2(project_id);
CREATE INDEX IF NOT EXISTS idx_mb_v2_template ON mass_balance_streams_v2(template_id);

CREATE TABLE IF NOT EXISTS mass_balance_snapshots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
    name        TEXT,
    checksum    TEXT,
    stream_data JSONB,
    water_summary JSONB,
    production_summary JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mbs_project ON mass_balance_snapshots(project_id);

-- ── Cascade staleness tracking ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS project_staleness (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    module      TEXT NOT NULL,
    is_stale    BOOLEAN DEFAULT FALSE,
    stale_since TIMESTAMPTZ,
    reason      TEXT,
    UNIQUE(project_id, module)
);

-- =============================================================================
-- OPTIMIZATION & COMPARISON (v3)
-- =============================================================================

-- Optimization jobs: parameter sweep / NSGA-II runs against a compilation.
CREATE TABLE IF NOT EXISTS optimization_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    compilation_id  UUID REFERENCES circuit_compilations(id),
    mode            TEXT NOT NULL CHECK (mode IN ('sweep','nsga2')),
    objective       TEXT,
    objectives      JSONB DEFAULT '[]'::jsonb,
    variables       JSONB DEFAULT '[]'::jsonb,
    constraints     JSONB DEFAULT '[]'::jsonb,
    status          TEXT DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
    result          JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_opt_jobs_project ON optimization_jobs(project_id);

-- Simulation comparison sets: named groups of simulation runs for side-by-side comparison.
CREATE TABLE IF NOT EXISTS simulation_comparison_sets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    run_ids     UUID[] NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cmp_sets_project ON simulation_comparison_sets(project_id);

-- Project feature flags (SIM_V3_UI, etc.)
ALTER TABLE projects ADD COLUMN IF NOT EXISTS feature_flags JSONB DEFAULT '{}'::jsonb;

-- Link simulation runs back to the compilation snapshot that produced them.
ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS compilation_id UUID REFERENCES circuit_compilations(id);
CREATE INDEX IF NOT EXISTS idx_runs_v2_compilation ON simulation_runs_v2(compilation_id);
