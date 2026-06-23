from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.dc_calculator import recalculate_all
except ImportError:  # pragma: no cover
    from engines.dc_calculator import recalculate_all  # type: ignore[no-redef]


class FakeCursor:
    def __init__(self):
        self.last = []
        self.updates: dict[str, float] = {}
        self.nominal_updates: dict[str, float] = {}
        self.rowcount = 1
        self.rows = [
            ("r1", "GIRATOIRE", "1.1.02", "Débit design alimentation", "t/h", 1800, 1800, None, None, "P"),
            ("r2", "GIRATOIRE", "1.1.07", "Ratio de réduction R80 = F80/P80", "-", 3.9, 3.9, None, None, "C"),
            ("r3", "GIRATOIRE", "1.1.09", "Énergie Bond W = 10·Wi·(1/√P80 - 1/√F80)", "kWh/t", 0.6, 0.6, None, None, "C"),
            ("r4", "GIRATOIRE", "1.1.10", "Puissance arbre P_shaft = W × débit", "kW", 1080, 1080, None, None, "C"),
            ("r5", "GIRATOIRE", "1.1.13", "PUISSANCE INSTALLÉE moteur", "kW", 1350, 1350, None, None, "C"),
            ("r6", "GIRATOIRE", "1.1.03", "F80 alimentation (ROM)", "µm", 528000, 528000, None, None, "D"),
            ("r7", "GIRATOIRE", "1.1.04", "F100 alimentation (top size)", "µm", 1000000, 1000000, None, None, "C"),
            ("r8", "GIRATOIRE", "1.1.06", "P80 produit", "µm", 135000, 135000, None, None, "C"),
            ("r9", "GIRATOIRE", "1.1.08", "Bond CWi (Crushing Work Index)", "kWh/t", 17.73, 14, None, None, "L"),
            ("r10", "GIRATOIRE", "1.1.11", "Rendement mécanique η_mech", "%", 92, 92, None, None, "D"),
            ("r11", "GIRATOIRE", "1.1.12", "Marge installation k_install", "%", 15, 15, None, None, "D"),
            ("r12", "BALL_MILL", "3.1.01", "Operating Percentage - Plant", "%", 92, 92, None, None, "D"),
            ("r13", "GENERAL", "1.2.2", "Concentrator plant equipment design factor", "%", 15, 15, None, None, "D"),
            ("r14", "GENERAL", "1.1.09", "Operating Percentage - Crushing Circuit", "%", 75, 75, None, None, "D"),
            ("r15", "CRIBLE", "2.3.07", "% passant à la coupure", "%", 65, 65, None, None, "D"),
            ("r16", "CONE", "2.2.03", "Débit alim. (oversize crible)", "t/h", 630, 630, None, None, "D"),
            ("r17", "CONE", "2.2.09", "Puissance arbre (W × débit oversize)", "kW", 880, 820, None, None, "C"),
            ("r18", "HPGR", "2.5.01", "Débit fresh feed (design)", "t/h", 1500, 1500, None, None, "P"),
            ("r19", "HPGR", "2.5.02", "Recycle ratio (edge + crible)", "%", 25, 25, None, None, "D"),
            ("r20", "HPGR", "2.5.03", "Débit total roll (incl. recycle)", "t/h", 1875, 1875, None, None, "C"),
            ("r21", "HPGR", "2.5.05", "P80 produit (post-crible, cible)", "µm", 6500, 6500, None, None, "D"),
            ("r22", "BALL_MILL", "3.1.01", "Débit alimentation (Design throughput)", "t/h", 1500, 1500, None, None, "P"),
            ("r23", "BALL_MILL", "3.1.02", "F80 alimentation (≈0.75×P80 HPGR)", "µm", 4500, 4500, None, None, "D"),
            ("r24", "BALL_MILL", "3.1.03", "P80 cible (Cyclone OF)", "µm", 90, 90, None, None, "D"),
            ("r25", "VERTIMILL", "3.4.01", "Débit alimentation (Cyclone OF primaire)", "t/h", 1500, 1500, None, None, "D"),
            ("r26", "VERTIMILL", "3.4.02", "F80 alimentation (P80 ball mill)", "µm", 100, 100, None, None, "D"),
            ("r27", "VERTIMILL", "3.4.03", "P80 cible (Cyclone OF secondaire)", "µm", 38, 38, None, None, "D"),
            ("r28", "VERTIMILL_REGRIND", "5.2.01", "Regrind circuit feed", "t/h", 95, 95, None, None, "C"),
            ("r29", "HYDROCYCLONE", "3.3.01", "Débit feed cyclone", "t/h", 6200, 6200, None, None, "C"),
            ("r30", "HYDROCYCLONE", "3.3.02", "Débit overflow", "t/h", 1800, 1800, None, None, "C"),
            ("r31", "LEACH_CUVES", "7.1.01", "Solid - feed", "t/h", 110, 110, None, None, "C"),
            ("r32", "LEACH_CUVES", "7.1.02", "Feed leach", "t/h", 110, 110, None, None, "C"),
            ("r33", "LEACH_CUVES", "7.1.03", "Processing circuit rate", "t/h", 110, 110, None, None, "C"),
            ("r34", "CIP", "7.2.01", "Solid - feed", "t/h", 110, 110, None, None, "C"),
            ("r35", "CIP", "7.2.02", "Feed CIP tanks", "t/h", 110, 110, None, None, "C"),
            ("r36", "DETOX_INCO", "9.1.01", "Circuit feed", "t/h", 110, 110, None, None, "C"),
            ("r37", "EPAISSISSEUR", "9.2.01", "Feed rate", "t/h", 1600, 1600, None, None, "C"),
        ]

    def execute(self, sql, params=None):
        self.last = [sql, params]
        if "FROM design_criteria_v2" in sql:
            self._result = self.rows
        elif "FROM projects" in sql:
            self._result = [(1596, 1.5, 92, 22.1, 10, 2340)]
        elif "UPDATE design_criteria_v2" in sql:
            value = params[0]
            row_id = params[-2]
            self.updates[row_id] = value
            if "nominal_value = %s" in sql:
                self.nominal_updates[row_id] = params[1]
            self._result = []
            self.rowcount = 1
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


