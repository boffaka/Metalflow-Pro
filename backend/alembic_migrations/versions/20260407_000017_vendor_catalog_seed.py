"""Vendor catalog seed data — reference cost correlations

Revision ID: 000017
Revises: 000016
Create Date: 2026-04-07

Reference: CostMine Mining Cost Service, AME Group benchmark data.
These are representative correlations for estimation purposes only.
CEPCI 2024 base index ≈ 820.
"""
from alembic import op
import uuid

revision = "000017"
down_revision = "000016"
branch_labels = None
depends_on = None

SEED_DATA = [
    {
        "equipment_family": "SAG Mill", "manufacturer": "Metso Outotec",
        "model_series": "Premier SAG",
        "power_range_kw_min": 2000, "power_range_kw_max": 30000,
        "capacity_range_min": 200, "capacity_range_max": 3000, "capacity_unit": "t/h",
        "lead_time_weeks": 72, "reference_capex_usd": 12000000,
        "reference_capacity": 5000, "cepci_year": 2024,
        "correlation_a": 1800, "correlation_b": 0.62,
    },
    {
        "equipment_family": "SAG Mill", "manufacturer": "FLSmidth",
        "model_series": "MAAG SAG",
        "power_range_kw_min": 1500, "power_range_kw_max": 25000,
        "capacity_range_min": 150, "capacity_range_max": 2500, "capacity_unit": "t/h",
        "lead_time_weeks": 78, "reference_capex_usd": 11500000,
        "reference_capacity": 5000, "cepci_year": 2024,
        "correlation_a": 1750, "correlation_b": 0.62,
    },
    {
        "equipment_family": "Ball Mill", "manufacturer": "FLSmidth",
        "model_series": "UMS Ball Mill",
        "power_range_kw_min": 500, "power_range_kw_max": 15000,
        "capacity_range_min": 50, "capacity_range_max": 1500, "capacity_unit": "t/h",
        "lead_time_weeks": 52, "reference_capex_usd": 5500000,
        "reference_capacity": 3000, "cepci_year": 2024,
        "correlation_a": 1200, "correlation_b": 0.60,
    },
    {
        "equipment_family": "Ball Mill", "manufacturer": "Metso Outotec",
        "model_series": "Vertimill",
        "power_range_kw_min": 100, "power_range_kw_max": 5000,
        "capacity_range_min": 20, "capacity_range_max": 400, "capacity_unit": "t/h",
        "lead_time_weeks": 40, "reference_capex_usd": 2000000,
        "reference_capacity": 1000, "cepci_year": 2024,
        "correlation_a": 900, "correlation_b": 0.58,
    },
    {
        "equipment_family": "HPGR", "manufacturer": "Köppern",
        "model_series": "HPGR 850/140",
        "power_range_kw_min": 500, "power_range_kw_max": 8000,
        "capacity_range_min": 100, "capacity_range_max": 2000, "capacity_unit": "t/h",
        "lead_time_weeks": 60, "reference_capex_usd": 8000000,
        "reference_capacity": 1000, "cepci_year": 2024,
        "correlation_a": 2200, "correlation_b": 0.65,
    },
    {
        "equipment_family": "Flotation", "manufacturer": "Metso Outotec",
        "model_series": "TankCell 300",
        "power_range_kw_min": 37, "power_range_kw_max": 450,
        "capacity_range_min": 50, "capacity_range_max": 350, "capacity_unit": "m3",
        "lead_time_weeks": 36, "reference_capex_usd": 350000,
        "reference_capacity": 300, "cepci_year": 2024,
        "correlation_a": 8500, "correlation_b": 0.55,
    },
    {
        "equipment_family": "Flotation", "manufacturer": "Eriez",
        "model_series": "HydroFloat",
        "power_range_kw_min": 15, "power_range_kw_max": 200,
        "capacity_range_min": 20, "capacity_range_max": 150, "capacity_unit": "m3",
        "lead_time_weeks": 28, "reference_capex_usd": 200000,
        "reference_capacity": 100, "cepci_year": 2024,
        "correlation_a": 7000, "correlation_b": 0.52,
    },
    {
        "equipment_family": "Thickener", "manufacturer": "FLSmidth",
        "model_series": "Dorr-Oliver High-Rate",
        "power_range_kw_min": 5, "power_range_kw_max": 150,
        "capacity_range_min": 10, "capacity_range_max": 60, "capacity_unit": "m",
        "lead_time_weeks": 30, "reference_capex_usd": 2500000,
        "reference_capacity": 40, "cepci_year": 2024,
        "correlation_a": 45000, "correlation_b": 1.8,
    },
    {
        "equipment_family": "IsaMill", "manufacturer": "Glencore Technology",
        "model_series": "M10000",
        "power_range_kw_min": 1120, "power_range_kw_max": 3000,
        "capacity_range_min": 50, "capacity_range_max": 300, "capacity_unit": "t/h",
        "lead_time_weeks": 52, "reference_capex_usd": 4500000,
        "reference_capacity": 2238, "cepci_year": 2024,
        "correlation_a": 1600, "correlation_b": 0.62,
    },
    {
        "equipment_family": "EW Cell", "manufacturer": "Chemours",
        "model_series": "EW Rectifier System",
        "power_range_kw_min": 50, "power_range_kw_max": 2000,
        "capacity_range_min": 10, "capacity_range_max": 200, "capacity_unit": "cathodes",
        "lead_time_weeks": 20, "reference_capex_usd": 500000,
        "reference_capacity": 60, "cepci_year": 2024,
        "correlation_a": 6000, "correlation_b": 0.70,
    },
]


def upgrade():
    for entry in SEED_DATA:
        op.execute(
            f"""
            INSERT INTO vendor_catalog (
                id, equipment_family, manufacturer, model_series,
                power_range_kw_min, power_range_kw_max,
                capacity_range_min, capacity_range_max, capacity_unit,
                lead_time_weeks, reference_capex_usd, reference_capacity,
                cepci_year, correlation_a, correlation_b
            ) VALUES (
                '{uuid.uuid4()}',
                '{entry["equipment_family"]}', '{entry["manufacturer"]}',
                '{entry["model_series"]}',
                {entry["power_range_kw_min"]}, {entry["power_range_kw_max"]},
                {entry["capacity_range_min"]}, {entry["capacity_range_max"]},
                '{entry["capacity_unit"]}',
                {entry["lead_time_weeks"]},
                {entry["reference_capex_usd"]}, {entry["reference_capacity"]},
                {entry["cepci_year"]},
                {entry["correlation_a"]}, {entry["correlation_b"]}
            )
            """
        )

    for family in ["SAG Mill", "Ball Mill", "HPGR", "Flotation", "Thickener", "IsaMill", "EW Cell"]:
        for factor_name, factor_value in [
            ("installation", 0.35), ("civil", 0.25),
            ("instrumentation", 0.15), ("piping", 0.20), ("electrical", 0.10),
        ]:
            op.execute(
                f"""
                INSERT INTO capex_correlations (id, equipment_family, factor_name, factor_value, reference)
                VALUES ('{uuid.uuid4()}', '{family}', '{factor_name}', {factor_value}, 'Lang')
                """
            )


def downgrade():
    op.execute("DELETE FROM vendor_catalog WHERE TRUE")
    op.execute("DELETE FROM capex_correlations WHERE reference = 'Lang'")
