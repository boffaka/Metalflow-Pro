"""Complete LIMS schema — all 11 metallurgical test modules.

Aligns database with the Gold Metallurgical Test Template v2.0:
SAM-00 through ENV-10.

Creates missing tables, adds missing columns to existing tables.
"""
revision = "000029"
down_revision = "000028"
revises = "000028"

from alembic import op


def upgrade():
    op.execute("""

    -- ═══════════════════════════════════════════════════════════════
    -- SAM-00: ÉCHANTILLONNAGE — enrich lims_samples
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS source_horizon VARCHAR(200);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS depth_interval VARCHAR(100);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS total_mass_kg FLOAT;
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS sent_mass_kg FLOAT;
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS collection_date DATE;
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS reception_date DATE;
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS collection_method VARCHAR(200);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS qaqc_protocol VARCHAR(100);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS crm_standard VARCHAR(100);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS duplicate_freq VARCHAR(20);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS blank_freq VARCHAR(20);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS packaging VARCHAR(100);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'Reçu';
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS oxidation_state VARCHAR(50);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS domain VARCHAR(100);
    ALTER TABLE lims_samples ADD COLUMN IF NOT EXISTS observations TEXT;

    -- ═══════════════════════════════════════════════════════════════
    -- MIN-01: MINÉRALOGIE — enrich lims_a1 + create lims_mineralogy
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS hg_ppm FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS sio2_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS al2o3_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS cao_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS mgo_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS na2o_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS k2o_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS tio2_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS mno_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS loi_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS c_total_pct FLOAT;
    ALTER TABLE lims_a1 ADD COLUMN IF NOT EXISTS s_sulfate_pct FLOAT;

    CREATE TABLE IF NOT EXISTS lims_a2 (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        p80_um FLOAT,
        d50_um FLOAT,
        ret_plus500_pct FLOAT,
        ret_plus212_pct FLOAT,
        ret_plus150_pct FLOAT,
        ret_plus106_pct FLOAT,
        ret_plus75_pct FLOAT,
        ret_plus53_pct FLOAT,
        ret_plus38_pct FLOAT,
        ret_minus38_pct FLOAT,
        au_head_g_t FLOAT,
        au_plus212_g_t FLOAT,
        au_plus75_g_t FLOAT,
        au_minus38_g_t FLOAT,
        au_dist_plus212_pct FLOAT,
        au_dist_plus75_pct FLOAT,
        au_dist_minus38_pct FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS lims_a3 (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        p80_broyage_um FLOAT,
        au_libre_pct FLOAT,
        au_assoc_sulfures_pct FLOAT,
        au_assoc_silicates_pct FLOAT,
        au_assoc_oxydes_pct FLOAT,
        au_occlus_pct FLOAT,
        au_pregrob_pct FLOAT,
        recup_cil_pred_pct FLOAT,
        recup_grav_pred_pct FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS lims_m1 (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        or_natif_pct FLOAT,
        electrum_pct FLOAT,
        pyrite_pct FLOAT,
        arsenopyrite_pct FLOAT,
        pyrrhotite_pct FLOAT,
        chalcopyrite_pct FLOAT,
        galene_pct FLOAT,
        sphalente_pct FLOAT,
        siderite_pct FLOAT,
        calcite_pct FLOAT,
        dolomite_pct FLOAT,
        quartz_pct FLOAT,
        feldspath_pct FLOAT,
        plagioclase_pct FLOAT,
        biotite_pct FLOAT,
        chlorite_pct FLOAT,
        muscovite_pct FLOAT,
        kaolinite_pct FLOAT,
        hematite_pct FLOAT,
        goethite_pct FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- ═══════════════════════════════════════════════════════════════
    -- COM-02: COMMINUTION — enrich lims_b1
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS a_x_b FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS ta FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS dwi_kwh_m3 FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS mia_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS mib_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS mic_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS mih_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS brwi_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS bulk_density_t_m3 FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS crushing_wi_kwh_t FLOAT;
    ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS sag_classification VARCHAR(50);

    -- ═══════════════════════════════════════════════════════════════
    -- GRV-03: GRAVIMÉTRIE — enrich lims_c2 + create lims_c2b, lims_c2c
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS p80_alim_um FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS solides_alim_pct FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS masse_alim_kg FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS au_alim_g_t FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS vitesse_knelson_rpm FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS pression_eau_psi FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS duree_min FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS masse_conc_g FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS au_conc_g_t FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS au_tail_g_t FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS yield_pct FLOAT;
    ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS enrichment_ratio FLOAT;

    CREATE TABLE IF NOT EXISTS lims_c2b (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        stage INTEGER DEFAULT 1,
        p80_um FLOAT,
        masse_conc_g FLOAT,
        au_conc_g_t FLOAT,
        au_conc_g FLOAT,
        masse_tail_g FLOAT,
        au_tail_g_t FLOAT,
        au_tail_g FLOAT,
        grg_cumul_pct FLOAT,
        recup_stage_pct FLOAT,
        yield_massique_pct FLOAT,
        equipment_type VARCHAR(50) DEFAULT 'Knelson',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS lims_c2c (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        inclinaison_deg FLOAT,
        freq_vibration_hz FLOAT,
        debit_eau_l_min FLOAT,
        densite_coupure_t_m3 FLOAT,
        temps_residence_min FLOAT,
        masse_alim_g FLOAT,
        au_alim_g_t FLOAT,
        au_conc_g_t FLOAT,
        au_tail_g_t FLOAT,
        rendement_massique_pct FLOAT,
        recup_au_pct FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- ═══════════════════════════════════════════════════════════════
    -- FLT-04: FLOTTATION — enrich lims_flotation
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS p80_alim_um FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS ph_pulpe FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS solides_pulpe_pct FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS pax_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS mibc_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS depresseur_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS temps_rougher_min FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS au_alim_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS au_conc_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS au_tail_g_t FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS s_recovery_pct FLOAT;
    ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS as_conc_ppm FLOAT;

    -- ═══════════════════════════════════════════════════════════════
    -- LIX-05: LIXIVIATION — enrich lims_d1 + create kinetics table
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS p80_alim_um FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS solides_pulpe_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS nacn_initial_ppm FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS nacn_residuel_ppm FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS ph_initial FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS ph_final FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS o2_mg_l FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS o2_consumption_kg_t FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS temperature_c FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS duree_h FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS carbon_dose_g_l FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS preg_rob_index FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_2h_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_4h_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_8h_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_12h_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_24h_pct FLOAT;
    ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS leach_rec_48h_pct FLOAT;

    CREATE TABLE IF NOT EXISTS lims_leach_kinetics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        test_id UUID REFERENCES lims_d1(id),
        time_h FLOAT NOT NULL,
        au_solution_mg_l FLOAT,
        au_residue_g_t FLOAT,
        recovery_pct FLOAT,
        ag_solution_mg_l FLOAT,
        cu_solution_mg_l FLOAT,
        cn_free_ppm FLOAT,
        cn_total_ppm FLOAT,
        o2_mg_l FLOAT,
        ph FLOAT,
        eh_mv FLOAT,
        fe2_mg_l FLOAT,
        leach_rate_g_l_h FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- ═══════════════════════════════════════════════════════════════
    -- EPP-06: ÉPAISSISSEUR — enrich lims_e1
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS isr_m_h FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS fsr_m_h FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS uf_density_pct FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS uf_density_t_m3 FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS overflow_turbidity_ntu FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS flux_t_m2_d FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS cn_overflow_ppm FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS au_overflow_ppb FLOAT;
    ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS viscosity_mpa_s FLOAT;

    -- ═══════════════════════════════════════════════════════════════
    -- FIL-07: FILTRATION — enrich lims_e2
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS solides_alim_pct FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS vide_kpa FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS temps_cycle_min FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS temps_formation_min FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS temps_sechage_min FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS flux_filtrat_l_m2_h FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS resistance_alpha FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS epaisseur_gateau_mm FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS cn_gateau_ppm FLOAT;
    ALTER TABLE lims_e2 ADD COLUMN IF NOT EXISTS pression_diff_kpa FLOAT;

    -- ═══════════════════════════════════════════════════════════════
    -- ELU-08: ÉLUTION — restructure lims_elution
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS pression_kpa FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS debit_bv_h FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS volumes_lit FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS duree_totale_h FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS au_carbone_avant_g_t FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS au_residuel_g_t FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS au_pic_mg_l FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS volume_pls_l FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS consomm_naoh_kg_t FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS consomm_nacn_kg_t FLOAT;
    ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS consomm_energie_kwh_t FLOAT;

    -- ═══════════════════════════════════════════════════════════════
    -- DTX-09: DÉTOXIFICATION — create lims_detox
    -- ═══════════════════════════════════════════════════════════════
    CREATE TABLE IF NOT EXISTS lims_detox (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id),
        sample_id UUID REFERENCES lims_samples(id),
        cn_wad_mg_l FLOAT,
        cn_total_mg_l FLOAT,
        cn_free_mg_l FLOAT,
        scn_mg_l FLOAT,
        ph_final FLOAT,
        cu_mg_l FLOAT,
        fe_mg_l FLOAT,
        ni_mg_l FLOAT,
        zn_mg_l FLOAT,
        as_mg_l FLOAT,
        hg_ug_l FLOAT,
        pb_mg_l FLOAT,
        consomm_so2_kg_t FLOAT,
        consomm_h2o2_kg_t FLOAT,
        consomm_cuso4_kg_t FLOAT,
        consomm_cao_kg_t FLOAT,
        duree_traitement_min FLOAT,
        cn_wad_rebound_24h FLOAT,
        cn_wad_rebound_7d FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- ═══════════════════════════════════════════════════════════════
    -- ENV-10: ENVIRONNEMENT — enrich lims_environmental
    -- ═══════════════════════════════════════════════════════════════
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS s_total_pct FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS s_sulfure_pct FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ap_kg_caco3_t FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS np_kg_caco3_t FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS nnp_kg_caco3_t FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS npr_ratio FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ph_paste FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS conductivity_us_cm FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS ard_classification VARCHAR(50);
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_as_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_ba_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_cd_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_cr_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_hg_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_pb_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_se_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS tclp_ag_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS splp_as_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS splp_pb_mg_l FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS density_solids_sg FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS permeability_m_s FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS shear_strength_deg FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS liquid_limit_pct FLOAT;
    ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS plastic_limit_pct FLOAT;

    -- ═══════════════════════════════════════════════════════════════
    -- INDEXES pour les nouvelles tables
    -- ═══════════════════════════════════════════════════════════════
    CREATE INDEX IF NOT EXISTS idx_lims_a2_project ON lims_a2(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_a3_project ON lims_a3(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_m1_project ON lims_m1(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_c2b_project ON lims_c2b(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_c2c_project ON lims_c2c(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_detox_project ON lims_detox(project_id);
    CREATE INDEX IF NOT EXISTS idx_lims_leach_kin_project ON lims_leach_kinetics(project_id);

    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS lims_leach_kinetics CASCADE;
        DROP TABLE IF EXISTS lims_detox CASCADE;
        DROP TABLE IF EXISTS lims_c2c CASCADE;
        DROP TABLE IF EXISTS lims_c2b CASCADE;
        DROP TABLE IF EXISTS lims_m1 CASCADE;
        DROP TABLE IF EXISTS lims_a3 CASCADE;
        DROP TABLE IF EXISTS lims_a2 CASCADE;
    """)