def test_recalculate_all_applies_engineer_workbook_crusher_design_rate_and_power():
    cur = FakeCursor()

    result = recalculate_all("project-1", "template-1", cur)

    assert result["errors"] == []
    assert cur.updates["r1"] == pytest.approx(2251.424, abs=0.001)
    assert cur.updates["r2"] == pytest.approx(528000 / 135000, abs=0.001)
    assert cur.updates["r3"] == pytest.approx(0.2394, abs=0.001)
    assert cur.updates["r4"] == pytest.approx(537.1, abs=0.2)
    assert cur.updates["r5"] == pytest.approx(671.3, abs=0.3)


def test_recalculate_all_updates_secondary_crusher_design_and_nominal_oversize():
    cur = FakeCursor()

    result = recalculate_all("project-1", "template-1", cur)

    assert result["errors"] == []
    expected_design_crush = 1596 * 1.15 * 0.92 / 0.75
    expected_nominal_crush = 1596 * 0.92 / 0.75
    assert cur.updates["r16"] == pytest.approx(expected_design_crush * 0.35, abs=0.1)
    assert cur.nominal_updates["r16"] == pytest.approx(expected_nominal_crush * 0.35, abs=0.1)


def test_recalculate_all_propagates_nominal_and_psd_through_hpgr_ball_vertimill():
    cur = FakeCursor()

    result = recalculate_all("project-1", "template-1", cur)

    assert result["errors"] == []
    mill_design = 1596 * 1.15
    assert cur.updates["r18"] == pytest.approx(mill_design, abs=0.1)
    assert cur.nominal_updates["r18"] == pytest.approx(1596, abs=0.1)
    assert cur.updates["r20"] == pytest.approx(mill_design * 1.25, abs=0.1)
    assert cur.nominal_updates["r20"] == pytest.approx(1596 * 1.25, abs=0.1)
    assert cur.updates["r22"] == pytest.approx(mill_design, abs=0.1)
    assert cur.nominal_updates["r22"] == pytest.approx(1596, abs=0.1)
    assert cur.updates["r23"] == pytest.approx(6500 * 0.75, abs=0.1)
    assert cur.updates["r24"] == pytest.approx(90, abs=0.1)
    assert cur.updates["r25"] == pytest.approx(mill_design, abs=0.1)
    assert cur.nominal_updates["r25"] == pytest.approx(1596, abs=0.1)
    assert cur.updates["r26"] == pytest.approx(90, abs=0.1)
    assert cur.updates["r28"] == pytest.approx(mill_design * 0.06, abs=0.1)
    assert cur.nominal_updates["r28"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r29"] == pytest.approx(1596 * 4.5, abs=0.2)
    assert cur.nominal_updates["r30"] == pytest.approx(1596, abs=0.1)
    assert cur.nominal_updates["r31"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r32"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r33"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r34"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r35"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r36"] == pytest.approx(1596 * 0.06, abs=0.1)
    assert cur.nominal_updates["r37"] == pytest.approx(1596, abs=0.1)


def test_recalculate_all_accepts_realdictcursor_rows_from_routes():
    cur = FakeCursor()
    tuple_rows = cur.rows
    keys = [
        "id", "op_code", "ref_number", "item", "unit", "design_value",
        "nominal_value", "min_value", "max_value", "source_code",
    ]
    cur.rows = [dict(zip(keys, row)) for row in tuple_rows]

    result = recalculate_all("project-1", "template-1", cur)

    assert result["errors"] == []
    assert cur.updates["r1"] == pytest.approx(2251.424, abs=0.001)
