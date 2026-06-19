import pytest
from models import FeedOverride, RunByOpsRequest, RunBySectionsRequest, RunSuggestedRequest


class TestFeedOverride:
    def test_valid_feed(self):
        f = FeedOverride(solids_tph=1500, au_g_t=1.8, p80_um=3000, pct_solids=50)
        assert f.solids_tph == 1500

    def test_rejects_negative_tph(self):
        with pytest.raises(Exception):
            FeedOverride(solids_tph=-1, au_g_t=1.8, p80_um=3000, pct_solids=50)

    def test_rejects_pct_solids_over_100(self):
        with pytest.raises(Exception):
            FeedOverride(solids_tph=100, au_g_t=1.0, p80_um=75, pct_solids=110)


class TestRunByOpsRequest:
    def test_valid_request(self):
        r = RunByOpsRequest(op_codes=["SAG_MILL", "BALL_MILL"])
        assert len(r.op_codes) == 2
        assert r.feed_override is None
        assert r.label is None

    def test_rejects_empty_op_codes(self):
        with pytest.raises(Exception):
            RunByOpsRequest(op_codes=[])

    def test_with_feed_override(self):
        r = RunByOpsRequest(
            op_codes=["CIL"],
            feed_override={"solids_tph": 500, "au_g_t": 2.0, "p80_um": 75, "pct_solids": 45},
            label="test run",
        )
        assert r.feed_override.solids_tph == 500
        assert r.label == "test run"


class TestRunBySectionsRequest:
    def test_valid_sections(self):
        r = RunBySectionsRequest(sections=["comminution", "leaching"])
        assert r.sections == ["comminution", "leaching"]

    def test_rejects_empty_sections(self):
        with pytest.raises(Exception):
            RunBySectionsRequest(sections=[])


class TestRunSuggestedRequest:
    def test_valid_request(self):
        r = RunSuggestedRequest(suggestion_id="auto_hpgr_swap")
        assert r.run_mode == "global"

    def test_valid_section_mode(self):
        r = RunSuggestedRequest(suggestion_id="auto_cip", run_mode="section")
        assert r.run_mode == "section"

    def test_rejects_invalid_mode(self):
        with pytest.raises(Exception):
            RunSuggestedRequest(suggestion_id="x", run_mode="invalid")
