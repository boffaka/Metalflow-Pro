# backend/engines/mer_generator.py
"""
MetalFlow Pro — MER (Mechanical Equipment Register) Auto-Generation Engine.

Generates a complete professional MER from circuit template operations.
For each enabled operation, creates the principal equipment AND all auxiliary
items (lube units, hydraulics, hoists, sump pumps, dust collectors, cranes,
emergency showers, samplers, etc.)

Target output: ~490 items for a full gold plant (CIL/CIP flowsheet).
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

import psycopg2.extras

logger = logging.getLogger("mpdpms.mer_generator")

# =============================================================================
# WBS MAPPING  (operation code -> WBS area code)
# =============================================================================

OP_WBS_MAP: dict[str, str] = {
    "GIRATOIRE": "554", "CRIBLE": "555", "CONE": "555",
    "HPGR": "558", "STOCKPILE": "557",
    "SAG_MILL": "562", "BALL_MILL": "562", "ROD_MILL": "562",
    "HYDROCYCLONE": "562",
    "FLOTATION_ROUGHER": "565", "FLOTATION_SCAVENGER": "565",
    "FLOTATION_CLEANER": "565", "FLOTATION_COLONNE": "565",
    "GRAVITE_KNELSON": "566", "GRAVITE_FALCON": "566",
    "ISAMILL": "567", "VERTIMILL_REGRIND": "567", "SMD": "567",
    "EPAISSISSEUR": "583", "EPAISSISSEUR_HD": "583",
    "EPAISSISSEUR_CONC": "571",
    "PREAERATION": "576", "LEACH_CUVES": "576",
    "CIP": "576", "CIL": "576",
    "ELUTION_AARL": "580", "ELUTION_ZADRA": "580",
    "ELECTROWINNING": "581", "FONDERIE": "581",
    "DETOX_INCO": "582", "DETOX_CARO": "582", "DETOX_PEROXIDE": "582",
    "TSF_CONVENTIONNEL": "583", "TSF_DRY_STACK": "583",
    "BASSIN_EAU": "501", "TRAITEMENT_EFFLUENT": "501",
    "REACTIF_PAX": "585", "REACTIF_MIBC": "585", "REACTIF_FLOCCULANT": "585",
    "REACTIF_LIME": "585", "REACTIF_NACN": "585", "REACTIF_CUSO4": "585",
    "REACTIF_NAOH": "585", "REACTIF_SO2": "585", "REACTIF_OXYGEN": "588",
    "REACTIF_CARBON": "585", "REACTIF_ACID": "585",
}

# =============================================================================
# WBS DESCRIPTIONS
# =============================================================================

WBS_DESC: dict[str, str] = {
    "501": "Water",
    "502": "Air",
    "504": "Fire Protection",
    "554": "Primary Crushing",
    "555": "Secondary Crushing",
    "557": "Crushed Ore Storage",
    "558": "Tertiary Crushing (HPGR)",
    "562": "Grinding",
    "565": "Flotation",
    "566": "Gravity Concentration",
    "567": "Regrinding",
    "571": "Thickening",
    "576": "Carbon-in-Pulp / In-Leach (CIP/CIL)",
    "580": "Carbon Stripping & Regeneration",
    "581": "Electrowinning and Refinery",
    "582": "Cyanide Destruction",
    "583": "Tailing Dewatering",
    "585": "Reagent",
    "588": "Oxygen Plant / System",
    "598": "Mobile Equipment",
    "691": "Reclaim Water Pumping Station",
}

# =============================================================================
# EQUIPMENT TYPE CODES  (2-3 letter codes per MER standard)
# =============================================================================

EQ_TYPES: dict[str, str] = {
    "crusher": "CRU", "screen": "SCR", "conveyor": "CON", "feeder": "FED",
    "bin": "BIN", "stockpile": "STK", "hpgr": "CRU", "mill": "MLL",
    "cyclone": "CYC", "pump_slurry": "PSR", "pump_water": "PWA",
    "pump_sump": "PPU", "pumpbox": "BPU", "thickener": "THI",
    "tank": "TNK", "agitator": "AGI", "flotation_cell": "CFL",
    "blower": "BLO", "elution_column": "CDE", "ew_cell": "CEL",
    "rectifier": "REC", "furnace": "FFU", "kiln": "KIL",
    "heat_exchanger": "HEX", "heater": "HEA", "filter": "FPL",
    "crane": "CRN", "hoist": "HST", "compressor": "COM",
    "air_dryer": "ADR", "air_receiver": "ARE", "dust_collector": "DCO",
    "fan": "FAN", "magnet": "MAG", "metal_detector": "MED",
    "scale": "SCA", "sampler": "SAM", "analyzer": "ANA",
    "valve": "DIV", "chute": "CHU", "silo": "SIL", "slaker": "SLA",
    "scrubber": "SCB", "shower": "SEM", "rock_breaker": "SKP",
    "lube_unit": "UGR", "hydraulic_unit": "HYD", "gear_spray": "GEA",
    "motor_hoist": "HST", "hopper": "HOP", "screw_feeder": "CVI",
    "mixing_tank": "MIX", "dosing_pump": "PME", "transfer_pump": "PSO",
    "apron_feeder": "AFE", "trash_screen": "TSC", "carbon_screen": "CSC",
    "conditioning_tank": "CTK", "acid_wash_column": "AWC",
    "dewatering_screen": "DWS", "monorail": "MNR", "vault_door": "VLT",
    "drying_oven": "OVN", "tools": "TOL", "quench_tank": "QTK",
    "fume_hood": "FHD", "belt_filter": "BFI", "gate": "GAT",
    "pressure_washer": "PWS",
}

# =============================================================================
# REFERENCE PRICES (CAD) — major equipment only
# =============================================================================

REF_PRICES: dict[str, float | None] = {
    "Gyratory Crusher": 3_200_000,
    "Ball Mill": 25_500_000,
    "SAG Mill": 28_000_000,
    "HPGR": 10_100_000,
    "Flotation Cell": 4_200_000,
    "IsaMill M15000": 5_450_000,
    "Thickener": 3_550_000,
    "Cone Crusher MP1250": 5_325_000,
}

# Default prices by eq_type code (CAD) — applied when item has no explicit price
# Based on industry benchmarks for PFS-level estimates (AACE Class 4)
EQ_TYPE_DEFAULT_PRICES: dict[str, float] = {
    "CRU": 250_000,      # Crushers (small/auxiliary)
    "AGI": 85_000,       # Agitators
    "BLW": 180_000,      # Blowers/compressors
    "CRN": 350_000,      # Cranes
    "EWC": 120_000,      # Electrowinning cells
    "FLT": 450_000,      # Filters
    "FRN": 280_000,      # Furnaces/kilns
    "HPR": 10_100_000,   # HPGR
    "MIL": 25_500_000,   # Mills (ball/SAG)
    "PMP": 45_000,       # Pumps (general)
    "PSR": 65_000,       # Pumps (slurry)
    "PWA": 35_000,       # Pumps (water)
    "SCR": 180_000,      # Screens
    "THK": 3_550_000,    # Thickeners
    "TNK": 120_000,      # Tanks
    "VLV": 8_000,        # Valves
    "CON": 85_000,       # Conveyors (per unit)
    "FDR": 65_000,       # Feeders
    "SAM": 25_000,       # Samplers
    "CLN": 45_000,       # Columns (elution)
    "HEX": 95_000,       # Heat exchangers
    "FLC": 4_200_000,    # Flotation cells
    "CYC": 35_000,       # Cyclones
    "EQP": 50_000,       # General equipment
}


# =============================================================================
# ITEM DEFINITION HELPER
# =============================================================================
# Each tuple: (eq_type, name, qty, duty_status, kw, is_long_lead, vendor, comments, price_cad)
# kw can be: int/float literal, or "dc:<field>|<default>" to read from design criteria

ItemDef = tuple[str, str, int, str, Any, bool, str | None, str | None, float | None]


def _item(eq_type: str, name: str, qty: int = 1, duty: str = "Duty",
          kw: Any = 0, long_lead: bool = False, vendor: str | None = None,
          comments: str | None = None, price: float | None = None) -> ItemDef:
    return (eq_type, name, qty, duty, kw, long_lead, vendor, comments, price)


# =============================================================================
# EQUIPMENT DEFINITIONS PER OPERATION
# =============================================================================

GIRATOIRE_ITEMS: list[ItemDef] = [
    _item("rock_breaker", "Primary Rock Breaker", kw=110, vendor="Sandvik", comments="Hydraulic pedestal mount"),
    _item("hydraulic_unit", "Rock Breaker Hydraulic Unit", kw=37, vendor="Sandvik"),
    _item("crusher", "Gyratory Crusher", kw="dc:power_kw|750", long_lead=True, vendor="Metso", comments="60x113, 1500 kW motor, VFD", price=3_200_000),
    _item("lube_unit", "Gyratory Lube Unit", kw=22, vendor="Metso"),
    _item("lube_unit", "Gyratory Oil Cooling Unit", kw=15, vendor="Metso"),
    _item("hydraulic_unit", "Gyratory Hydraulic Unit", kw=30, vendor="Metso"),
    _item("fan", "Gyratory Dust Seal Fan", kw=15, vendor="Metso"),
    _item("hoist", "Gyratory Shaft Support Stand", duty="Standby", vendor="Metso"),
    _item("hoist", "Gyratory Mantle Cart Removal", duty="Standby", vendor="Metso"),
    _item("bin", "Gyratory Discharge Bin", comments="Platework, lined"),
    _item("dust_collector", "Primary Crushing Dust Collection System", kw=150, vendor="Camfil"),
    _item("fan", "Dust Collector Exhaust Fan", kw=55, vendor="Camfil"),
    _item("crane", "Primary Crushing Area Crane", kw=130, vendor="Scott Steel", comments="50T, emergency power"),
    _item("hoist", "Primary Crushing Area Hoist", kw=7.5, vendor="Konecranes"),
    _item("apron_feeder", "Apron Feeder", kw=200, vendor="Metso", comments="2250mm wide"),
    _item("hoist", "Apron Feeder Hoist", kw=5, vendor="Konecranes"),
    _item("conveyor", "Primary Crusher Discharge Conveyor", kw=400, vendor="Martin", comments="1800mm wide"),
    _item("magnet", "Discharge Conveyor Cross-Belt Magnet", kw=8, vendor="Eriez"),
    _item("bin", "Magnet Trash Bin", comments="Platework"),
    _item("metal_detector", "Discharge Conveyor Metal Detector", kw=2, vendor="Eriez"),
    _item("scale", "Discharge Conveyor Belt Scale", kw=1, vendor="Thermo Fisher"),
    _item("pump_sump", "Crushing Area Portable Sump Pump", kw=15, duty="Standby"),
    _item("compressor", "Primary Crushing Air Compressor", kw=90, vendor="Atlas Copco", comments="Oil-free"),
    _item("air_dryer", "Primary Crushing Air Dryer", kw=5, vendor="Atlas Copco"),
    _item("air_receiver", "Primary Crushing Air Receiver", vendor="Atlas Copco", comments="2 m3"),
    _item("shower", "Primary Crushing Emergency Shower", comments="Emergency power"),
]

CRIBLE_ITEMS: list[ItemDef] = [
    _item("screen", "Vibrating Screen", kw=90, long_lead=True, vendor="Metso", comments="Banana screen, 3660x7320mm"),
    _item("chute", "Screen Feed Chute", comments="Platework, lined"),
    _item("chute", "Screen Oversize Chute", comments="Platework, lined"),
    _item("chute", "Screen Undersize Chute", comments="Platework, lined"),
    _item("conveyor", "Screen Feed Conveyor", kw=250, vendor="Martin", comments="1500mm wide"),
    _item("conveyor", "Screen Oversize Transfer Conveyor", kw=200, vendor="Martin", comments="1200mm wide"),
    _item("metal_detector", "Screen Feed Metal Detector", kw=2, vendor="Eriez"),
    _item("scale", "Screen Feed Belt Scale", kw=1, vendor="Thermo Fisher"),
    _item("dust_collector", "Secondary Crushing Dust Collection", kw=90, vendor="Camfil"),
    _item("fan", "Dust Collector Fan", kw=37, vendor="Camfil"),
    _item("pump_sump", "Secondary Crushing Sump Pump", kw=15),
    _item("shower", "Secondary Crushing Emergency Shower"),
]

CONE_ITEMS: list[ItemDef] = [
    _item("crusher", "Cone Crusher MP1250", kw="dc:power_kw|600", long_lead=True, vendor="Metso", comments="VFD drive", price=5_325_000),
    _item("crusher", "Cone Crusher Spare Head", duty="Standby", vendor="Metso", long_lead=True),
    _item("crusher", "Cone Crusher Spare Bowl", duty="Standby", vendor="Metso", long_lead=True),
    _item("lube_unit", "Cone Crusher Lube Unit", kw=15, vendor="Metso"),
    _item("hydraulic_unit", "Cone Crusher Hydraulic Unit", kw=22, vendor="Metso"),
    _item("fan", "Cone Crusher Dust Seal Fan", kw=7.5, vendor="Metso"),
    _item("fan", "Cone Crusher Cooling Fan", kw=15, vendor="Metso"),
    _item("bin", "Cone Crusher Feed Bin", comments="Platework, 150t live capacity"),
    _item("feeder", "Cone Crusher Belt Feeder", kw=30, vendor="Metso"),
    _item("dust_collector", "Cone Crusher Dust Collection", kw=75, vendor="Camfil"),
    _item("fan", "Cone Crusher Dust Collection Fan", kw=30, vendor="Camfil"),
    _item("crane", "Secondary Crushing Area Crane", kw=100, vendor="Scott Steel", comments="30T"),
    _item("conveyor", "Cone Crusher Return Conveyor", kw=200, vendor="Martin", comments="1200mm wide"),
    _item("pump_sump", "Secondary Crushing Area Sump Pump", kw=15),
]

HPGR_ITEMS: list[ItemDef] = [
    _item("bin", "HPGR Feed Control Bin", comments="100t live capacity"),
    _item("bin", "HPGR Feed Bin", comments="250t live capacity, lined"),
    _item("gate", "HPGR Feed Gate", kw=5),
    _item("feeder", "HPGR Belt Feeder", kw=55, vendor="Metso"),
    _item("hpgr", "HPGR", kw="dc:power_kw|5800", long_lead=True, vendor="Metso", comments="HRC3000, 2x2900 kW VFD", price=10_100_000),
    _item("lube_unit", "HPGR Lube Unit", kw=30, vendor="Metso"),
    _item("hydraulic_unit", "HPGR Hydraulic Unit", kw=45, vendor="Metso"),
    _item("screen", "HPGR Wet Screen No. 1", kw=45, vendor="Metso", comments="Stack Sizer, 3050x7320mm"),
    _item("screen", "HPGR Wet Screen No. 2", kw=45, vendor="Metso", comments="Stack Sizer, 3050x7320mm"),
    _item("pumpbox", "Wet Screen Feed Box No. 1", comments="Platework"),
    _item("pumpbox", "Wet Screen Feed Box No. 2", comments="Platework"),
    _item("bin", "Screen Feed Bin", comments="Platework"),
    _item("gate", "HPGR Discharge Gate No. 1", kw=5),
    _item("gate", "HPGR Discharge Gate No. 2", kw=5),
    _item("conveyor", "HPGR Oversize Recycle Conveyor", kw=200, vendor="Martin", comments="1200mm wide"),
    _item("conveyor", "HPGR Product Conveyor", kw=250, vendor="Martin", comments="1500mm wide"),
    _item("dust_collector", "HPGR Dust Collection System", kw=110, vendor="Camfil"),
    _item("fan", "HPGR Dust Collection Fan", kw=45, vendor="Camfil"),
    _item("scale", "HPGR Feed Weightometer", kw=1, vendor="Thermo Fisher"),
    _item("scale", "HPGR Product Weightometer", kw=1, vendor="Thermo Fisher"),
    _item("metal_detector", "HPGR Feed Metal Detector", kw=2, vendor="Eriez"),
    _item("pump_sump", "HPGR Area Sump Pump", kw=23),
    _item("compressor", "HPGR Area Air Compressor", kw=90, vendor="Atlas Copco"),
    _item("air_dryer", "HPGR Area Air Dryer", kw=5, vendor="Atlas Copco"),
    _item("air_receiver", "HPGR Area Air Receiver", vendor="Atlas Copco", comments="2 m3"),
    _item("crane", "HPGR Area Overhead Crane No. 1", kw=130, vendor="Scott Steel", comments="50T, emergency power"),
    _item("crane", "HPGR Area Overhead Crane No. 2", kw=80, vendor="Scott Steel", comments="25T"),
    _item("shower", "HPGR Area Emergency Shower", comments="Emergency power"),
]

STOCKPILE_ITEMS: list[ItemDef] = [
    _item("stockpile", "Crushed Ore Stockpile", comments="Live capacity 24h"),
    _item("conveyor", "Stockpile Feed Conveyor", kw=350, vendor="Martin", comments="1500mm, tripper"),
    _item("feeder", "Stockpile Reclaim Feeder No. 1", kw=90, vendor="Metso"),
    _item("feeder", "Stockpile Reclaim Feeder No. 2", kw=90, vendor="Metso"),
    _item("conveyor", "Stockpile Reclaim Conveyor", kw=300, vendor="Martin", comments="1500mm"),
    _item("scale", "Reclaim Conveyor Belt Scale", kw=1, vendor="Thermo Fisher"),
    _item("dust_collector", "Stockpile Dust Collection", kw=55, vendor="Camfil"),
    _item("pump_sump", "Stockpile Area Sump Pump", kw=15),
]

SAG_MILL_ITEMS: list[ItemDef] = [
    _item("mill", "SAG Mill", kw="dc:power_kw|22000", long_lead=True, vendor="Metso", comments="40x22, gearless drive", price=28_000_000),
    _item("pumpbox", "SAG Mill Discharge Pumpbox", comments="Platework allowance"),
    _item("pump_slurry", "SAG Mill Cyclone Feed Pump 1", kw=800, vendor="FLSmidth", comments="VFD", duty="Duty"),
    _item("pump_slurry", "SAG Mill Cyclone Feed Pump 2", kw=800, vendor="FLSmidth", comments="VFD", duty="Standby"),
    _item("cyclone", "SAG Mill Cyclone Cluster", vendor="FLSmidth", comments="12 duty + 2 standby"),
    _item("screen", "SAG Mill Trommel Screen", vendor="Metso", comments="10mm aperture"),
    _item("conveyor", "SAG Mill Pebble Conveyor", kw=75, vendor="Martin"),
    _item("lube_unit", "SAG Mill Lube Unit", kw=495, vendor="Metso"),
    _item("lube_unit", "SAG Mill Temporary Lube Unit", kw=30, duty="Standby", vendor="Metso"),
    _item("hydraulic_unit", "SAG Mill Hydraulic Unit", kw=4, vendor="Metso"),
    _item("gear_spray", "SAG Mill Gear Spray Unit", kw=4, vendor="Metso"),
    _item("hoist", "SAG Mill Winch", duty="Standby", vendor="RME"),
    _item("chute", "SAG Mill Discharge Launder", comments="Platework"),
    _item("hopper", "SAG Mill Feed Hopper", comments="Platework"),
    _item("crane", "SAG Mill Area Overhead Crane", kw=130, vendor="Scott Steel", comments="50T, emergency power"),
    _item("pump_sump", "SAG Mill Area Sump Pump", kw=23),
    _item("shower", "SAG Mill Area Emergency Shower", comments="Emergency power"),
    _item("sampler", "SAG Mill Discharge Sampler", kw=1, comments="2-stage"),
]

BALL_MILL_ITEMS: list[ItemDef] = [
    _item("mill", "Ball Mill", kw="dc:power_kw|16000", long_lead=True, vendor="Metso", comments="28x42, VFD drive", price=25_500_000),
    _item("pumpbox", "Cyclone Feed Pumpbox", comments="Platework allowance"),
    _item("pump_slurry", "Cyclone Feed Pump 1", kw=1200, vendor="FLSmidth", comments="VFD", duty="Duty"),
    _item("pump_slurry", "Cyclone Feed Pump 2", kw=1200, vendor="FLSmidth", comments="VFD", duty="Standby"),
    _item("cyclone", "Ball Mill Cyclone Cluster", vendor="FLSmidth", comments="15 duty + 2 standby"),
    _item("feeder", "Ball Mill Kibble Feeder", duty="Standby", vendor="FLSmidth"),
    _item("hoist", "Ball Mill Winch", duty="Standby", vendor="RME"),
    _item("gear_spray", "Ball Mill Gear Spray Unit", kw=4, vendor="Metso"),
    _item("lube_unit", "Ball Mill Lube Unit", kw=495, vendor="Metso"),
    _item("lube_unit", "Ball Mill Temporary Lube Unit", kw=30, duty="Standby", vendor="Metso"),
    _item("hydraulic_unit", "Ball Mill Hydraulic Unit", kw=4, vendor="Metso"),
    _item("screen", "Ball Mill Trommel Screen", vendor="Metso", comments="10mm x 45mm aperture"),
    _item("chute", "Ball Mill Discharge Launder", comments="Platework"),
    _item("hopper", "Ball Mill Feed Hopper", comments="Platework"),
    _item("crane", "Grinding Area Overhead Crane", kw=130, vendor="Scott Steel", comments="50T, emergency power"),
    _item("pump_sump", "Grinding Area Sump Pump", kw=23),
    _item("shower", "Grinding Area Emergency Shower", comments="Emergency power"),
    _item("sampler", "Primary Sampler Hydrocyclones", kw=1, comments="2-stage"),
    _item("analyzer", "Particle Size Analyser", kw=10, vendor="Metso", comments="On-stream"),
]

HYDROCYCLONE_ITEMS: list[ItemDef] = [
    _item("cyclone", "Classification Cyclone Cluster", vendor="FLSmidth", comments="Gmax 26, 15+2 cyclones"),
    _item("pumpbox", "Classification Cyclone Feed Pumpbox", comments="Platework"),
    _item("pump_slurry", "Classification Cyclone Feed Pump 1", kw=500, vendor="FLSmidth", comments="VFD"),
    _item("pump_slurry", "Classification Cyclone Feed Pump 2", kw=500, vendor="FLSmidth", comments="VFD", duty="Standby"),
    _item("sampler", "Cyclone Overflow Sampler", kw=1),
    _item("sampler", "Cyclone Underflow Sampler", kw=1),
    _item("analyzer", "Cyclone Overflow PSA", kw=10, vendor="Metso"),
]

FLOTATION_ROUGHER_ITEMS: list[ItemDef] = [
    _item("trash_screen", "Rougher Flotation Trash Screen No. 1", kw=3, vendor="Derrick", comments="0.5mm aperture"),
    _item("trash_screen", "Rougher Flotation Trash Screen No. 2", kw=3, vendor="Derrick", comments="0.5mm aperture"),
    _item("conditioning_tank", "Rougher Conditioning Tank", kw=0, comments="100 m3, lined"),
    _item("agitator", "Rougher Conditioning Tank Agitator", kw=90, vendor="Lightnin"),
    _item("sampler", "Rougher Feed Sampler", kw=1),
    _item("sampler", "Rougher Tails Sampler", kw=1),
    _item("analyzer", "Rougher PSA", kw=10, vendor="Metso", comments="On-stream"),
    _item("flotation_cell", "Rougher Flotation Cell No. 1", kw=185, long_lead=True, vendor="FLSmidth", comments="300 m3 forced air", price=4_200_000),
    _item("flotation_cell", "Rougher Flotation Cell No. 2", kw=185, vendor="FLSmidth", comments="300 m3 forced air", price=4_200_000),
    _item("flotation_cell", "Rougher Flotation Cell No. 3", kw=185, vendor="FLSmidth", comments="300 m3 forced air", price=4_200_000),
    _item("flotation_cell", "Rougher Flotation Cell No. 4", kw=185, vendor="FLSmidth", comments="300 m3 forced air", price=4_200_000),
    _item("flotation_cell", "Rougher Flotation Cell No. 5", kw=185, vendor="FLSmidth", comments="300 m3 forced air"),
    _item("flotation_cell", "Rougher Flotation Cell No. 6", kw=185, vendor="FLSmidth", comments="300 m3 forced air"),
    _item("pumpbox", "Rougher Tails Pumpbox", comments="Platework"),
    _item("pump_slurry", "Rougher Tails Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Rougher Tails Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pumpbox", "Rougher Concentrate Pumpbox", comments="Platework"),
    _item("pump_slurry", "Rougher Concentrate Pump", kw=55, vendor="Metso"),
    _item("blower", "Rougher Flotation Blower 1", kw=450, vendor="Gardner Denver", comments="VFD"),
    _item("blower", "Rougher Flotation Blower 2", kw=450, vendor="Gardner Denver", comments="VFD", duty="Standby"),
    _item("crane", "Flotation Area Overhead Crane", kw=100, vendor="Scott Steel", comments="25T"),
    _item("pump_sump", "Flotation Area Sump Pump", kw=23),
    _item("shower", "Flotation Area Emergency Shower", comments="Emergency power"),
]

FLOTATION_SCAVENGER_ITEMS: list[ItemDef] = [
    _item("flotation_cell", "Scavenger Flotation Cell No. 1", kw=130, vendor="FLSmidth", comments="160 m3"),
    _item("flotation_cell", "Scavenger Flotation Cell No. 2", kw=130, vendor="FLSmidth", comments="160 m3"),
    _item("flotation_cell", "Scavenger Flotation Cell No. 3", kw=130, vendor="FLSmidth", comments="160 m3"),
    _item("flotation_cell", "Scavenger Flotation Cell No. 4", kw=130, vendor="FLSmidth", comments="160 m3"),
    _item("pumpbox", "Scavenger Tails Pumpbox", comments="Platework"),
    _item("pump_slurry", "Scavenger Tails Pump 1", kw=150, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Scavenger Tails Pump 2", kw=150, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pumpbox", "Scavenger Concentrate Pumpbox", comments="Platework"),
    _item("pump_slurry", "Scavenger Concentrate Pump", kw=37, vendor="Metso"),
    _item("sampler", "Scavenger Tails Sampler", kw=1),
    _item("blower", "Scavenger Flotation Blower", kw=250, vendor="Gardner Denver", comments="VFD"),
]

FLOTATION_CLEANER_ITEMS: list[ItemDef] = [
    _item("flotation_cell", "Cleaner Flotation Cell No. 1", kw=55, vendor="FLSmidth", comments="50 m3"),
    _item("flotation_cell", "Cleaner Flotation Cell No. 2", kw=55, vendor="FLSmidth", comments="50 m3"),
    _item("flotation_cell", "Cleaner Flotation Cell No. 3", kw=55, vendor="FLSmidth", comments="50 m3"),
    _item("pumpbox", "Cleaner Tails Pumpbox", comments="Platework"),
    _item("pump_slurry", "Cleaner Tails Pump", kw=55, vendor="Metso"),
    _item("pumpbox", "Cleaner Concentrate Pumpbox", comments="Platework"),
    _item("pump_slurry", "Cleaner Concentrate Pump", kw=37, vendor="Metso"),
    _item("sampler", "Cleaner Concentrate Sampler", kw=1),
    _item("blower", "Cleaner Flotation Blower", kw=110, vendor="Gardner Denver"),
]

FLOTATION_COLONNE_ITEMS: list[ItemDef] = [
    _item("flotation_cell", "Column Flotation Cell", kw=30, long_lead=True, vendor="FLSmidth", comments="3m dia x 12m"),
    _item("pump_slurry", "Column Cell Feed Pump", kw=45, vendor="Metso"),
    _item("blower", "Column Cell Air Blower", kw=55, vendor="Gardner Denver"),
    _item("pump_water", "Column Cell Wash Water Pump", kw=15),
    _item("pumpbox", "Column Cell Tails Pumpbox", comments="Platework"),
    _item("pump_slurry", "Column Cell Tails Pump", kw=30, vendor="Metso"),
    _item("sampler", "Column Concentrate Sampler", kw=1),
]

GRAVITE_KNELSON_ITEMS: list[ItemDef] = [
    _item("crusher", "Gravity Concentrator (Knelson)", kw=45, long_lead=True, vendor="FLSmidth", comments="KC-CVD48"),
    _item("pump_slurry", "Gravity Concentrate Pump", kw=15, vendor="Metso"),
    _item("tank", "Gravity Concentrate Holding Tank", comments="5 m3"),
    _item("agitator", "Gravity Concentrate Tank Agitator", kw=7.5),
    _item("pump_water", "Gravity Fluidization Water Pump", kw=15),
    _item("sampler", "Gravity Concentrate Sampler", kw=1),
]

GRAVITE_FALCON_ITEMS: list[ItemDef] = [
    _item("crusher", "Gravity Concentrator (Falcon)", kw=30, long_lead=True, vendor="Sepro", comments="Falcon SB"),
    _item("pump_slurry", "Falcon Concentrate Pump", kw=15, vendor="Metso"),
    _item("tank", "Falcon Concentrate Tank", comments="3 m3"),
    _item("pump_water", "Falcon Fluidization Water Pump", kw=11),
    _item("sampler", "Falcon Concentrate Sampler", kw=1),
]

ISAMILL_ITEMS: list[ItemDef] = [
    _item("pump_slurry", "Regrind Cyclone Feed Pump 1", kw=150, vendor="FLSmidth", comments="VFD"),
    _item("pump_slurry", "Regrind Cyclone Feed Pump 2", kw=150, vendor="FLSmidth", comments="VFD", duty="Standby"),
    _item("cyclone", "Regrind Cyclone Cluster", vendor="FLSmidth", comments="10 duty + 2 standby"),
    _item("pumpbox", "IsaMill Feed Pumpbox", comments="Platework"),
    _item("pump_slurry", "IsaMill Feed Pump 1", kw=90, vendor="FLSmidth"),
    _item("pump_slurry", "IsaMill Feed Pump 2", kw=90, vendor="FLSmidth"),
    _item("mill", "IsaMill M15000 No. 1", kw=3000, long_lead=True, vendor="Glencore", comments="Ceramic media", price=5_450_000),
    _item("mill", "IsaMill M15000 No. 2", kw=3000, long_lead=True, vendor="Glencore", comments="Ceramic media", price=5_450_000),
    _item("tank", "IsaMill Media Hopper", comments="Media charge storage"),
    _item("sampler", "Regrind Feed Sampler", kw=1),
    _item("analyzer", "Regrind Product PSA", kw=10, vendor="Metso"),
    _item("pumpbox", "IsaMill Discharge Pumpbox", comments="Platework"),
    _item("pump_slurry", "IsaMill Discharge Pump", kw=55, vendor="Metso"),
    _item("trash_screen", "IsaMill Trash Screen", kw=3, vendor="Derrick"),
    _item("pump_sump", "Regrind Area Sump Pump", kw=15),
]

VERTIMILL_REGRIND_ITEMS: list[ItemDef] = [
    _item("mill", "Vertimill VTM-1500", kw=1120, long_lead=True, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Vertimill Feed Pump", kw=90, vendor="Metso", comments="VFD"),
    _item("cyclone", "Vertimill Classification Cyclone Cluster", vendor="FLSmidth"),
    _item("pumpbox", "Vertimill Discharge Pumpbox", comments="Platework"),
    _item("pump_slurry", "Vertimill Discharge Pump", kw=55, vendor="Metso"),
    _item("sampler", "Vertimill Product Sampler", kw=1),
    _item("analyzer", "Vertimill Product PSA", kw=10, vendor="Metso"),
    _item("pump_sump", "Regrind Vertimill Area Sump Pump", kw=15),
]

SMD_ITEMS: list[ItemDef] = [
    _item("mill", "Stirred Media Detritor (SMD)", kw=750, long_lead=True, vendor="Metso"),
    _item("pump_slurry", "SMD Feed Pump", kw=55, vendor="Metso"),
    _item("cyclone", "SMD Classification Cyclone Cluster", vendor="FLSmidth"),
    _item("pumpbox", "SMD Discharge Pumpbox"),
    _item("pump_slurry", "SMD Discharge Pump", kw=37, vendor="Metso"),
    _item("sampler", "SMD Product Sampler", kw=1),
    _item("pump_sump", "SMD Area Sump Pump", kw=15),
]

EPAISSISSEUR_ITEMS: list[ItemDef] = [
    _item("thickener", "Tailings Thickener", kw=45, long_lead=True, vendor="FLSmidth", comments="50m dia, high-rate", price=3_550_000),
    _item("agitator", "Thickener Rake Drive", kw=55, vendor="FLSmidth"),
    _item("pumpbox", "Thickener Underflow Pumpbox", comments="Platework"),
    _item("pump_slurry", "Thickener Underflow Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Thickener Underflow Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_slurry", "Thickener Underflow Pump 3", kw=200, vendor="Metso", comments="VFD spare", duty="Standby"),
    _item("pump_slurry", "Thickener Booster Pump 1", kw=200, vendor="Metso"),
    _item("pump_slurry", "Thickener Booster Pump 2", kw=200, vendor="Metso", duty="Standby"),
    _item("crane", "Thickener Area Crane", kw=50, vendor="Scott Steel", comments="10T"),
    _item("pump_sump", "Thickener Area Sump Pump 1", kw=15),
    _item("pump_sump", "Thickener Area Sump Pump 2", kw=15, duty="Standby"),
    _item("shower", "Thickener Area Emergency Shower"),
]

EPAISSISSEUR_HD_ITEMS: list[ItemDef] = [
    _item("thickener", "High-Density Thickener", kw=55, long_lead=True, vendor="FLSmidth", comments="30m dia"),
    _item("agitator", "HD Thickener Rake Drive", kw=75, vendor="FLSmidth"),
    _item("pumpbox", "HD Thickener Underflow Pumpbox"),
    _item("pump_slurry", "HD Thickener Underflow Pump 1", kw=250, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "HD Thickener Underflow Pump 2", kw=250, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_sump", "HD Thickener Area Sump Pump", kw=15),
]

EPAISSISSEUR_CONC_ITEMS: list[ItemDef] = [
    _item("thickener", "Concentrate Thickener", kw=30, long_lead=True, vendor="FLSmidth", comments="15m dia"),
    _item("agitator", "Concentrate Thickener Rake Drive", kw=30, vendor="FLSmidth"),
    _item("pumpbox", "Concentrate Thickener Underflow Pumpbox"),
    _item("pump_slurry", "Concentrate Thickener U/F Pump 1", kw=55, vendor="Metso"),
    _item("pump_slurry", "Concentrate Thickener U/F Pump 2", kw=55, vendor="Metso", duty="Standby"),
    _item("pump_sump", "Concentrate Thickener Area Sump Pump", kw=15),
]

PREAERATION_ITEMS: list[ItemDef] = [
    _item("tank", "Pre-Aeration Tank", comments="1500 m3, lined"),
    _item("agitator", "Pre-Aeration Tank Agitator", kw=185, vendor="Lightnin"),
    _item("blower", "Pre-Aeration Blower 1", kw=250, vendor="Gardner Denver"),
    _item("blower", "Pre-Aeration Blower 2", kw=250, vendor="Gardner Denver", duty="Standby"),
    _item("sampler", "Pre-Aeration Discharge Sampler", kw=1),
]

LEACH_CUVES_ITEMS: list[ItemDef] = [
    _item("tank", "Leach Tank No. 1", comments="1500 m3, lined, baffled"),
    _item("tank", "Leach Tank No. 2", comments="1500 m3, lined, baffled"),
    _item("tank", "Leach Tank No. 3", comments="1500 m3, lined, baffled"),
    _item("tank", "Leach Tank No. 4", comments="1500 m3, lined, baffled"),
    _item("tank", "Leach Tank No. 5", comments="1500 m3, lined, baffled"),
    _item("agitator", "Leach Tank Agitator No. 1", kw=185, vendor="Lightnin"),
    _item("agitator", "Leach Tank Agitator No. 2", kw=185, vendor="Lightnin"),
    _item("agitator", "Leach Tank Agitator No. 3", kw=185, vendor="Lightnin"),
    _item("agitator", "Leach Tank Agitator No. 4", kw=185, vendor="Lightnin"),
    _item("agitator", "Leach Tank Agitator No. 5", kw=185, vendor="Lightnin"),
    _item("sampler", "Leach Feed Sampler", kw=1),
    _item("sampler", "Leach Discharge Sampler", kw=1),
    _item("pump_sump", "Leach Area Sump Pump", kw=23),
    _item("shower", "Leach Area Emergency Shower", comments="Emergency power"),
]

CIP_ITEMS: list[ItemDef] = [
    _item("sampler", "CIP Feed Sampler", kw=1),
    _item("tank", "CIP Tank No. 1", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 2", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 3", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 4", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 5", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 6", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 7", comments="1500 m3, lined, baffled"),
    _item("tank", "CIP Tank No. 8", comments="1500 m3, lined, baffled"),
    _item("agitator", "CIP Tank Agitator No. 1", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 2", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 3", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 4", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 5", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 6", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 7", kw=185, vendor="Lightnin"),
    _item("agitator", "CIP Tank Agitator No. 8", kw=185, vendor="Lightnin"),
    _item("carbon_screen", "CIP Intertank Screen No. 1", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 2", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 3", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 4", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 5", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 6", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 7", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIP Intertank Screen No. 8", kw=3, vendor="Kemix"),
    _item("pump_slurry", "Carbon Advance Pump No. 1", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 2", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 3", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 4", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 5", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 6", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 7", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 8", kw=15, vendor="Metso"),
    _item("analyzer", "CIP Cyanide Analyser", kw=5, vendor="ABB", comments="On-line"),
    _item("analyzer", "CIP HCN Analyser", kw=3, vendor="Dräger", comments="Safety monitor"),
    _item("carbon_screen", "Carbon Safety Screen", kw=5, vendor="Kemix", comments="Vibrating, 0.6mm aperture"),
    _item("pump_sump", "CIP Area Sump Pump 1", kw=23),
    _item("pump_sump", "CIP Area Sump Pump 2", kw=23, duty="Standby"),
    _item("shower", "CIP Area Emergency Shower", comments="Emergency power"),
    _item("sampler", "CIP Tails Sampler", kw=1),
]

CIL_ITEMS: list[ItemDef] = [
    _item("sampler", "CIL Feed Sampler", kw=1),
    _item("tank", "Pre-Aeration Tank", comments="1500 m3, lined"),
    _item("agitator", "Pre-Aeration Tank Agitator", kw=185, vendor="Lightnin"),
    _item("tank", "CIL Tank No. 1", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 2", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 3", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 4", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 5", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 6", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 7", comments="1500 m3, lined, baffled, carbon retention"),
    _item("tank", "CIL Tank No. 8", comments="1500 m3, lined, baffled, carbon retention"),
    _item("agitator", "CIL Tank Agitator No. 1", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 2", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 3", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 4", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 5", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 6", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 7", kw=185, vendor="Lightnin"),
    _item("agitator", "CIL Tank Agitator No. 8", kw=185, vendor="Lightnin"),
    _item("carbon_screen", "CIL Intertank Screen No. 1", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 2", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 3", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 4", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 5", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 6", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 7", kw=3, vendor="Kemix"),
    _item("carbon_screen", "CIL Intertank Screen No. 8", kw=3, vendor="Kemix"),
    _item("pump_slurry", "Carbon Advance Pump No. 1", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 2", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 3", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 4", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 5", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 6", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 7", kw=15, vendor="Metso"),
    _item("pump_slurry", "Carbon Advance Pump No. 8", kw=15, vendor="Metso"),
    _item("analyzer", "CIL Cyanide Analyser", kw=5, vendor="ABB"),
    _item("analyzer", "CIL HCN Analyser", kw=3, vendor="Dräger"),
    _item("carbon_screen", "Carbon Safety Screen", kw=5, vendor="Kemix"),
    _item("pump_sump", "CIL Area Sump Pump 1", kw=23),
    _item("pump_sump", "CIL Area Sump Pump 2", kw=23, duty="Standby"),
    _item("shower", "CIL Area Emergency Shower", comments="Emergency power"),
    _item("sampler", "CIL Tails Sampler", kw=1),
]

ELUTION_AARL_ITEMS: list[ItemDef] = [
    _item("pump_slurry", "Loaded Carbon Transfer Pump", kw=15, vendor="Metso"),
    _item("carbon_screen", "Loaded Carbon Dewatering Screen", kw=3, vendor="Kemix"),
    _item("tank", "Loaded Carbon Hopper", comments="10 m3"),
    _item("acid_wash_column", "Acid Wash Column", comments="3% HCl, 316L SS"),
    _item("fan", "Acid Wash Exhaust Fan", kw=7.5),
    _item("transfer_pump", "Acid Wash Transfer Pump", kw=7.5),
    _item("pump_sump", "Acid Wash Sump Pump", kw=7.5),
    _item("tank", "Dilute Acid Storage Tank", comments="20 m3, HDPE lined"),
    _item("dosing_pump", "Dilute Acid Dosing Pump", kw=3),
    _item("agitator", "Dilute Acid Tank Agitator", kw=5.5),
    _item("tank", "Neutralization Tank", comments="10 m3"),
    _item("dosing_pump", "Neutralization Dosing Pump", kw=3),
    _item("agitator", "Neutralization Tank Agitator", kw=5.5),
    _item("elution_column", "AARL Elution Column", comments="316L SS, 5t carbon capacity"),
    _item("tank", "Strip Solution Heating Tank", comments="15 m3, insulated"),
    _item("heat_exchanger", "Primary Heat Exchanger", kw=0, comments="Shell & tube"),
    _item("heat_exchanger", "Secondary Heat Exchanger", kw=0, comments="Shell & tube"),
    _item("heater", "Strip Solution Heater", kw=500, comments="Electric, 130 deg C"),
    _item("transfer_pump", "Strip Solution Pump", kw=15),
    _item("dewatering_screen", "Carbon Dewatering Screen", kw=3, vendor="Kemix"),
    _item("hopper", "Kiln Feed Hopper", comments="2 m3"),
    _item("screw_feeder", "Kiln Screw Feeder", kw=5.5),
    _item("kiln", "Carbon Regeneration Kiln", kw=90, long_lead=True, vendor="FEECO", comments="750 deg C, propane fired"),
    _item("fan", "Kiln Exhaust Fan", kw=30),
    _item("scrubber", "Kiln Exhaust Scrubber", kw=15),
    _item("quench_tank", "Carbon Quench Tank", comments="5 m3, 316L SS"),
    _item("pump_slurry", "Quench Carbon Pump", kw=7.5),
    _item("screen", "Carbon Sizing Screen", kw=3, vendor="Kemix"),
    _item("tank", "Carbon Fines Settling Tank", comments="5 m3"),
    _item("tank", "Carbon Water Tank", comments="30 m3"),
    _item("pump_water", "Carbon Transfer Water Pump", kw=15),
    _item("filter", "Carbon Fines Filter", kw=5),
    _item("pump_slurry", "Carbon Fines Pump", kw=5.5),
    _item("pump_sump", "Elution Area Sump Pump", kw=15),
    _item("shower", "Elution Area Emergency Shower No. 1", comments="Emergency power"),
    _item("shower", "Elution Area Emergency Shower No. 2", comments="Emergency power"),
]

ELUTION_ZADRA_ITEMS: list[ItemDef] = [
    _item("pump_slurry", "Loaded Carbon Transfer Pump", kw=15, vendor="Metso"),
    _item("carbon_screen", "Loaded Carbon Dewatering Screen", kw=3, vendor="Kemix"),
    _item("tank", "Loaded Carbon Hopper", comments="10 m3"),
    _item("acid_wash_column", "Acid Wash Column", comments="3% HCl, 316L SS"),
    _item("fan", "Acid Wash Exhaust Fan", kw=7.5),
    _item("transfer_pump", "Acid Wash Transfer Pump", kw=7.5),
    _item("pump_sump", "Acid Wash Sump Pump", kw=7.5),
    _item("tank", "Dilute Acid Storage Tank", comments="20 m3, HDPE"),
    _item("dosing_pump", "Dilute Acid Dosing Pump", kw=3),
    _item("agitator", "Dilute Acid Tank Agitator", kw=5.5),
    _item("tank", "Neutralization Tank", comments="10 m3"),
    _item("dosing_pump", "Neutralization Dosing Pump", kw=3),
    _item("agitator", "Neutralization Tank Agitator", kw=5.5),
    _item("elution_column", "Zadra Elution Column", comments="316L SS, recirculating"),
    _item("tank", "Zadra Strip Solution Tank", comments="20 m3, insulated"),
    _item("heat_exchanger", "Zadra Heat Exchanger", comments="Shell & tube"),
    _item("heater", "Zadra Strip Solution Heater", kw=400, comments="Electric, 95 deg C"),
    _item("transfer_pump", "Zadra Circulation Pump", kw=15),
    _item("dewatering_screen", "Carbon Dewatering Screen", kw=3, vendor="Kemix"),
    _item("hopper", "Kiln Feed Hopper", comments="2 m3"),
    _item("screw_feeder", "Kiln Screw Feeder", kw=5.5),
    _item("kiln", "Carbon Regeneration Kiln", kw=90, long_lead=True, vendor="FEECO", comments="750 deg C"),
    _item("fan", "Kiln Exhaust Fan", kw=30),
    _item("scrubber", "Kiln Exhaust Scrubber", kw=15),
    _item("quench_tank", "Carbon Quench Tank", comments="5 m3"),
    _item("pump_slurry", "Quench Carbon Pump", kw=7.5),
    _item("screen", "Carbon Sizing Screen", kw=3, vendor="Kemix"),
    _item("tank", "Carbon Fines Settling Tank", comments="5 m3"),
    _item("tank", "Carbon Water Tank", comments="30 m3"),
    _item("pump_water", "Carbon Transfer Water Pump", kw=15),
    _item("filter", "Carbon Fines Filter", kw=5),
    _item("pump_slurry", "Carbon Fines Pump", kw=5.5),
    _item("pump_sump", "Elution Area Sump Pump", kw=15),
    _item("shower", "Elution Area Emergency Shower No. 1"),
    _item("shower", "Elution Area Emergency Shower No. 2"),
]

ELECTROWINNING_ITEMS: list[ItemDef] = [
    _item("ew_cell", "Electrowinning Cell No. 1", kw=60, vendor="Kemix"),
    _item("ew_cell", "Electrowinning Cell No. 2", kw=60, vendor="Kemix"),
    _item("ew_cell", "Electrowinning Cell No. 3", kw=60, vendor="Kemix"),
    _item("ew_cell", "Electrowinning Cell No. 4", kw=60, vendor="Kemix"),
    _item("rectifier", "EW Rectifier No. 1", kw=80, vendor="Dynapower"),
    _item("rectifier", "EW Rectifier No. 2", kw=80, vendor="Dynapower"),
    _item("rectifier", "EW Rectifier No. 3", kw=80, vendor="Dynapower"),
    _item("rectifier", "EW Rectifier No. 4", kw=80, vendor="Dynapower"),
    _item("pressure_washer", "EW High Pressure Cleaner", kw=15),
    _item("tank", "Sludge Filter Feed Tank", comments="5 m3"),
    _item("pump_slurry", "Sludge Filter Feed Pump", kw=7.5),
    _item("filter", "Sludge Filter Press", kw=11, vendor="Outotec"),
    _item("bin", "Flux Bin No. 1 (Borax)", comments="1 m3"),
    _item("bin", "Flux Bin No. 2 (Soda Ash)", comments="1 m3"),
    _item("bin", "Flux Bin No. 3 (Silica)", comments="1 m3"),
    _item("bin", "Flux Bin No. 4 (Niter)", comments="1 m3"),
    _item("scale", "Flux Weighing Scale", kw=1),
    _item("mixing_tank", "Flux Mixer", kw=5.5),
    _item("furnace", "Barring Furnace", kw=250, long_lead=True, vendor="Thermcraft", comments="Tilting, 1200 deg C"),
    _item("dust_collector", "Furnace Baghouse", kw=30),
    _item("fume_hood", "Furnace Fume Hood", kw=0),
    _item("fan", "Furnace Exhaust Fan", kw=22),
    _item("tank", "Slag Tray", comments="Cast iron"),
    _item("fan", "Gold Room Exhaust Fan No. 1", kw=15),
    _item("fan", "Gold Room Exhaust Fan No. 2", kw=15),
    _item("drying_oven", "Drying Oven", kw=30, comments="For cathode sludge"),
    _item("fume_hood", "Drying Oven Fume Hood"),
    _item("fan", "Drying Oven Extraction Fan", kw=7.5),
    _item("monorail", "Gold Room Monorail", kw=5),
    _item("hoist", "Gold Room Hoist", kw=7.5, vendor="Konecranes"),
    _item("scale", "Bullion Scale", kw=1, comments="Precision 0.1g"),
    _item("pump_sump", "Gold Room Sump Pump", kw=7.5),
    _item("vault_door", "Vault Door", comments="GSA Class 5"),
    _item("tools", "Gold Room Tool Set", comments="Moulds, tongs, crucibles"),
    _item("shower", "Gold Room Emergency Shower"),
]

FONDERIE_ITEMS: list[ItemDef] = ELECTROWINNING_ITEMS  # alias

DETOX_INCO_ITEMS: list[ItemDef] = [
    _item("carbon_screen", "Detox Carbon Safety Screen", kw=5, vendor="Kemix"),
    _item("sampler", "Detox Feed Sampler", kw=1),
    _item("sampler", "Detox Discharge Sampler", kw=1),
    _item("tank", "INCO Detox Reactor No. 1", comments="600 m3, lined"),
    _item("tank", "INCO Detox Reactor No. 2", comments="600 m3, lined"),
    _item("agitator", "INCO Detox Agitator No. 1", kw=110, vendor="Lightnin"),
    _item("agitator", "INCO Detox Agitator No. 2", kw=110, vendor="Lightnin"),
    _item("analyzer", "Detox HCN Analyser", kw=3, vendor="Dräger"),
    _item("analyzer", "Detox Cyanide Analyser", kw=5, vendor="ABB"),
    _item("pumpbox", "Tailings Thickener Feed Pumpbox", comments="Platework"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_sump", "Detox Area Sump Pump", kw=15),
    _item("shower", "Detox Area Emergency Shower", comments="Emergency power"),
]

DETOX_CARO_ITEMS: list[ItemDef] = [
    _item("carbon_screen", "Detox Carbon Safety Screen", kw=5, vendor="Kemix"),
    _item("sampler", "Detox Feed Sampler", kw=1),
    _item("sampler", "Detox Discharge Sampler", kw=1),
    _item("tank", "Caro's Acid Detox Reactor No. 1", comments="600 m3, lined"),
    _item("tank", "Caro's Acid Detox Reactor No. 2", comments="600 m3, lined"),
    _item("agitator", "Caro's Acid Detox Agitator No. 1", kw=110, vendor="Lightnin"),
    _item("agitator", "Caro's Acid Detox Agitator No. 2", kw=110, vendor="Lightnin"),
    _item("mixing_tank", "Caro's Acid Mixing Tank", kw=0, comments="HDPE lined"),
    _item("dosing_pump", "Caro's Acid Dosing Pump 1", kw=3),
    _item("dosing_pump", "Caro's Acid Dosing Pump 2", kw=3, duty="Standby"),
    _item("analyzer", "Detox Cyanide Analyser", kw=5, vendor="ABB"),
    _item("pumpbox", "Tailings Thickener Feed Pumpbox"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_sump", "Detox Area Sump Pump", kw=15),
    _item("shower", "Detox Area Emergency Shower"),
]

DETOX_PEROXIDE_ITEMS: list[ItemDef] = [
    _item("carbon_screen", "Detox Carbon Safety Screen", kw=5, vendor="Kemix"),
    _item("sampler", "Detox Feed Sampler", kw=1),
    _item("sampler", "Detox Discharge Sampler", kw=1),
    _item("tank", "Peroxide Detox Reactor No. 1", comments="600 m3, lined"),
    _item("tank", "Peroxide Detox Reactor No. 2", comments="600 m3, lined"),
    _item("agitator", "Peroxide Detox Agitator No. 1", kw=110, vendor="Lightnin"),
    _item("agitator", "Peroxide Detox Agitator No. 2", kw=110, vendor="Lightnin"),
    _item("tank", "H2O2 Storage Tank", comments="50 m3, HDPE"),
    _item("dosing_pump", "H2O2 Dosing Pump 1", kw=3),
    _item("dosing_pump", "H2O2 Dosing Pump 2", kw=3, duty="Standby"),
    _item("analyzer", "Detox Cyanide Analyser", kw=5, vendor="ABB"),
    _item("pumpbox", "Tailings Thickener Feed Pumpbox"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Tailings Thickener Feed Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_sump", "Detox Area Sump Pump", kw=15),
    _item("shower", "Detox Area Emergency Shower"),
]

TSF_CONVENTIONNEL_ITEMS: list[ItemDef] = [
    _item("pump_slurry", "Tailings Distribution Pump 1", kw=350, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Tailings Distribution Pump 2", kw=350, vendor="Metso", comments="VFD", duty="Standby"),
    _item("pump_slurry", "Tailings Distribution Pump 3", kw=350, vendor="Metso", comments="VFD spare", duty="Standby"),
    _item("valve", "Tailings Cyclone / Spigot Valves", comments="Set of 20"),
    _item("pump_water", "Reclaim Water Pump 1", kw=200, vendor="Metso"),
    _item("pump_water", "Reclaim Water Pump 2", kw=200, vendor="Metso", duty="Standby"),
    _item("pump_sump", "TSF Seepage Pump 1", kw=30),
    _item("pump_sump", "TSF Seepage Pump 2", kw=30, duty="Standby"),
]

TSF_DRY_STACK_ITEMS: list[ItemDef] = [
    _item("belt_filter", "Tailings Belt Filter No. 1", kw=90, long_lead=True, vendor="Outotec", comments="3m wide"),
    _item("belt_filter", "Tailings Belt Filter No. 2", kw=90, long_lead=True, vendor="Outotec", comments="3m wide"),
    _item("belt_filter", "Tailings Belt Filter No. 3", kw=90, vendor="Outotec", comments="3m wide spare", duty="Standby"),
    _item("pump_slurry", "Filter Feed Pump 1", kw=200, vendor="Metso", comments="VFD"),
    _item("pump_slurry", "Filter Feed Pump 2", kw=200, vendor="Metso", comments="VFD", duty="Standby"),
    _item("conveyor", "Filtered Tailings Stacker Conveyor", kw=250, vendor="Martin"),
    _item("pump_water", "Filtrate Return Pump 1", kw=55),
    _item("pump_water", "Filtrate Return Pump 2", kw=55, duty="Standby"),
    _item("compressor", "Filter Cloth Wash Compressor", kw=37),
    _item("pump_sump", "Filter Area Sump Pump", kw=15),
]

BASSIN_EAU_ITEMS: list[ItemDef] = [
    _item("pump_water", "Process Water Pump No. 1", kw=200, vendor="KSB"),
    _item("pump_water", "Process Water Pump No. 2", kw=200, vendor="KSB", duty="Standby"),
    _item("pump_water", "Process Water Pump No. 3", kw=200, vendor="KSB", duty="Standby", comments="Installed spare"),
    _item("tank", "Process Water Tank", comments="5000 m3, HDPE lined"),
    _item("pump_water", "Fresh Water Pump No. 1", kw=90, vendor="Grundfos"),
    _item("pump_water", "Fresh Water Pump No. 2", kw=90, vendor="Grundfos", duty="Standby"),
    _item("tank", "Fresh / Fire Water Tank", comments="2000 m3, HDPE lined"),
    _item("pump_water", "Potable Water Pump", kw=7.5, vendor="Grundfos"),
    _item("tank", "Potable Water Treatment System", kw=5, comments="UV + chlorination"),
    _item("pump_water", "Gland Water Pump No. 1", kw=30, vendor="Grundfos"),
    _item("pump_water", "Gland Water Pump No. 2", kw=30, vendor="Grundfos", duty="Standby"),
    _item("tank", "Gland Water Tank", comments="50 m3"),
    _item("shower", "Emergency Eye Shower Package", comments="Multiple locations, emergency power"),
    _item("pump_water", "Fire Protection Jockey Pump", kw=15, vendor="Grundfos"),
    _item("pump_water", "Fire Protection Main Pump", kw=150, vendor="Grundfos", comments="Diesel backup"),
    _item("tank", "Fire Protection Skid", comments="NFPA compliant"),
]

TRAITEMENT_EFFLUENT_ITEMS: list[ItemDef] = [
    _item("tank", "Effluent Treatment Reactor", comments="200 m3"),
    _item("agitator", "Effluent Reactor Agitator", kw=30),
    _item("dosing_pump", "Effluent pH Adjustment Pump", kw=3),
    _item("dosing_pump", "Effluent Flocculant Pump", kw=3),
    _item("thickener", "Effluent Clarifier", kw=15, comments="10m dia"),
    _item("pump_slurry", "Effluent Sludge Pump", kw=15),
    _item("pump_water", "Treated Effluent Discharge Pump", kw=30),
    _item("analyzer", "Effluent pH Analyser", kw=1),
    _item("analyzer", "Effluent Turbidity Analyser", kw=1),
    _item("sampler", "Effluent Compliance Sampler", kw=1, comments="ISCO type"),
]

# --- Reagent systems (generic template + overrides) ---

def _reagent_items(reagent_name: str, vendor: str | None = None,
                   has_dust: bool = True, is_liquid: bool = False) -> list[ItemDef]:
    """Generate standard reagent system items for a given reagent."""
    items: list[ItemDef] = []
    if not is_liquid:
        items.append(_item("hopper", f"{reagent_name} Storage Hopper", comments="Bulk storage"))
        items.append(_item("screw_feeder", f"{reagent_name} Screw Feeder", kw=5.5))
    else:
        items.append(_item("tank", f"{reagent_name} Bulk Storage Tank", comments="HDPE lined"))
    items.append(_item("mixing_tank", f"{reagent_name} Mixing Tank", kw=0, comments="With agitator"))
    items.append(_item("agitator", f"{reagent_name} Mixing Tank Agitator", kw=7.5))
    items.append(_item("tank", f"{reagent_name} Day Tank", comments="24h capacity"))
    items.append(_item("dosing_pump", f"{reagent_name} Dosing Pump No. 1", kw=3, vendor=vendor))
    items.append(_item("dosing_pump", f"{reagent_name} Dosing Pump No. 2", kw=3, vendor=vendor, duty="Standby"))
    if has_dust and not is_liquid:
        items.append(_item("dust_collector", f"{reagent_name} Dust Collector", kw=15))
        items.append(_item("fan", f"{reagent_name} Dust Collector Fan", kw=7.5))
    items.append(_item("transfer_pump", f"{reagent_name} Transfer Pump", kw=5.5))
    return items


REACTIF_PAX_ITEMS = _reagent_items("PAX (Potassium Amyl Xanthate)")
REACTIF_MIBC_ITEMS = _reagent_items("MIBC Frother", is_liquid=True)
REACTIF_FLOCCULANT_ITEMS = _reagent_items("Flocculant", vendor="SNF")
REACTIF_LIME_ITEMS = _reagent_items("Lime (CaO)", vendor="Graymont")
REACTIF_NACN_ITEMS = _reagent_items("Sodium Cyanide (NaCN)", vendor="Cyanco")
REACTIF_CUSO4_ITEMS = _reagent_items("Copper Sulphate (CuSO4)")
REACTIF_NAOH_ITEMS = _reagent_items("Caustic Soda (NaOH)", is_liquid=True)
REACTIF_SO2_ITEMS = _reagent_items("SO2 / SMBS", has_dust=False)
REACTIF_OXYGEN_ITEMS = [
    _item("compressor", "Oxygen Plant Compressor", kw=500, long_lead=True, vendor="Air Liquide"),
    _item("tank", "Oxygen Buffer Tank", comments="Pressure vessel"),
    _item("valve", "Oxygen Distribution Manifold", comments="SS piping"),
    _item("analyzer", "Oxygen Purity Analyser", kw=1),
    _item("fan", "Oxygen Plant Ventilation Fan", kw=15),
]
REACTIF_CARBON_ITEMS = _reagent_items("Activated Carbon", has_dust=True)
REACTIF_ACID_ITEMS = _reagent_items("Hydrochloric Acid (HCl)", is_liquid=True)


# =============================================================================
# MASTER OPERATION -> ITEMS MAP
# =============================================================================

OP_ITEMS_MAP: dict[str, list[ItemDef]] = {
    "GIRATOIRE": GIRATOIRE_ITEMS,
    "CRIBLE": CRIBLE_ITEMS,
    "CONE": CONE_ITEMS,
    "HPGR": HPGR_ITEMS,
    "STOCKPILE": STOCKPILE_ITEMS,
    "SAG_MILL": SAG_MILL_ITEMS,
    "BALL_MILL": BALL_MILL_ITEMS,
    "ROD_MILL": BALL_MILL_ITEMS,  # similar items
    "HYDROCYCLONE": HYDROCYCLONE_ITEMS,
    "FLOTATION_ROUGHER": FLOTATION_ROUGHER_ITEMS,
    "FLOTATION_SCAVENGER": FLOTATION_SCAVENGER_ITEMS,
    "FLOTATION_CLEANER": FLOTATION_CLEANER_ITEMS,
    "FLOTATION_COLONNE": FLOTATION_COLONNE_ITEMS,
    "GRAVITE_KNELSON": GRAVITE_KNELSON_ITEMS,
    "GRAVITE_FALCON": GRAVITE_FALCON_ITEMS,
    "ISAMILL": ISAMILL_ITEMS,
    "VERTIMILL_REGRIND": VERTIMILL_REGRIND_ITEMS,
    "SMD": SMD_ITEMS,
    "EPAISSISSEUR": EPAISSISSEUR_ITEMS,
    "EPAISSISSEUR_HD": EPAISSISSEUR_HD_ITEMS,
    "EPAISSISSEUR_CONC": EPAISSISSEUR_CONC_ITEMS,
    "PREAERATION": PREAERATION_ITEMS,
    "LEACH_CUVES": LEACH_CUVES_ITEMS,
    "CIP": CIP_ITEMS,
    "CIL": CIL_ITEMS,
    "ELUTION_AARL": ELUTION_AARL_ITEMS,
    "ELUTION_ZADRA": ELUTION_ZADRA_ITEMS,
    "ELECTROWINNING": ELECTROWINNING_ITEMS,
    "FONDERIE": FONDERIE_ITEMS,
    "DETOX_INCO": DETOX_INCO_ITEMS,
    "DETOX_CARO": DETOX_CARO_ITEMS,
    "DETOX_PEROXIDE": DETOX_PEROXIDE_ITEMS,
    "TSF_CONVENTIONNEL": TSF_CONVENTIONNEL_ITEMS,
    "TSF_DRY_STACK": TSF_DRY_STACK_ITEMS,
    "BASSIN_EAU": BASSIN_EAU_ITEMS,
    "TRAITEMENT_EFFLUENT": TRAITEMENT_EFFLUENT_ITEMS,
    "REACTIF_PAX": REACTIF_PAX_ITEMS,
    "REACTIF_MIBC": REACTIF_MIBC_ITEMS,
    "REACTIF_FLOCCULANT": REACTIF_FLOCCULANT_ITEMS,
    "REACTIF_LIME": REACTIF_LIME_ITEMS,
    "REACTIF_NACN": REACTIF_NACN_ITEMS,
    "REACTIF_CUSO4": REACTIF_CUSO4_ITEMS,
    "REACTIF_NAOH": REACTIF_NAOH_ITEMS,
    "REACTIF_SO2": REACTIF_SO2_ITEMS,
    "REACTIF_OXYGEN": REACTIF_OXYGEN_ITEMS,
    "REACTIF_CARBON": REACTIF_CARBON_ITEMS,
    "REACTIF_ACID": REACTIF_ACID_ITEMS,
}


# =============================================================================
# RESOLVE KW FROM DESIGN CRITERIA OR LITERAL
# =============================================================================

def _resolve_kw(kw_spec: Any, dc_map: dict[str, float]) -> float:
    """
    Resolve installed kW from a spec.

    kw_spec can be:
      - int/float: literal value
      - str "dc:<field>|<default>": look up from design criteria map
    """
    if isinstance(kw_spec, (int, float)):
        return float(kw_spec)
    if isinstance(kw_spec, str) and kw_spec.startswith("dc:"):
        rest = kw_spec[3:]
        field, _, default_str = rest.partition("|")
        default_val = float(default_str) if default_str else 0.0
        return dc_map.get(field.strip(), default_val)
    return 0.0


# =============================================================================
# TAG GENERATION
# =============================================================================

def _make_tag(bu: str, wbs: str, eq_type_code: str, seq: int) -> str:
    """Generate equipment tag: {BU}{WBS}{EQ_TYPE}{SEQ:03d}"""
    return f"{bu}{wbs}{eq_type_code}{seq:03d}"


# =============================================================================
# MAIN GENERATOR
# =============================================================================

def generate_mer(project_id: str, template_id: str, cursor) -> dict:
    """
    Generate full MER from enabled circuit operations.

    Steps:
    1. Read enabled operations from circuit_operations
    2. Read design criteria for sizing parameters
    3. For each operation, generate equipment items (principal + auxiliaries)
    4. Assign WBS codes, equipment tags, sequential numbering
    5. Insert into equipment_v2
    6. Return summary

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        cursor: psycopg2 cursor (caller manages connection + commit)

    Returns:
        {items_created: int, total_kw: float, total_capex_cad: float,
         by_wbs: [{wbs, description, count, kw, capex}]}
    """
    try:
        return _generate_mer_impl(project_id, template_id, cursor)
    except Exception as e:
        logger.error("generate_mer failed for project_id=%s, template_id=%s: %s",
                     project_id, template_id, e)
        raise RuntimeError(f"generate_mer failed for project {project_id}: {e}") from e


def _generate_mer_impl(project_id: str, template_id: str, cursor) -> dict:
    """Internal implementation of generate_mer."""
    bu = "47"  # Default business unit

    # -----------------------------------------------------------------
    # 1. Clear existing equipment for this project
    # -----------------------------------------------------------------
    cursor.execute(
        "DELETE FROM equipment_v2 WHERE project_id = %s",
        (project_id,),
    )
    logger.info("Cleared existing equipment_v2 for project %s", project_id)

    # -----------------------------------------------------------------
    # 2. Read enabled operations, sorted by sort_order
    # -----------------------------------------------------------------
    cursor.execute(
        "SELECT op_code, sort_order "
        "FROM circuit_operations "
        "WHERE template_id = %s AND enabled = TRUE "
        "ORDER BY sort_order",
        (template_id,),
    )
    operations = cursor.fetchall()
    if not operations:
        logger.warning("No enabled operations for template %s", template_id)
        return {
            "items_created": 0,
            "total_kw": 0.0,
            "total_capex_cad": 0.0,
            "by_wbs": [],
        }

    # -----------------------------------------------------------------
    # 3. Read design criteria for power / sizing lookups
    # -----------------------------------------------------------------
    dc_map: dict[str, float] = {}
    try:
        cursor.execute("SAVEPOINT dc_read")
        cursor.execute(
            "SELECT item, design_value FROM design_criteria_v2 "
            "WHERE project_id = %s AND enabled = TRUE AND design_value IS NOT NULL",
            (project_id,),
        )
        for row in cursor.fetchall():
            try:
                item_key = row["item"] if isinstance(row, dict) else row[0]
                design_val = row["design_value"] if isinstance(row, dict) else row[1]
                dc_map[item_key] = float(design_val)
            except (ValueError, TypeError, IndexError):
                pass
        cursor.execute("RELEASE SAVEPOINT dc_read")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT dc_read")
        logger.debug("Could not read design_criteria_v2, using defaults")

    # -----------------------------------------------------------------
    # 4. Build all equipment rows
    # -----------------------------------------------------------------
    # Track sequence counters per (wbs, eq_type_code)
    seq_counters: dict[tuple[str, str], int] = defaultdict(int)
    global_item_no = 0
    rows_to_insert: list[tuple] = []

    # WBS-level aggregation
    wbs_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "kw": 0.0, "capex": 0.0}
    )

    for op_row in operations:
        op_code = op_row["op_code"] if isinstance(op_row, dict) else op_row[0]
        # op_params could hold overrides (future use)
        wbs = OP_WBS_MAP.get(op_code)
        if wbs is None:
            logger.warning("No WBS mapping for operation %s, skipping", op_code)
            continue

        wbs_desc = WBS_DESC.get(wbs, "Unknown")
        item_defs = OP_ITEMS_MAP.get(op_code)
        if item_defs is None:
            logger.warning("No item definitions for operation %s, skipping", op_code)
            continue

        for item_def in item_defs:
            eq_type, name, qty, duty, kw_spec, is_long_lead, vendor, comments, price = item_def

            # Resolve eq_type code
            eq_type_code = EQ_TYPES.get(eq_type, "EQP")

            # Resolve kW
            installed_kw = _resolve_kw(kw_spec, dc_map)

            # Has VFD?
            has_vfd = bool(comments and "VFD" in comments.upper()) if comments else False

            # Emergency power?
            emergency_power = bool(
                comments and "emergency power" in comments.lower()
            ) if comments else False

            # Sequential tag
            seq_key = (wbs, eq_type_code)
            seq_counters[seq_key] += 1
            seq_no = seq_counters[seq_key]
            tag = _make_tag(bu, wbs, eq_type_code, seq_no)

            global_item_no += 1

            row = (
                str(uuid.uuid4()),   # id
                project_id,          # project_id
                template_id,         # template_id
                op_code,             # op_code
                global_item_no,      # item_number
                wbs,                 # wbs_code
                wbs_desc,            # wbs_description
                None,                # pfd_drawing
                "A",                 # revision
                None,                # initials
                bu,                  # bu
                eq_type_code,        # eq_type
                f"{seq_no:03d}",     # seq_no
                tag,                 # equipment_tag
                name,                # equipment_name
                qty,                 # quantity
                None,                # description
                comments,            # comments
                None,                # specifications
                has_vfd,             # has_vfd
                duty,                # duty_status
                installed_kw,        # installed_kw
                emergency_power,     # emergency_power
                vendor,              # vendor
                price or EQ_TYPE_DEFAULT_PRICES.get(eq_type_code, 0),  # price_cad (use default if not explicit)
                None,                # installation_hours
                None,                # reference_doc
                is_long_lead,        # is_long_lead
                None,                # lead_time_weeks
                None,                # weight_kg
                None,                # material
                True,                # enabled
                global_item_no,      # sort_order
                1,                   # version
            )
            rows_to_insert.append(row)

            # Aggregate stats
            actual_price = price or EQ_TYPE_DEFAULT_PRICES.get(eq_type_code, 0)
            wbs_stats[wbs]["count"] += 1
            wbs_stats[wbs]["kw"] += installed_kw
            wbs_stats[wbs]["capex"] += actual_price

    # -----------------------------------------------------------------
    # 5. Batch insert
    # -----------------------------------------------------------------
    if rows_to_insert:
        insert_sql = """
            INSERT INTO equipment_v2 (
                id, project_id, template_id, op_code, item_number,
                wbs_code, wbs_description, pfd_drawing, revision, initials,
                bu, eq_type, seq_no, equipment_tag, equipment_name,
                quantity, description, comments, specifications,
                has_vfd, duty_status, installed_kw, emergency_power,
                vendor, price_cad, installation_hours, reference_doc,
                is_long_lead, lead_time_weeks, weight_kg, material,
                enabled, sort_order, version
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
        """
        psycopg2.extras.execute_batch(cursor, insert_sql, rows_to_insert, page_size=100)
        logger.info("Inserted %d equipment items for project %s", len(rows_to_insert), project_id)

    # -----------------------------------------------------------------
    # 6. Build summary
    # -----------------------------------------------------------------
    total_kw = sum(s["kw"] for s in wbs_stats.values())
    total_capex = sum(s["capex"] for s in wbs_stats.values())

    by_wbs = []
    for wbs_code in sorted(wbs_stats.keys()):
        s = wbs_stats[wbs_code]
        by_wbs.append({
            "wbs": wbs_code,
            "description": WBS_DESC.get(wbs_code, "Unknown"),
            "count": s["count"],
            "kw": round(s["kw"], 1),
            "capex": round(s["capex"], 2),
        })

    return {
        "items_created": len(rows_to_insert),
        "total_kw": round(total_kw, 1),
        "total_capex_cad": round(total_capex, 2),
        "by_wbs": by_wbs,
    }


# =============================================================================
# UTILITY: count items without DB (for testing / validation)
# =============================================================================

def count_items_for_ops(op_codes: list[str]) -> dict:
    """
    Count how many MER items would be generated for a list of operations.
    Useful for validation without a database connection.
    """
    try:
        total = 0
        by_op: dict[str, int] = {}
        for op in op_codes:
            items = OP_ITEMS_MAP.get(op, [])
            by_op[op] = len(items)
            total += len(items)
        return {"total": total, "by_operation": by_op}
    except Exception as e:
        logger.error("count_items_for_ops failed for op_codes=%s: %s", op_codes, e)
        return {"total": 0, "by_operation": {}}


# =============================================================================
# CLI / quick test
# =============================================================================

if __name__ == "__main__":
    print(f"Operations mapped: {len(OP_WBS_MAP)}")
    print(f"Operations with item definitions: {len(OP_ITEMS_MAP)}")

    # Count total items across all operations
    all_ops = list(OP_ITEMS_MAP.keys())
    summary = count_items_for_ops(all_ops)
    print(f"Total items across all operations: {summary['total']}")
    print("\nItems per operation:")
    for op, cnt in sorted(summary["by_operation"].items()):
        wbs = OP_WBS_MAP.get(op, "???")
        print(f"  {op:30s}  WBS {wbs}  ->  {cnt:3d} items")
