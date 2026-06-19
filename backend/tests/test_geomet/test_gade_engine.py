"""
Tests unitaires et property-based pour gade_engine.py.

Tâches couvertes : 4.1, 4.2, 4.3

Validates: Requirements 2.1, 2.3, 2.8, 3.1, 3.3
"""
from __future__ import annotations

import math
import sys
import os
import pytest

# Support import both as backend.engines.gade_engine and engines.gade_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    from backend.engines.gade_engine import (
        DomainizingConfig,
        run_geometallurgical_domaining,
        train_domain_recovery_model,
        predict_domain_for_samples,
        _extract_feature_matrix,
        _knn_impute,
        _normalize,
        _apply_weights,
        _classify_ore_type,
        _cosine_similarity_to_domain,
    )
except ImportError:
    from engines.gade_engine import (
        DomainizingConfig,
        run_geometallurgical_domaining,
        train_domain_recovery_model,
        predict_domain_for_samples,
        _extract_feature_matrix,
        _knn_impute,
        _normalize,
        _apply_weights,
        _classify_ore_type,
        _cosine_similarity_to_domain,
    )

import numpy as np

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_sample(
    au_grade: float = 1.0,
    fe_pct: float = 5.0,
    s_sulphide_pct: float = 1.0,
    as_ppm: float = 100.0,
    carbon_organic_pct: float = 0.05,
    cu_ppm: float = 50.0,
    bwi_ball: float = 14.0,
    sg: float = 2.75,
    au_recovery: float = 0.90,
    cn_consumption: float = 0.5,
) -> dict:
    return {
        "au_grade": au_grade,
        "fe_pct": fe_pct,
        "s_sulphide_pct": s_sulphide_pct,
        "as_ppm": as_ppm,
        "carbon_organic_pct": carbon_organic_pct,
        "cu_ppm": cu_ppm,
        "bwi_ball": bwi_ball,
        "sg": sg,
        "au_recovery": au_recovery,
        "cn_consumption": cn_consumption,
    }


