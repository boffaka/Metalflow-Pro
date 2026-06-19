"""
GADE Engine — Geometallurgical Auto-Domaining Engine (Pure).

Engine pur : aucun accès DB, aucun I/O (sauf sérialisation joblib dans
train_domain_recovery_model). Toutes les fonctions reçoivent des données
et retournent des résultats. Compatible Python 3.11+, graceful degradation
si sklearn / shap / hdbscan / xgboost / umap sont absents.

Tâches implémentées :
  4.1 — Pipeline principal K-Means (run_geometallurgical_domaining)
  4.2 — GMM et HDBSCAN dans run_geometallurgical_domaining
  4.3 — train_domain_recovery_model + predict_domain_for_samples
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

logger = logging.getLogger("mpdpms.gade_engine")

# ---------------------------------------------------------------------------
# Optional imports — graceful degradation
# ---------------------------------------------------------------------------
try:
    from sklearn.impute import KNNImputer
    from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans
    from sklearn.mixture import GaussianMixture
    from sklearn.manifold import TSNE
    from sklearn.metrics import (
        silhouette_score as sk_silhouette,
        davies_bouldin_score as sk_davies_bouldin,
    )
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.model_selection import cross_val_score
    _SKLEARN = True
except ImportError:  # pragma: no cover
    _SKLEARN = False

try:
    import shap as _shap_lib
    _SHAP = True
except ImportError:  # pragma: no cover
    _SHAP = False

try:
    import hdbscan as _hdbscan_lib
    _HDBSCAN = True
except ImportError:  # pragma: no cover
    _HDBSCAN = False

try:
    from xgboost import XGBRegressor
    _XGBOOST = True
except ImportError:  # pragma: no cover
    _XGBOOST = False

try:
    import umap as _umap_lib
    _UMAP = True
except ImportError:  # pragma: no cover
    _UMAP = False

try:
    import joblib as _joblib
    _JOBLIB = True
except ImportError:  # pragma: no cover
    _JOBLIB = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOMAIN_PALETTE = [
    "#0D9488", "#D97706", "#DC2626", "#7C3AED",
    "#2563EB", "#DB2777", "#059669", "#EA580C",
]

# Features d'entrée pour les modèles prédictifs (tâche 4.3)
PREDICTOR_FEATURES = [
    "au_grade", "s_sulphide_pct", "carbon_organic_pct",
    "cu_ppm", "as_ppm", "fe_pct", "bwi_ball", "sg",
]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

ClusterAlgorithm = Literal["kmeans", "gmm", "hdbscan", "hierarchical"]
NormMethod = Literal["zscore", "minmax", "robust"]


@dataclass
class DomainizingConfig:
    algorithm: ClusterAlgorithm = "kmeans"
    features_used: list[str] = field(default_factory=list)
    feature_weights: dict[str, float] | None = None
    normalization: NormMethod = "robust"
    n_domains_requested: int | None = None


# ---------------------------------------------------------------------------
# Step 1 — Feature matrix extraction
# ---------------------------------------------------------------------------

def _extract_feature_matrix(
    samples: list[dict],
    features: list[str],
) -> tuple[np.ndarray, list[int]]:
    """
    Extrait une matrice numpy (n_samples × n_features) depuis une liste de dicts.
    Retourne (matrix, valid_indices) où valid_indices sont les indices des samples
    ayant au moins une feature non-None.
    """
    rows: list[list[float]] = []
    valid_indices: list[int] = []

    for idx, sample in enumerate(samples):
        row: list[float] = []
        for feat in features:
            val = _get_feature_value(sample, feat)
            row.append(float(val) if val is not None else float("nan"))
        # Garder si au moins 50% des features sont présentes
        n_valid = sum(1 for v in row if not np.isnan(v))
        if n_valid >= max(1, len(features) // 2):
            rows.append(row)
            valid_indices.append(idx)

    if not rows:
        return np.empty((0, len(features))), []

    return np.array(rows, dtype=np.float64), valid_indices


def _get_feature_value(sample: dict, feature: str) -> float | None:
    """
    Cherche la valeur d'une feature dans un dict potentiellement imbriqué.
    Supporte les chemins plats (ex: "au_grade") et les champs courants LIMS.
    """
    # Accès direct
    if feature in sample and sample[feature] is not None:
        return sample[feature]

    # Chercher dans les sous-dicts courants LIMS
    for sub_key in ("lims_a1", "lims_b1", "lims_d1", "leach_test", "grind_test", "assay"):
        sub = sample.get(sub_key)
        if isinstance(sub, dict) and feature in sub and sub[feature] is not None:
            return sub[feature]

    return None


# ---------------------------------------------------------------------------
# Step 2 — KNN imputation
# ---------------------------------------------------------------------------

def _knn_impute(X: np.ndarray) -> np.ndarray:
    """
    Imputation KNN (sklearn KNNImputer, n_neighbors=5).
    Fallback sur médiane colonne si sklearn absent.
    """
    if not np.any(np.isnan(X)):
        return X

    if _SKLEARN:
        imputer = KNNImputer(n_neighbors=min(5, X.shape[0] - 1) if X.shape[0] > 1 else 1)
        return imputer.fit_transform(X)

    # Fallback : médiane par colonne
    X_out = X.copy()
    for col in range(X.shape[1]):
        col_data = X_out[:, col]
        nan_mask = np.isnan(col_data)
        if nan_mask.any():
            median_val = float(np.nanmedian(col_data)) if not np.all(nan_mask) else 0.0
            col_data[nan_mask] = median_val
        X_out[:, col] = col_data
    return X_out


# ---------------------------------------------------------------------------
# Step 3 — Normalisation
# ---------------------------------------------------------------------------

def _normalize(X: np.ndarray, method: NormMethod) -> np.ndarray:
    """StandardScaler / RobustScaler / MinMaxScaler selon méthode demandée."""
    if not _SKLEARN:
        # Fallback z-score manuel
        means = np.mean(X, axis=0)
        stds = np.std(X, axis=0)
        stds[stds == 0] = 1.0
        return (X - means) / stds

    if method == "zscore":
        return StandardScaler().fit_transform(X)
    elif method == "minmax":
        return MinMaxScaler().fit_transform(X)
    else:  # "robust" (recommandé pour données géo)
        return RobustScaler().fit_transform(X)


# ---------------------------------------------------------------------------
# Step 4 — Pondération features
# ---------------------------------------------------------------------------

def _apply_weights(
    X: np.ndarray,
    features: list[str],
    weights: dict[str, float] | None,
) -> np.ndarray:
    """Multiplie chaque colonne par le poids correspondant."""
    if not weights:
        return X
    w = np.array([weights.get(f, 1.0) for f in features], dtype=np.float64)
    return X * w


# ---------------------------------------------------------------------------
# Step 5 — PCA préalable
# ---------------------------------------------------------------------------

def _apply_pca(X: np.ndarray, variance: float = 0.95) -> np.ndarray:
    """
    PCA pour réduire la dimensionnalité en conservant `variance` de la variance.
    Retourne X_pca. Fallback : retourner X si sklearn absent ou n < 3.
    """
    if not _SKLEARN or X.shape[0] < 3 or X.shape[1] < 2:
        return X

    n_components = min(variance, X.shape[0] - 1, X.shape[1])
    try:
        pca = PCA(n_components=n_components, random_state=42)
        return pca.fit_transform(X)
    except Exception as exc:
        logger.warning("PCA failed (%s), using raw features", exc)
        return X


# ---------------------------------------------------------------------------
# Step 6 — Clustering
# ---------------------------------------------------------------------------

def _select_optimal_k_sklearn(X: np.ndarray, k_min: int = 2, k_max: int = 15) -> int:
    """
    Sélectionne le k optimal via silhouette + Davies-Bouldin + Calinski-Harabasz.
    Range : k_min à min(k_max, n//20).
    """
    n = X.shape[0]
    k_max = max(k_min, min(k_max, n // 20)) if n // 20 >= k_min else k_min

    if k_max < k_min:
        return k_min

    from sklearn.metrics import calinski_harabasz_score

    best_score: float = -float("inf")
    best_k: int = k_min

    for k in range(k_min, k_max + 1):
        if k >= n:
            break
        try:
            km = KMeans(n_clusters=k, n_init=20, random_state=42)
            labels = km.fit_predict(X)
            if len(np.unique(labels)) < 2:
                continue
            sil = sk_silhouette(X, labels)
            db = sk_davies_bouldin(X, labels)
            ch = calinski_harabasz_score(X, labels)
            # Score combiné normalisé (sil élevé bon, db faible bon, ch élevé bon)
            score = sil - (db / 10.0) + (ch / 10000.0)
            if score > best_score:
                best_score = score
                best_k = k
        except Exception as exc:
            logger.debug("k=%d failed: %s", k, exc)

    return best_k


def _cluster_kmeans(X: np.ndarray, k: int) -> np.ndarray:
    """K-Means sklearn."""
    km = KMeans(n_clusters=k, n_init=20, random_state=42)
    return km.fit_predict(X)


def _cluster_gmm(X: np.ndarray, k: int | None, n: int) -> tuple[np.ndarray, np.ndarray]:
    """
    GMM avec sélection BIC si k est None.
    Retourne (labels, probabilities).
    """
    if k is None:
        k_max = max(2, min(10, n // 20))
        best_bic = float("inf")
        best_k = 2
        for kk in range(2, k_max + 1):
            if kk >= n:
                break
            try:
                gm = GaussianMixture(
                    n_components=kk, covariance_type="full", random_state=42
                )
                gm.fit(X)
                bic = gm.bic(X)
                if bic < best_bic:
                    best_bic = bic
                    best_k = kk
            except Exception:
                pass
        k = best_k

    gm = GaussianMixture(n_components=k, covariance_type="full", random_state=42)
    gm.fit(X)
    labels = gm.predict(X)
    probs = gm.predict_proba(X)
    return labels, probs


def _cluster_hdbscan(
    X: np.ndarray, n: int
) -> tuple[np.ndarray, int]:
    """
    HDBSCAN. Retourne (labels, n_noise).
    Les points bruit ont label == -1.
    Fallback K-Means si hdbscan absent.
    """
    if not _HDBSCAN:
        logger.warning("hdbscan not available, falling back to kmeans")
        k = max(2, min(8, n // 20))
        if _SKLEARN:
            labels = _cluster_kmeans(X, k)
        else:
            labels = _fallback_kmeans(X, k)
        return labels, 0

    min_cluster_size = max(10, n // 20)
    clusterer = _hdbscan_lib.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=5,
        prediction_data=True,
    )
    labels = clusterer.fit_predict(X)
    n_noise = int(np.sum(labels == -1))
    return labels, n_noise


def _fallback_kmeans(X: np.ndarray, k: int, max_iter: int = 100, seed: int = 42) -> np.ndarray:
    """K-Means pur numpy si sklearn absent."""
    rng = np.random.RandomState(seed)
    n = X.shape[0]
    idx = rng.choice(n, min(k, n), replace=False)
    centroids = X[idx].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = dists.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            mask = labels == j
            if mask.any():
                centroids[j] = X[mask].mean(axis=0)
    return labels


# ---------------------------------------------------------------------------
# Step 7 — Quality metrics
# ---------------------------------------------------------------------------

def _compute_clustering_metrics(
    X_pca: np.ndarray, labels: np.ndarray
) -> tuple[float, float]:
    """Retourne (silhouette_score, davies_bouldin_score)."""
    unique = np.unique(labels[labels >= 0])  # exclure bruit HDBSCAN
    if len(unique) < 2 or X_pca.shape[0] < 2:
        return 0.0, 0.0

    # Utiliser seulement les points non-bruit pour les métriques
    mask = labels >= 0
    X_eval = X_pca[mask]
    lbl_eval = labels[mask]

    if len(np.unique(lbl_eval)) < 2:
        return 0.0, 0.0

    try:
        if _SKLEARN:
            sil = float(sk_silhouette(X_eval, lbl_eval))
            db = float(sk_davies_bouldin(X_eval, lbl_eval))
        else:
            sil = _numpy_silhouette(X_eval, lbl_eval)
            db = _numpy_davies_bouldin(X_eval, lbl_eval)
        return sil, db
    except Exception as exc:
        logger.warning("Metrics computation failed: %s", exc)
        return 0.0, 0.0


def _numpy_silhouette(X: np.ndarray, labels: np.ndarray) -> float:
    """Silhouette pur numpy (fallback)."""
    n = X.shape[0]
    unique = np.unique(labels)
    if len(unique) < 2:
        return 0.0
    sil = np.zeros(n)
    for i in range(n):
        same = labels == labels[i]
        a_i = float(np.mean(np.linalg.norm(X[same] - X[i], axis=1))) if same.sum() > 1 else 0.0
        b_i = float("inf")
        for c in unique:
            if c == labels[i]:
                continue
            mask_c = labels == c
            b_i = min(b_i, float(np.mean(np.linalg.norm(X[mask_c] - X[i], axis=1))))
        denom = max(a_i, b_i)
        sil[i] = (b_i - a_i) / denom if denom > 0 else 0.0
    return float(np.mean(sil))


def _numpy_davies_bouldin(X: np.ndarray, labels: np.ndarray) -> float:
    """Davies-Bouldin pur numpy (fallback)."""
    unique = np.unique(labels)
    k = len(unique)
    if k < 2:
        return 0.0
    centroids = np.array([X[labels == c].mean(axis=0) for c in unique])
    spreads = np.array([
        np.mean(np.linalg.norm(X[labels == c] - centroids[i], axis=1))
        for i, c in enumerate(unique)
    ])
    spreads[spreads == 0] = 1e-9
    db = 0.0
    for i in range(k):
        max_r = 0.0
        for j in range(k):
            if i == j:
                continue
            d_ij = np.linalg.norm(centroids[i] - centroids[j]) or 1e-9
            max_r = max(max_r, (spreads[i] + spreads[j]) / d_ij)
        db += max_r
    return float(db / k)


# ---------------------------------------------------------------------------
# Step 8 — Construction des objets domaine
# ---------------------------------------------------------------------------

def _classify_ore_type(stats: dict) -> str:
    """
    Règles de classification du type de minerai.
    as_ppm > 500 ou s_sulphide > 2 → partially_refractory
    c_organic > 0.2 → preg_robbing
    sinon → free_milling
    """
    as_ppm = stats.get("as_ppm", {}).get("mean", 0) or 0
    s_sulphide = stats.get("s_sulphide_pct", {}).get("mean", 0) or 0
    c_organic = stats.get("carbon_organic_pct", {}).get("mean", 0) or 0

    if as_ppm > 500 or s_sulphide > 2:
        return "partially_refractory"
    if c_organic > 0.2:
        return "preg_robbing"
    return "free_milling"


def _recommend_processing_route(ore_type: str, stats: dict) -> str:
    """Recommandation de route de traitement basée sur type de minerai."""
    if ore_type == "partially_refractory":
        return "roasting_or_pressure_oxidation_then_cil"
    if ore_type == "preg_robbing":
        return "carbon_blinding_or_gravity_concentration_then_cil"
    bwi = stats.get("bwi_ball", {}).get("mean", 14) or 14
    if bwi > 18:
        return "sag_ball_mill_cil"
    return "ball_mill_cil"


def _compute_domain_statistics(
    X_raw: np.ndarray,
    features: list[str],
    sample_indices: list[int],
) -> dict[str, dict]:
    """
    Calcule mean, std, median, p10, p90 pour chaque feature.
    X_raw est la matrice avant normalisation (valeurs brutes).
    """
    stats: dict[str, dict] = {}
    for j, feat in enumerate(features):
        col = X_raw[:, j]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            stats[feat] = {"mean": 0.0, "std": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0}
        else:
            stats[feat] = {
                "mean": float(np.mean(valid)),
                "std": float(np.std(valid)),
                "median": float(np.median(valid)),
                "p10": float(np.percentile(valid, 10)),
                "p90": float(np.percentile(valid, 90)),
            }
    return stats


def _build_metallurgical_signature(
    stats: dict,
    valid_samples: list[dict],
    ore_type: str,
) -> dict:
    """Construit la signature métallurgique d'un domaine."""
    # avg_au_recovery : chercher dans plusieurs champs possibles
    au_recoveries = _collect_target_values(valid_samples, "au_recovery")
    cn_consumptions = _collect_target_values(valid_samples, "cn_consumption")
    bwi_values = _collect_target_values(valid_samples, "bwi_ball") or _collect_target_values(valid_samples, "bwi_kwh_t")
    grind_targets = _collect_target_values(valid_samples, "p80_target") or _collect_target_values(valid_samples, "p80_um")

    def _safe_mean(vals: list[float], default: float) -> float:
        return float(np.mean(vals)) if vals else default

    avg_rec = _safe_mean(au_recoveries, 0.89)
    # Normaliser : si > 1 → supposer c'est un pourcentage
    if avg_rec > 1.0:
        avg_rec = avg_rec / 100.0
    avg_rec = max(0.0, min(1.0, avg_rec))

    processing_route = _recommend_processing_route(ore_type, stats)

    return {
        "avg_au_recovery": round(avg_rec, 4),
        "avg_cn_consumption": round(_safe_mean(cn_consumptions, 0.5), 4),
        "avg_bwi": round(_safe_mean(bwi_values, 14.0), 2),
        "avg_grind_target": round(_safe_mean(grind_targets, 150.0), 1),
        "ore_type_classification": ore_type,
        "processing_route_recommendation": processing_route,
    }


def _collect_target_values(samples: list[dict], field_name: str) -> list[float]:
    """Collecte les valeurs non-None d'un champ depuis une liste de samples."""
    vals: list[float] = []
    for s in samples:
        val = _get_feature_value(s, field_name)
        if val is not None:
            try:
                vals.append(float(val))
            except (TypeError, ValueError):
                pass
    return vals


# ---------------------------------------------------------------------------
# Step 9 — SHAP / Feature importance
# ---------------------------------------------------------------------------

def _compute_shap_explanations(
    X: np.ndarray,
    labels: np.ndarray,
    features: list[str],
) -> dict[int, list[dict]]:
    """
    Entraîne un RandomForestClassifier, calcule SHAP values (ou feature_importances_).
    Retourne {domain_id: [{"feature", "importance_score", "direction", "description"}]}
    """
    # Filtrer les points bruit HDBSCAN
    mask = labels >= 0
    X_clean = X[mask]
    y_clean = labels[mask]

    unique_labels = np.unique(y_clean)
    if len(unique_labels) < 2 or X_clean.shape[0] < 4:
        # Pas assez de données — retourner importance uniforme
        return _uniform_importance(unique_labels, features)

    if not _SKLEARN:
        return _uniform_importance(unique_labels, features)

    try:
        rf = RandomForestClassifier(
            n_estimators=100, random_state=42, n_jobs=-1
        )
        rf.fit(X_clean, y_clean)

        if _SHAP:
            explainer = _shap_lib.TreeExplainer(rf)
            # shap_values shape: (n_classes, n_samples, n_features) ou (n_samples, n_features)
            shap_vals = explainer.shap_values(X_clean)
            return _build_domain_shap_features(shap_vals, rf, unique_labels, features, X_clean)
        else:
            importances = rf.feature_importances_
            return _build_domain_rf_features(importances, unique_labels, features, X_clean, rf)

    except Exception as exc:
        logger.warning("SHAP computation failed (%s), using fallback", exc)
        return _uniform_importance(unique_labels, features)


def _build_domain_shap_features(
    shap_vals: Any,
    rf: Any,
    unique_labels: np.ndarray,
    features: list[str],
    X: np.ndarray,
) -> dict[int, list[dict]]:
    """Construit discriminating_features depuis SHAP values par classe."""
    result: dict[int, list[dict]] = {}
    n_features = len(features)

    for i, label in enumerate(unique_labels):
        try:
            # shap_vals peut être liste (une entrée par classe) ou 2D
            if isinstance(shap_vals, list) and len(shap_vals) > i:
                sv = np.array(shap_vals[i])  # (n_samples, n_features)
            elif isinstance(shap_vals, np.ndarray) and shap_vals.ndim == 3:
                sv = shap_vals[:, :, i] if shap_vals.shape[2] == len(unique_labels) else shap_vals[i]
            else:
                sv = np.abs(np.array(shap_vals))

            mean_abs = np.mean(np.abs(sv), axis=0)
            if mean_abs.shape[0] != n_features:
                raise ValueError("shape mismatch")

            # Direction : si mean SHAP pour cette classe est positif → "high"
            mean_signed = np.mean(sv, axis=0)

            top_idx = np.argsort(mean_abs)[::-1][:5]
            feats = []
            for fi in top_idx:
                direction = "high" if mean_signed[fi] >= 0 else "low"
                feats.append({
                    "feature": features[fi],
                    "importance_score": round(float(mean_abs[fi]), 4),
                    "direction": direction,
                    "description": f"{'High' if direction == 'high' else 'Low'} {features[fi]} "
                                   f"discriminates domain {label}",
                })
            result[int(label)] = feats
        except Exception:
            result[int(label)] = _uniform_features_for_label(features)

    return result


def _build_domain_rf_features(
    importances: np.ndarray,
    unique_labels: np.ndarray,
    features: list[str],
    X: np.ndarray,
    rf: Any,
) -> dict[int, list[dict]]:
    """Fallback RF feature_importances_ (globaux, même pour tous les domaines)."""
    top_idx = np.argsort(importances)[::-1][:5]
    result: dict[int, list[dict]] = {}
    for label in unique_labels:
        # Direction : comparer la moyenne du domaine vs moyenne globale
        feats = []
        for fi in top_idx:
            # Heuristique direction : moyenne domaine vs moyenne globale
            np.array(
                [True] * X.shape[0]  # simplifié, pas de labels ici
            )
            direction = "high"
            feats.append({
                "feature": features[fi],
                "importance_score": round(float(importances[fi]), 4),
                "direction": direction,
                "description": f"Feature {features[fi]} important for domain {label}",
            })
        result[int(label)] = feats
    return result


def _uniform_importance(unique_labels: np.ndarray, features: list[str]) -> dict[int, list[dict]]:
    return {
        int(label): _uniform_features_for_label(features)
        for label in unique_labels
    }


def _uniform_features_for_label(features: list[str]) -> list[dict]:
    n = len(features)
    score = round(1.0 / n, 4) if n > 0 else 0.0
    return [
        {"feature": f, "importance_score": score, "direction": "high",
         "description": f"Feature {f}"}
        for f in features[:5]
    ]


# ---------------------------------------------------------------------------
# Step 10 — Visualisation 2D (PCA, t-SNE, UMAP)
# ---------------------------------------------------------------------------

def _viz_pca_2d(
    X_normalized: np.ndarray,
    labels: np.ndarray,
    samples: list[dict],
) -> list[dict]:
    """PCA 2D obligatoire."""
    if not _SKLEARN or X_normalized.shape[1] < 2:
        # Fallback : utiliser les 2 premières dimensions
        X2 = X_normalized[:, :2] if X_normalized.shape[1] >= 2 else np.column_stack(
            [X_normalized[:, 0], np.zeros(X_normalized.shape[0])]
        )
    else:
        try:
            pca2 = PCA(n_components=2, random_state=42)
            X2 = pca2.fit_transform(X_normalized)
        except Exception:
            X2 = X_normalized[:, :2]

    points = []
    for i, s in enumerate(samples):
        sample_code = str(s.get("sample_code") or s.get("sample_id") or s.get("id") or i)
        points.append({
            "x": round(float(X2[i, 0]), 4),
            "y": round(float(X2[i, 1]), 4),
            "domain_id": int(labels[i]),
            "sample_code": sample_code,
        })
    return points


def _viz_tsne_2d(
    X_pca: np.ndarray,
    labels: np.ndarray,
    samples: list[dict],
) -> list[dict] | None:
    """t-SNE 2D. None si sklearn absent, trop peu de samples/features, ou erreur."""
    if not _SKLEARN:
        return None
    n = X_pca.shape[0]
    n_feat = X_pca.shape[1]
    # t-SNE nécessite n_components < min(n_samples, n_features)
    if n < 5 or n_feat < 2:
        return None
    try:
        perplexity = min(30, n // 5)
        perplexity = max(5, perplexity)  # perplexity doit être >= 5
        tsne = TSNE(n_components=2, random_state=42, perplexity=perplexity)
        X2 = tsne.fit_transform(X_pca)
        points = []
        for i, s in enumerate(samples):
            sample_code = str(s.get("sample_code") or s.get("sample_id") or s.get("id") or i)
            points.append({
                "x": round(float(X2[i, 0]), 4),
                "y": round(float(X2[i, 1]), 4),
                "domain_id": int(labels[i]),
                "sample_code": sample_code,
            })
        return points
    except Exception as exc:
        logger.warning("t-SNE failed: %s", exc)
        return None


def _viz_umap_2d(
    X_pca: np.ndarray,
    labels: np.ndarray,
    samples: list[dict],
) -> list[dict] | None:
    """UMAP 2D. None si umap-learn absent ou erreur."""
    if not _UMAP:
        return None
    try:
        reducer = _umap_lib.UMAP(random_state=42)
        X2 = reducer.fit_transform(X_pca)
        points = []
        for i, s in enumerate(samples):
            sample_code = str(s.get("sample_code") or s.get("sample_id") or s.get("id") or i)
            points.append({
                "x": round(float(X2[i, 0]), 4),
                "y": round(float(X2[i, 1]), 4),
                "domain_id": int(labels[i]),
                "sample_code": sample_code,
            })
        return points
    except Exception as exc:
        logger.warning("UMAP failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Tâche 4.1 + 4.2 — Fonction principale run_geometallurgical_domaining
# ---------------------------------------------------------------------------

def run_geometallurgical_domaining(
    samples: list[dict],
    config: DomainizingConfig | dict | None = None,
) -> dict:
    """
    Pipeline principal GADE.

    Étapes :
      1. Extraction features
      2. Imputation KNN
      3. Normalisation
      4. Pondération features
      5. PCA préalable (95% variance)
      6. Clustering (kmeans / gmm / hdbscan)
      7. Métriques qualité (silhouette, davies-bouldin)
      8. Construction objets domaine
      9. SHAP / Feature importance
      10. Visualisation 2D (PCA obligatoire, t-SNE + UMAP optionnels)

    Retourne un dict conforme au format de sortie GADE.
    """
    # Normaliser config
    if config is None:
        config = DomainizingConfig()
    elif isinstance(config, dict):
        config = DomainizingConfig(
            algorithm=config.get("algorithm", "kmeans"),
            features_used=config.get("features_used", []),
            feature_weights=config.get("feature_weights"),
            normalization=config.get("normalization", "robust"),
            n_domains_requested=config.get("n_domains_requested"),
        )

    features = config.features_used
    if not features:
        # Utiliser les features standard si non spécifiées
        features = [
            "au_grade", "fe_pct", "s_sulphide_pct", "as_ppm",
            "carbon_organic_pct", "cu_ppm", "bwi_ball", "sg",
            "au_recovery", "cn_consumption",
        ]

    n_total = len(samples)
    if n_total < 4:
        return {
            "status": "insufficient_data",
            "message": f"Need >= 4 samples, got {n_total}",
            "domains": [],
            "labels": [],
            "silhouette_score": 0.0,
            "davies_bouldin_score": 0.0,
            "n_samples_used": 0,
            "n_domains_found": 0,
            "viz_pca": [],
            "viz_tsne": None,
            "viz_umap": None,
        }

    # --- Étape 1 : Extraction ---
    X_raw, valid_indices = _extract_feature_matrix(samples, features)
    valid_samples = [samples[i] for i in valid_indices]
    n_valid = X_raw.shape[0]

    if n_valid < 4:
        return {
            "status": "insufficient_data",
            "message": f"Only {n_valid} samples had enough features",
            "domains": [],
            "labels": [],
            "silhouette_score": 0.0,
            "davies_bouldin_score": 0.0,
            "n_samples_used": n_valid,
            "n_domains_found": 0,
            "viz_pca": [],
            "viz_tsne": None,
            "viz_umap": None,
        }

    # --- Étape 2 : Imputation ---
    X_imputed = _knn_impute(X_raw)

    # --- Étape 3 : Normalisation ---
    X_norm = _normalize(X_imputed, config.normalization)

    # --- Étape 4 : Pondération ---
    X_weighted = _apply_weights(X_norm, features, config.feature_weights)

    # --- Étape 5 : PCA ---
    X_pca = _apply_pca(X_weighted, variance=0.95)

    # --- Étape 6 : Clustering ---
    algorithm = config.algorithm
    n_domains_requested = config.n_domains_requested
    n_noise = 0
    gmm_probs: np.ndarray | None = None

    if algorithm == "gmm" and _SKLEARN:
        labels_arr, gmm_probs = _cluster_gmm(X_pca, n_domains_requested, n_valid)
        labels = labels_arr
    elif algorithm == "hdbscan":
        labels_arr, n_noise = _cluster_hdbscan(X_pca, n_valid)
        labels = labels_arr
    else:
        # kmeans (ou hierarchical en fallback kmeans)
        if n_domains_requested is not None:
            k = max(2, n_domains_requested)
        elif _SKLEARN:
            k = _select_optimal_k_sklearn(X_pca, k_min=2, k_max=min(15, n_valid // 20))
            k = max(2, k)
        else:
            k = max(2, min(8, n_valid // 20))

        if _SKLEARN:
            labels = _cluster_kmeans(X_pca, k)
        else:
            labels = _fallback_kmeans(X_pca, k)

    # Remap des labels pour exclure bruit HDBSCAN (label == -1)
    unique_labels = sorted(set(labels[labels >= 0].tolist()))
    n_domains_found = len(unique_labels)

    # --- Étape 7 : Métriques ---
    silhouette, davies_bouldin = _compute_clustering_metrics(X_pca, labels)

    # --- Étape 9 : SHAP (calculé avant step 8 pour alimenter discriminating_features) ---
    shap_by_domain = _compute_shap_explanations(X_pca, labels, features)

    # --- Étape 8 : Construction domaines ---
    domains: list[dict] = []
    for dom_idx, label_val in enumerate(unique_labels):
        mask = labels == label_val
        X_domain_raw = X_raw[mask]
        domain_valid_samples = [valid_samples[i] for i in range(n_valid) if mask[i]]
        n_dom = int(mask.sum())

        stats = _compute_domain_statistics(X_domain_raw, features, [])
        ore_type = _classify_ore_type(stats)
        met_sig = _build_metallurgical_signature(stats, domain_valid_samples, ore_type)

        disc_features = shap_by_domain.get(int(label_val), _uniform_features_for_label(features))

        color = DOMAIN_PALETTE[dom_idx % len(DOMAIN_PALETTE)]
        domain_code = f"DOM-{dom_idx + 1:02d}"

        domain_obj: dict = {
            "domain_code": domain_code,
            "n_samples": n_dom,
            "pct_of_total": round(n_dom / n_valid * 100.0, 2),
            "statistics": stats,
            "metallurgical_signature": met_sig,
            "discriminating_features": disc_features,
            "color": color,
        }

        # Confidence scores GMM
        if gmm_probs is not None:
            domain_mask_indices = [i for i in range(n_valid) if mask[i]]
            if dom_idx < gmm_probs.shape[1]:
                domain_probs = [
                    round(float(gmm_probs[i, dom_idx]), 4)
                    for i in domain_mask_indices
                ]
                domain_obj["gmm_confidence_scores"] = domain_probs

        domains.append(domain_obj)

    # Métadonnées HDBSCAN
    extra: dict = {}
    if algorithm == "hdbscan":
        extra["n_noise_samples"] = n_noise

    # --- Étape 10 : Visualisation 2D ---
    # PCA 2D sur X_norm (avant PCA préalable) pour meilleure lisibilité
    viz_pca = _viz_pca_2d(X_weighted, labels, valid_samples)

    # t-SNE et UMAP sur X_pca
    viz_tsne = _viz_tsne_2d(X_pca, labels, valid_samples)
    viz_umap = _viz_umap_2d(X_pca, labels, valid_samples)

    # Construire la correspondance label → domain_code
    label_to_domain_id = {
        label_val: dom_idx
        for dom_idx, label_val in enumerate(unique_labels)
    }

    # Remapper les labels finaux (0..n_domains-1, -1 pour bruit)
    labels_out = [
        label_to_domain_id.get(int(lbl), -1) for lbl in labels.tolist()
    ]

    return {
        "status": "ok",
        "domains": domains,
        "labels": labels_out,
        "silhouette_score": round(silhouette, 4),
        "davies_bouldin_score": round(davies_bouldin, 4),
        "n_samples_used": n_valid,
        "n_domains_found": n_domains_found,
        "viz_pca": viz_pca,
        "viz_tsne": viz_tsne,
        "viz_umap": viz_umap,
        **extra,
    }


# ---------------------------------------------------------------------------
# Tâche 4.3 — train_domain_recovery_model
# ---------------------------------------------------------------------------

def train_domain_recovery_model(
    domain_samples: list[dict],
    target: str = "au_recovery",
    model_type: str = "random_forest",
    k_folds: int = 5,
    domain_code: str = "DOM-01",
) -> dict | None:
    """
    Entraîne un modèle prédictif XGBoost ou RandomForest pour un domaine.

    Args:
        domain_samples : liste de dicts LIMS pour ce domaine.
        target         : variable cible ("au_recovery"|"cn_consumption"|"bwi"|"residue_grade").
        model_type     : "random_forest" ou "xgboost".
        k_folds        : nombre de folds pour cross-validation.
        domain_code    : code du domaine (pour le nom du fichier joblib).

    Retourne None si len(domain_samples) < 10, sinon un dict de métriques.
    """
    n = len(domain_samples)
    if n < 10:
        logger.warning(
            "train_domain_recovery_model: insufficient samples (%d < 10) for domain %s, target %s",
            n, domain_code, target,
        )
        return None

    if not _SKLEARN:
        logger.error("scikit-learn not available — cannot train model")
        return None

    # --- Extraction features d'entrée ---
    available_features = [
        f for f in PREDICTOR_FEATURES
        if any(_get_feature_value(s, f) is not None for s in domain_samples)
    ]
    if not available_features:
        logger.warning("No predictor features found for domain %s", domain_code)
        return None

    X_rows: list[list[float]] = []
    y_vals: list[float] = []

    for s in domain_samples:
        y_val = _extract_target_value(s, target)
        if y_val is None:
            continue
        row = []
        for feat in available_features:
            v = _get_feature_value(s, feat)
            row.append(float(v) if v is not None else float("nan"))
        X_rows.append(row)
        y_vals.append(float(y_val))

    if len(y_vals) < 10:
        logger.warning(
            "Insufficient samples with target '%s' (%d), domain %s",
            target, len(y_vals), domain_code,
        )
        return None

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_vals, dtype=np.float64)

    # Imputer les NaN
    X = _knn_impute(X)

    # Normaliser la target si c'est au_recovery > 1 (c'est un %)
    y_normalized = y.copy()
    if target == "au_recovery" and np.mean(y) > 1.0:
        y_normalized = y / 100.0

    # --- Split 80/20 ---
    n_samples = X.shape[0]
    test_size = max(1, int(n_samples * 0.2))
    train_size = n_samples - test_size

    # Split simple (pas de stratification sur régression)
    rng = np.random.RandomState(42)
    indices = rng.permutation(n_samples)
    train_idx = indices[:train_size]
    test_idx = indices[train_size:]

    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y_normalized[train_idx], y_normalized[test_idx]

    # --- Entraîner le modèle ---
    model_obj = _build_predictive_model(model_type, X_train, y_train)
    if model_obj is None:
        return None

    actual_model_type, model = model_obj

    # --- Métriques test ---
    y_pred = model.predict(X_test)
    test_r2 = _r2_score(y_test, y_pred)
    test_rmse = float(np.sqrt(np.mean((y_test - y_pred) ** 2)))
    test_mae = float(np.mean(np.abs(y_test - y_pred)))

    # --- Cross-validation ---
    cv_k = min(k_folds, train_size)
    if cv_k >= 2:
        try:
            cv_scores = cross_val_score(
                model, X_train, y_train, cv=cv_k, scoring="r2"
            )
            cross_val_scores_list = [round(float(s), 4) for s in cv_scores]
        except Exception as exc:
            logger.debug("cross_val_score failed: %s", exc)
            cross_val_scores_list = []
    else:
        cross_val_scores_list = []

    # Ré-entraîner sur tout X (train+test) pour le modèle final
    model.fit(X, y_normalized)

    # --- Feature importances ---
    if hasattr(model, "feature_importances_"):
        fi = {
            available_features[i]: round(float(model.feature_importances_[i]), 4)
            for i in range(len(available_features))
        }
    else:
        fi = {f: round(1.0 / len(available_features), 4) for f in available_features}

    # --- Sérialisation joblib ---
    artifact_path = _serialize_model(model, domain_code, target)

    return {
        "model_type": actual_model_type,
        "test_r2": round(test_r2, 4),
        "test_rmse": round(test_rmse, 6),
        "test_mae": round(test_mae, 6),
        "cross_val_scores": cross_val_scores_list,
        "feature_importances": fi,
        "model_artifact_path": artifact_path,
        "n_training_samples": int(train_size),
        "n_test_samples": int(test_size),
        "input_features": available_features,
        "target": target,
    }


def _extract_target_value(sample: dict, target: str) -> float | None:
    """
    Extrait la valeur cible d'un sample selon la variable demandée.
    Cherche dans plusieurs champs LIMS courants.
    """
    TARGET_FIELD_MAP: dict[str, list[str]] = {
        "au_recovery": ["au_recovery", "au_recovery_pct", "leach_au_recovery"],
        "cn_consumption": ["cn_consumption", "nacn_consumption_kg_t", "cyanide_consumption"],
        "bwi": ["bwi_ball", "bwi_kwh_t", "bond_work_index"],
        "residue_grade": ["residue_grade", "tails_grade", "leach_residue_au"],
    }
    field_candidates = TARGET_FIELD_MAP.get(target, [target])

    for field_name in field_candidates:
        val = _get_feature_value(sample, field_name)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # Chercher dans les sous-dicts leach_test / grind_test
    for sub_key in ("leach_test", "grind_test"):
        sub = sample.get(sub_key)
        if isinstance(sub, dict):
            for field_name in field_candidates:
                if field_name in sub and sub[field_name] is not None:
                    try:
                        return float(sub[field_name])
                    except (TypeError, ValueError):
                        pass

    return None


def _build_predictive_model(
    model_type: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
) -> tuple[str, Any] | None:
    """
    Instancie et entraîne le modèle. Retourne (actual_model_type, fitted_model).
    Fallback RF si xgboost absent.
    """
    if model_type == "xgboost":
        if _XGBOOST:
            try:
                model = XGBRegressor(
                    n_estimators=200,
                    max_depth=4,
                    learning_rate=0.05,
                    random_state=42,
                    verbosity=0,
                )
                model.fit(X_train, y_train)
                return "xgboost", model
            except Exception as exc:
                logger.warning("XGBoost training failed (%s), falling back to RF", exc)
        else:
            logger.info("xgboost not available, using random_forest")

    # Random Forest (défaut ou fallback)
    try:
        model = RandomForestRegressor(
            n_estimators=200,
            max_depth=6,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train, y_train)
        return "random_forest", model
    except Exception as exc:
        logger.error("RandomForestRegressor training failed: %s", exc)
        return None


def _r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R² score."""
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot == 0:
        return 0.0
    return float(1.0 - ss_res / ss_tot)


def _serialize_model(model: Any, domain_code: str, target: str) -> str | None:
    """
    Sérialise le modèle avec joblib dans storage/models/{domain_code}_{target}.joblib.
    Crée le dossier si nécessaire.
    Retourne le chemin ou None si joblib absent.
    """
    if not _JOBLIB:
        logger.warning("joblib not available — model not serialized")
        return None

    # Chemin relatif depuis la racine du projet backend
    models_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),  # backend/
        "storage", "models",
    )
    os.makedirs(models_dir, exist_ok=True)

    # Nom de fichier safe
    safe_code = "".join(c if c.isalnum() or c in "-_" else "_" for c in domain_code)
    safe_target = "".join(c if c.isalnum() or c in "-_" else "_" for c in target)
    filename = f"{safe_code}_{safe_target}.joblib"
    filepath = os.path.join(models_dir, filename)

    try:
        _joblib.dump(model, filepath)
        logger.info("Model serialized to %s", filepath)
        return filepath
    except Exception as exc:
        logger.error("Model serialization failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Tâche 4.3 — predict_domain_for_samples
# ---------------------------------------------------------------------------

def predict_domain_for_samples(
    samples: list[dict],
    session_result: dict,
) -> list[dict]:
    """
    Assigne chaque sample au domaine le plus proche en distance cosinus
    dans l'espace des features normalisées.

    Args:
        samples        : liste de nouveaux samples à assigner.
        session_result : résultat de run_geometallurgical_domaining.

    Retourne :
        [{sample_idx, domain_id, confidence, predicted_recovery,
          predicted_cn, predicted_bwi}]
    """
    domains = session_result.get("domains", [])
    if not domains:
        return []

    results: list[dict] = []

    for sample_idx, sample in enumerate(samples):
        # Construire vecteur de features pour ce sample
        # Utiliser les features disponibles dans les domaines
        best_domain_idx = 0
        best_similarity = -float("inf")

        for dom_idx, domain in enumerate(domains):
            stats = domain.get("statistics", {})
            if not stats:
                continue

            # Distance cosinus dans l'espace des moyennes de features
            sim = _cosine_similarity_to_domain(sample, stats)
            if sim > best_similarity:
                best_similarity = sim
                best_domain_idx = dom_idx

        best_domain = domains[best_domain_idx]
        met_sig = best_domain.get("metallurgical_signature", {})

        # Confidence : normaliser la similarité cosinus de [-1,1] vers [0,1]
        confidence = float(max(0.0, min(1.0, (best_similarity + 1.0) / 2.0)))

        predicted_recovery = met_sig.get("avg_au_recovery", 0.89)
        predicted_cn = met_sig.get("avg_cn_consumption", 0.5)
        predicted_bwi = met_sig.get("avg_bwi", 14.0)

        results.append({
            "sample_idx": sample_idx,
            "domain_id": best_domain_idx,
            "domain_code": best_domain.get("domain_code", f"DOM-{best_domain_idx + 1:02d}"),
            "confidence": round(confidence, 4),
            "predicted_recovery": round(float(predicted_recovery), 4),
            "predicted_cn": round(float(predicted_cn), 4),
            "predicted_bwi": round(float(predicted_bwi), 2),
        })

    return results


def _cosine_similarity_to_domain(sample: dict, stats: dict) -> float:
    """
    Calcule la similarité cosinus entre un sample et le centroïde d'un domaine
    (représenté par les moyennes de chaque feature).
    """
    features = list(stats.keys())
    sample_vec: list[float] = []
    domain_vec: list[float] = []

    for feat in features:
        sample_val = _get_feature_value(sample, feat)
        domain_mean = stats[feat].get("mean", 0) if isinstance(stats[feat], dict) else 0
        domain_std = stats[feat].get("std", 1) if isinstance(stats[feat], dict) else 1
        if domain_std == 0:
            domain_std = 1.0

        if sample_val is not None:
            # Normaliser par l'écart-type du domaine
            sample_vec.append(float(sample_val) / domain_std)
            domain_vec.append(float(domain_mean) / domain_std)

    if not sample_vec:
        return 0.0

    sv = np.array(sample_vec, dtype=np.float64)
    dv = np.array(domain_vec, dtype=np.float64)

    norm_s = np.linalg.norm(sv)
    norm_d = np.linalg.norm(dv)

    if norm_s == 0 or norm_d == 0:
        return 0.0

    return float(np.dot(sv, dv) / (norm_s * norm_d))


# ---------------------------------------------------------------------------
# Compatibility aliases — KMeans auto-k API (run_gade / train_recovery_model /
# predict_domain) wrapping the full GADE pipeline above.
# ---------------------------------------------------------------------------

# Feature sets disponibles par catégorie LIMS
GEOCHEMICAL_FEATURES = [
    "au_g_t", "ag_g_t", "cu_pct", "fe_pct", "s_total_pct",
    "s_sulfide_pct", "as_ppm", "c_organic_pct", "sio2_pct", "al2o3_pct",
]
COMMINUTION_FEATURES = [
    "bwi_kwh_t", "brwi_kwh_t", "spi_kwh_t", "a_x_b", "dwi_kwh_m3",
    "abrasion_index_ai", "ucs_mpa",
]
METALLURGICAL_FEATURES = [
    "leach_rec_24h_pct", "leach_rec_48h_pct", "au_recovery_pct",
    "nacn_consumption_kg_t", "grg_rec_pct", "au_lib_pct",
]
ALL_DEFAULT_FEATURES = GEOCHEMICAL_FEATURES + COMMINUTION_FEATURES + METALLURGICAL_FEATURES


def run_gade(
    samples: list[dict],
    config: dict | None = None,
) -> dict:
    """
    Simplified GADE entry point: KNN impute → RobustScaler → PCA → KMeans auto-k
    → RF feature importance.

    Delegates to run_geometallurgical_domaining with kmeans algorithm and maps
    the result to the compact run_gade output schema.

    config keys (all optional):
      features      : list[str]   — feature subset to use
      n_domains     : int | None  — None = auto (silhouette on 2-10)
      normalization : str         — 'robust' (default) or 'standard'/'zscore'
      min_cluster_size : int      — minimum samples per domain (default 5)
    """
    cfg = config or {}
    features = cfg.get("features") or ALL_DEFAULT_FEATURES
    n_domains_req = cfg.get("n_domains")
    norm_raw = cfg.get("normalization", "robust")
    # Map 'standard' → 'zscore' for the inner engine
    norm_map = {"standard": "zscore", "robust": "robust", "zscore": "zscore", "minmax": "minmax"}
    norm = norm_map.get(norm_raw, "robust")

    inner_cfg = DomainizingConfig(
        algorithm="kmeans",
        features_used=features,
        normalization=norm,  # type: ignore[arg-type]
        n_domains_requested=n_domains_req,
    )

    res = run_geometallurgical_domaining(samples, inner_cfg)

    if res.get("status") == "insufficient_data":
        return {
            "domains": [], "labels": [], "pca_coords": [],
            "silhouette_score": None, "davies_bouldin_score": None,
            "n_domains_found": 0, "n_samples_used": res.get("n_samples_used", 0),
            "warnings": [res.get("message", "Insufficient data.")],
        }

    # Re-map viz_pca → pca_coords in the compact schema
    pca_coords = []
    for pt in res.get("viz_pca", []):
        pca_coords.append({
            "x": pt["x"],
            "y": pt["y"],
            "label": pt["domain_id"],
            "sample_idx": None,
            "domain_code": (res["domains"][pt["domain_id"]]["domain_code"]
                            if pt["domain_id"] < len(res["domains"]) else "D01"),
        })

    # Compact domain objects
    min_size = cfg.get("min_cluster_size", 5)
    warnings_list: list[str] = []
    compact_domains = []
    for d in res.get("domains", []):
        if d["n_samples"] < min_size:
            warnings_list.append(
                f"{d['domain_code']} : seulement {d['n_samples']} échantillons (< {min_size})."
            )
        disc = d.get("discriminating_features", [])
        compact_domains.append({
            "domain_id": res["domains"].index(d),
            "domain_code": d["domain_code"],
            "label": d["domain_code"],
            "n_samples": d["n_samples"],
            "pct_of_total": d["pct_of_total"],
            "statistics": d.get("statistics", {}),
            "discriminating_features": disc,
        })

    return {
        "domains": compact_domains,
        "labels": res.get("labels", []),
        "pca_coords": pca_coords,
        "silhouette_score": res.get("silhouette_score"),
        "davies_bouldin_score": res.get("davies_bouldin_score"),
        "n_domains_found": res.get("n_domains_found", 0),
        "n_samples_used": res.get("n_samples_used", 0),
        "features_used": features,
        "warnings": warnings_list,
    }


def train_recovery_model(
    domain_samples: list[dict],
    target: str = "leach_rec_48h_pct",
) -> dict | None:
    """
    Trains a RandomForestRegressor on domain samples for a given target.
    Returns None if < 10 samples with a valid target value.

    Wraps train_domain_recovery_model with a target alias map for LIMS field names.
    """
    # Alias map: compact target names → inner engine target names
    target_alias = {
        "leach_rec_48h_pct": "au_recovery",
        "leach_rec_24h_pct": "au_recovery",
        "au_recovery_pct": "au_recovery",
        "au_recovery": "au_recovery",
        "nacn_consumption_kg_t": "cn_consumption",
        "cn_consumption": "cn_consumption",
        "bwi_kwh_t": "bwi",
        "bwi": "bwi",
    }
    inner_target = target_alias.get(target, "au_recovery")
    return train_domain_recovery_model(domain_samples, target=inner_target)


def predict_domain(
    sample: dict,
    session_result: dict,
) -> dict:
    """
    Assigns a single sample to the nearest domain using cosine similarity to
    domain centroids.  session_result is the output of run_gade() or
    run_geometallurgical_domaining().

    Returns {domain_id, domain_code, label, confidence}.
    """
    domains = session_result.get("domains", [])
    if not domains:
        return {"domain_id": 0, "domain_code": "D01", "confidence": 0.0}

    results = predict_domain_for_samples([sample], session_result)
    if not results:
        return {"domain_id": 0, "domain_code": "D01", "confidence": 0.0}

    r = results[0]
    domain = domains[r["domain_id"]] if r["domain_id"] < len(domains) else domains[0]
    return {
        "domain_id": r["domain_id"],
        "domain_code": r.get("domain_code", domain.get("domain_code", "D01")),
        "label": domain.get("label", domain.get("domain_code", "D01")),
        "confidence": r["confidence"],
    }