def _make_samples_two_clusters(n: int = 40) -> list[dict]:
    """Génère deux clusters bien séparés (n/2 chacun)."""
    rng = np.random.RandomState(42)
    samples = []
    for i in range(n // 2):
        samples.append(_make_sample(
            au_grade=float(rng.normal(0.5, 0.1)),
            bwi_ball=float(rng.normal(10.0, 0.5)),
            s_sulphide_pct=float(rng.normal(0.5, 0.1)),
            au_recovery=float(rng.normal(0.88, 0.02)),
        ))
    for i in range(n // 2):
        samples.append(_make_sample(
            au_grade=float(rng.normal(3.0, 0.2)),
            bwi_ball=float(rng.normal(18.0, 0.5)),
            s_sulphide_pct=float(rng.normal(3.5, 0.2)),
            au_recovery=float(rng.normal(0.72, 0.03)),
        ))
    return samples


# ---------------------------------------------------------------------------
# Tests Tâche 4.1 — Pipeline K-Means
# ---------------------------------------------------------------------------

class TestRunGeometallurgicalDomaining:

    def test_returns_dict_with_required_keys(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="kmeans",
            features_used=["au_grade", "bwi_ball", "s_sulphide_pct", "au_recovery"],
        )
        result = run_geometallurgical_domaining(samples, config)
        required_keys = [
            "domains", "labels", "silhouette_score", "davies_bouldin_score",
            "n_samples_used", "n_domains_found", "viz_pca", "viz_tsne", "viz_umap",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_insufficient_data_returns_status(self):
        result = run_geometallurgical_domaining([_make_sample()] * 2)
        assert result["status"] == "insufficient_data"
        assert result["domains"] == []

    def test_labels_length_matches_n_samples_used(self):
        """Property P2 : len(labels) == n_samples_used."""
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        assert len(result["labels"]) == result["n_samples_used"]

    def test_domain_n_samples_sum_equals_n_samples_used(self):
        """Property P2 : sum(domain.n_samples) == n_samples_used."""
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        total = sum(d["n_samples"] for d in result["domains"])
        assert total == result["n_samples_used"]

    def test_domain_pct_sum_approx_100(self):
        """Property P3 : sum(domain.pct_of_total) ≈ 100."""
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        total_pct = sum(d["pct_of_total"] for d in result["domains"])
        assert abs(total_pct - 100.0) < 0.5, f"pct sum = {total_pct}"

    def test_silhouette_score_in_range(self):
        """Property P1 : silhouette_score ∈ [-1, 1]."""
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        s = result["silhouette_score"]
        assert -1.0 <= s <= 1.0, f"silhouette = {s}"

    def test_domain_codes_unique(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        codes = [d["domain_code"] for d in result["domains"]]
        assert len(codes) == len(set(codes))

    def test_domain_colors_are_hex(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        for d in result["domains"]:
            color = d.get("color", "")
            assert color.startswith("#"), f"Invalid color: {color}"
            assert len(color) == 7

    def test_viz_pca_present_always(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        assert result["viz_pca"] is not None
        assert len(result["viz_pca"]) > 0

    def test_viz_pca_has_required_fields(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        for pt in result["viz_pca"][:5]:
            assert "x" in pt
            assert "y" in pt
            assert "domain_id" in pt
            assert "sample_code" in pt

    def test_domain_statistics_has_stats(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="kmeans",
            features_used=["au_grade", "bwi_ball"],
        )
        result = run_geometallurgical_domaining(samples, config)
        for d in result["domains"]:
            stats = d["statistics"]
            for feat in ["au_grade", "bwi_ball"]:
                if feat in stats:
                    assert "mean" in stats[feat]
                    assert "std" in stats[feat]
                    assert "median" in stats[feat]
                    assert "p10" in stats[feat]
                    assert "p90" in stats[feat]

    def test_metallurgical_signature_present(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        for d in result["domains"]:
            sig = d["metallurgical_signature"]
            assert "ore_type_classification" in sig
            assert "processing_route_recommendation" in sig
            assert "avg_au_recovery" in sig
            assert "avg_bwi" in sig

    def test_discriminating_features_present(self):
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples)
        for d in result["domains"]:
            feats = d.get("discriminating_features", [])
            assert isinstance(feats, list)
            if feats:
                for f in feats:
                    assert "feature" in f
                    assert "importance_score" in f
                    assert "direction" in f
                    assert f["direction"] in ("high", "low")

    def test_config_as_dict(self):
        """L'engine accepte un dict en config."""
        samples = _make_samples_two_clusters(40)
        result = run_geometallurgical_domaining(samples, {
            "algorithm": "kmeans",
            "features_used": ["au_grade", "bwi_ball"],
        })
        assert result["n_domains_found"] >= 1

    def test_n_domains_requested_respected(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="kmeans",
            features_used=["au_grade", "bwi_ball"],
            n_domains_requested=3,
        )
        result = run_geometallurgical_domaining(samples, config)
        assert result["n_domains_found"] == 3

    def test_normalization_robust(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(normalization="robust")
        result = run_geometallurgical_domaining(samples, config)
        assert result["n_domains_found"] >= 1

    def test_normalization_minmax(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(normalization="minmax")
        result = run_geometallurgical_domaining(samples, config)
        assert result["n_domains_found"] >= 1

    def test_missing_values_handled(self):
        """Samples avec valeurs None — l'engine doit gérer sans planter."""
        samples = []
        rng = np.random.RandomState(0)
        for i in range(30):
            s = _make_sample(au_grade=float(rng.normal(1.0 if i < 15 else 3.0, 0.2)))
            if i % 5 == 0:
                s["bwi_ball"] = None
            samples.append(s)
        result = run_geometallurgical_domaining(samples)
        assert result["n_samples_used"] >= 0


# ---------------------------------------------------------------------------
# Tests Tâche 4.2 — GMM et HDBSCAN
# ---------------------------------------------------------------------------

class TestAlternativeAlgorithms:

    def test_gmm_algorithm(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="gmm",
            features_used=["au_grade", "bwi_ball", "s_sulphide_pct"],
        )
        result = run_geometallurgical_domaining(samples, config)
        assert result["n_domains_found"] >= 1
        assert len(result["domains"]) >= 1

    def test_gmm_pct_sum_approx_100(self):
        """Property P3 pour GMM."""
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="gmm",
            features_used=["au_grade", "bwi_ball"],
        )
        result = run_geometallurgical_domaining(samples, config)
        total_pct = sum(d["pct_of_total"] for d in result["domains"])
        assert abs(total_pct - 100.0) < 0.5

    def test_hdbscan_fallback_or_ok(self):
        """HDBSCAN ou fallback K-Means — le résultat doit être cohérent."""
        samples = _make_samples_two_clusters(50)
        config = DomainizingConfig(
            algorithm="hdbscan",
            features_used=["au_grade", "bwi_ball", "s_sulphide_pct"],
        )
        result = run_geometallurgical_domaining(samples, config)
        # Vérifier cohérence de base
        assert result["n_domains_found"] >= 1
        # n_noise_samples présent pour hdbscan
        assert "n_noise_samples" in result or result["n_domains_found"] >= 1

    def test_hdbscan_n_noise_key(self):
        """Quand HDBSCAN est utilisé, n_noise_samples doit être dans le résultat."""
        samples = _make_samples_two_clusters(50)
        config = DomainizingConfig(algorithm="hdbscan")
        result = run_geometallurgical_domaining(samples, config)
        assert "n_noise_samples" in result

    def test_gmm_with_n_domains_requested(self):
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="gmm",
            n_domains_requested=2,
            features_used=["au_grade", "bwi_ball"],
        )
        result = run_geometallurgical_domaining(samples, config)
        assert result["n_domains_found"] == 2


# ---------------------------------------------------------------------------
# Tests Tâche 4.3 — train_domain_recovery_model
# ---------------------------------------------------------------------------

class TestTrainDomainRecoveryModel:

    def _make_domain_samples(self, n: int = 30, target_range: tuple = (0.8, 0.95)) -> list[dict]:
        rng = np.random.RandomState(42)
        samples = []
        for i in range(n):
            rec = float(rng.uniform(*target_range))
            samples.append({
                "au_grade": float(rng.normal(1.5, 0.3)),
                "fe_pct": float(rng.normal(5.0, 0.5)),
                "s_sulphide_pct": float(rng.normal(1.2, 0.2)),
                "as_ppm": float(rng.normal(150, 20)),
                "carbon_organic_pct": float(rng.normal(0.05, 0.01)),
                "cu_ppm": float(rng.normal(60, 10)),
                "bwi_ball": float(rng.normal(13.0, 1.0)),
                "sg": float(rng.normal(2.75, 0.1)),
                "au_recovery": rec,
                "cn_consumption": float(rng.normal(0.5, 0.05)),
            })
        return samples

    def test_returns_none_if_less_than_10(self):
        """Property P6 : len < 10 → no RecoveryModel."""
        for n in range(0, 10):
            result = train_domain_recovery_model(
                [_make_sample()] * n,
                target="au_recovery",
            )
            assert result is None, f"Expected None for n={n}, got {result}"

    def test_returns_dict_for_sufficient_samples(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        for key in ["model_type", "test_r2", "test_rmse", "test_mae",
                    "cross_val_scores", "feature_importances", "model_artifact_path"]:
            assert key in result, f"Missing key: {key}"

    def test_r2_in_valid_range(self):
        """Property P5 : test_r2 ∈ [-1, 1]."""
        samples = self._make_domain_samples(40)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        r2 = result["test_r2"]
        assert -1.0 <= r2 <= 1.0, f"R2 = {r2} out of range"

    def test_rmse_nonnegative(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        assert result["test_rmse"] >= 0.0

    def test_mae_nonnegative(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        assert result["test_mae"] >= 0.0

    def test_feature_importances_sum_approx_1(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        fi = result["feature_importances"]
        total = sum(fi.values())
        assert abs(total - 1.0) < 0.05, f"Feature importances sum = {total}"

    def test_cross_val_scores_list(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery")
        assert result is not None
        assert isinstance(result["cross_val_scores"], list)

    def test_model_type_random_forest(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, model_type="random_forest")
        assert result is not None
        assert result["model_type"] == "random_forest"

    def test_model_type_xgboost_fallback(self):
        """XGBoost ou fallback RF — le résultat est un dict valide."""
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, model_type="xgboost")
        assert result is not None
        assert result["model_type"] in ("xgboost", "random_forest")

    def test_target_cn_consumption(self):
        rng = np.random.RandomState(1)
        samples = [{
            **_make_sample(),
            "cn_consumption": float(rng.uniform(0.3, 0.9)),
        } for _ in range(30)]
        result = train_domain_recovery_model(samples, target="cn_consumption")
        assert result is not None
        assert -1.0 <= result["test_r2"] <= 1.0

    def test_model_artifact_created_or_none(self):
        """L'artefact joblib doit être créé (ou None si joblib absent)."""
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, target="au_recovery", domain_code="DOM-TEST")
        assert result is not None
        # Soit un chemin de fichier, soit None si joblib absent
        artifact = result["model_artifact_path"]
        if artifact is not None:
            assert os.path.exists(artifact), f"Artifact not found: {artifact}"

    def test_k_folds_parameter(self):
        samples = self._make_domain_samples(30)
        result = train_domain_recovery_model(samples, k_folds=3)
        assert result is not None
        # cross_val_scores devrait avoir 3 éléments si possible
        if result["cross_val_scores"]:
            assert len(result["cross_val_scores"]) <= 3


# ---------------------------------------------------------------------------
# Tests predict_domain_for_samples
# ---------------------------------------------------------------------------

class TestPredictDomainForSamples:

    def _make_session_result(self) -> dict:
        samples = _make_samples_two_clusters(40)
        config = DomainizingConfig(
            algorithm="kmeans",
            features_used=["au_grade", "bwi_ball"],
            n_domains_requested=2,
        )
        return run_geometallurgical_domaining(samples, config)

    def test_returns_list(self):
        session = self._make_session_result()
        new_samples = [_make_sample()] * 5
        result = predict_domain_for_samples(new_samples, session)
        assert isinstance(result, list)
        assert len(result) == 5

    def test_sample_idx_sequential(self):
        session = self._make_session_result()
        new_samples = [_make_sample()] * 3
        result = predict_domain_for_samples(new_samples, session)
        for i, r in enumerate(result):
            assert r["sample_idx"] == i

    def test_domain_id_valid(self):
        session = self._make_session_result()
        n_domains = session["n_domains_found"]
        new_samples = [_make_sample()] * 10
        result = predict_domain_for_samples(new_samples, session)
        for r in result:
            assert 0 <= r["domain_id"] < n_domains

    def test_confidence_in_range(self):
        """Property P4 : confidence ∈ [0, 1]."""
        session = self._make_session_result()
        new_samples = [_make_sample()] * 10
        result = predict_domain_for_samples(new_samples, session)
        for r in result:
            c = r["confidence"]
            assert 0.0 <= c <= 1.0, f"confidence = {c}"

    def test_predicted_recovery_present(self):
        session = self._make_session_result()
        new_samples = [_make_sample()]
        result = predict_domain_for_samples(new_samples, session)
        assert len(result) == 1
        assert "predicted_recovery" in result[0]
        assert "predicted_cn" in result[0]
        assert "predicted_bwi" in result[0]

    def test_empty_samples(self):
        session = self._make_session_result()
        result = predict_domain_for_samples([], session)
        assert result == []

    def test_empty_session(self):
        result = predict_domain_for_samples(
            [_make_sample()], {"domains": []}
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests des fonctions utilitaires
# ---------------------------------------------------------------------------

class TestOreTypeClassification:

    def test_free_milling(self):
        stats = {
            "as_ppm": {"mean": 50},
            "s_sulphide_pct": {"mean": 0.5},
            "carbon_organic_pct": {"mean": 0.01},
        }
        assert _classify_ore_type(stats) == "free_milling"

    def test_partially_refractory_high_as(self):
        stats = {
            "as_ppm": {"mean": 600},
            "s_sulphide_pct": {"mean": 0.5},
            "carbon_organic_pct": {"mean": 0.01},
        }
        assert _classify_ore_type(stats) == "partially_refractory"

    def test_partially_refractory_high_s(self):
        stats = {
            "as_ppm": {"mean": 50},
            "s_sulphide_pct": {"mean": 2.5},
            "carbon_organic_pct": {"mean": 0.01},
        }
        assert _classify_ore_type(stats) == "partially_refractory"

    def test_preg_robbing(self):
        stats = {
            "as_ppm": {"mean": 100},
            "s_sulphide_pct": {"mean": 0.5},
            "carbon_organic_pct": {"mean": 0.3},
        }
        assert _classify_ore_type(stats) == "preg_robbing"

    def test_empty_stats(self):
        result = _classify_ore_type({})
        assert result == "free_milling"


class TestExtractFeatureMatrix:

    def test_basic_extraction(self):
        samples = [_make_sample() for _ in range(5)]
        X, valid_idx = _extract_feature_matrix(samples, ["au_grade", "fe_pct"])
        assert X.shape == (5, 2)
        assert len(valid_idx) == 5

    def test_missing_values_nan(self):
        samples = [{"au_grade": 1.0, "fe_pct": None}] * 5
        X, valid_idx = _extract_feature_matrix(samples, ["au_grade", "fe_pct"])
        assert X.shape[0] == 5
        # fe_pct devrait être NaN
        assert np.isnan(X[0, 1])

    def test_all_missing_sample_excluded(self):
        samples = [{"au_grade": None, "fe_pct": None}] * 5
        X, valid_idx = _extract_feature_matrix(samples, ["au_grade", "fe_pct"])
        assert X.shape[0] == 0


class TestKnnImpute:

    def test_no_nan_unchanged(self):
        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        result = _knn_impute(X)
        np.testing.assert_array_almost_equal(result, X)

    def test_nan_imputed(self):
        X = np.array([
            [1.0, 2.0],
            [np.nan, 4.0],
            [5.0, 6.0],
            [1.0, 2.0],
            [5.0, 6.0],
        ])
        result = _knn_impute(X)
        assert not np.any(np.isnan(result))
        assert result.shape == X.shape
