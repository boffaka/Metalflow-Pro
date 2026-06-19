"""
GeoMet Intelligence Engine — GADE (Geometallurgical Auto-Domaining Engine).

Multivariate clustering of LIMS metallurgical response (A1/B1/D1) with weighted
PCA, k-means validation, per-domain Random Forest recovery models, and
automatic domain naming per GMIE v1.0 spec.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

import numpy as np

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

try:
    from ..db import qall
except ImportError:  # pragma: no cover
    from db import qall

logger = logging.getLogger("mpdpms.geomet_predictor")

# Primary features (GMIE §1 — mandatory when available)
FEATURES = [
    "au_g_t", "fe_pct", "s_total_pct", "as_ppm", "cu_pct",
    "c_organic_pct", "bwi_kwh_t", "au_recovery_pct", "nacn_consumption_kg_t",
]

LOG_TRANSFORM_FEATURES = {"au_g_t", "as_ppm", "cu_pct", "nacn_consumption_kg_t"}

DEFAULT_FEATURE_WEIGHTS: dict[str, float] = {
    "au_recovery_pct": 3.0,
    "bwi_kwh_t": 2.5,
    "s_total_pct": 2.0,
    "as_ppm": 2.0,
    "au_g_t": 1.0,
    "fe_pct": 1.0,
    "cu_pct": 1.0,
    "c_organic_pct": 1.0,
    "nacn_consumption_kg_t": 1.0,
}

MIN_CLUSTER_SAMPLES = 15
MAX_MISSING_PCT = 0.30
OUTLIER_Z = 3.5

PREDICTOR_FEATURES = ["au_g_t", "fe_pct", "s_total_pct", "as_ppm", "c_organic_pct", "bwi_kwh_t", "cu_pct"]

ORE_CLASS_RULES = {
    "refractory": lambda p: (p.get("as_ppm", 0) or 0) > 500 or (p.get("s_total_pct", 0) or 0) > 2,
    "semi_refractory": lambda p: 200 < (p.get("as_ppm", 0) or 0) <= 500,
    "preg_robbing": lambda p: (p.get("c_organic_pct", 0) or 0) > 0.2,
    "free_milling": lambda p: True,
}


def _fetch_joined_samples(pid: str) -> list[dict]:
    rows = qall("""
        SELECT
            s.id AS sample_id, s.sample_id_display, s.lithology, s.geomet_domain AS zone,
            s.provenance, s.depth_interval,
            a.au_g_t, a.fe_pct, a.s_total_pct, a.as_ppm, a.c_organic_pct, a.cu_pct,
            b.bwi_kwh_t, b.rwi_kwh_t, b.sg, b.abrasion_index_ai,
            d.au_recovery_pct, d.nacn_consumption_kg_t, d.cao_consumption_kg_t,
            d.p80_um, d.leach_time_h
        FROM lims_samples s
        LEFT JOIN lims_a1 a ON a.sample_id = s.id AND a.project_id = s.project_id
        LEFT JOIN lims_b1 b ON b.sample_id = s.id AND b.project_id = s.project_id
        LEFT JOIN lims_d1 d ON d.sample_id = s.id AND d.project_id = s.project_id
        WHERE s.project_id = %s AND a.au_g_t IS NOT NULL
    """, (pid,))
    return rows or []


def _missing_fraction(row: dict) -> float:
    missing = sum(1 for f in FEATURES if row.get(f) is None)
    return missing / max(len(FEATURES), 1)


def _transform_value(feat: str, val: float) -> float:
    if feat in LOG_TRANSFORM_FEATURES and val > 0:
        return float(np.log10(val + 1e-6))
    return float(val)


def _build_feature_matrix(
    samples: list[dict],
    weights: dict[str, float],
) -> tuple[np.ndarray, np.ndarray, list[dict], list[int]]:
    """Return (X_weighted_z, X_raw, valid_samples, outlier_indices)."""
    valid: list[tuple[dict, list[float]]] = []
    for s in samples:
        if _missing_fraction(s) > MAX_MISSING_PCT:
            continue
        row = []
        skip = False
        for f in FEATURES:
            v = s.get(f)
            if v is None:
                skip = True
                break
            row.append(_transform_value(f, float(v)))
        if not skip:
            valid.append((s, row))

    if len(valid) < 4:
        return np.empty((0, len(FEATURES))), np.empty((0, len(FEATURES))), [], []

    raw_t = np.array([r for _, r in valid], dtype=np.float64)
    raw = np.array([[float(s.get(f) or 0) for f in FEATURES] for s, _ in valid], dtype=np.float64)
    valid_samples = [s for s, _ in valid]

    means = raw_t.mean(axis=0)
    stds = raw_t.std(axis=0)
    stds[stds == 0] = 1.0
    z = (raw_t - means) / stds

    w_vec = np.array([weights.get(f, 1.0) for f in FEATURES], dtype=np.float64)
    X = z * w_vec

    outlier_idx: list[int] = []
    for i in range(X.shape[0]):
        if np.sum(np.abs(z[i]) > OUTLIER_Z) >= 2:
            outlier_idx.append(i)

    return X, raw, valid_samples, outlier_idx


def _pca_summary(X: np.ndarray, feature_names: list[str]) -> dict:
    if X.shape[0] < 3 or X.shape[1] < 2:
        return {"components": [], "explained_variance_ratio": [], "n_components_85pct": 0}
    Xc = X - X.mean(axis=0)
    cov = np.cov(Xc, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    total = float(eigvals.sum()) or 1.0
    ratios = (eigvals / total).tolist()
    cum = np.cumsum(ratios)
    n85 = int(np.searchsorted(cum, 0.85) + 1)
    components = []
    for i in range(min(3, len(ratios))):
        loadings = {feature_names[j]: round(float(eigvecs[j, i]), 4) for j in range(len(feature_names))}
        components.append({"pc": i + 1, "variance_pct": round(ratios[i] * 100, 2), "loadings": loadings})
    return {
        "components": components,
        "explained_variance_ratio": [round(r * 100, 2) for r in ratios[:5]],
        "n_components_85pct": n85,
    }


def _pca_scores(X: np.ndarray, n_comp: int = 2) -> np.ndarray:
    if X.shape[0] < 2 or X.shape[1] < 2:
        return np.zeros((X.shape[0], n_comp))
    Xc = X - X.mean(axis=0)
    cov = np.cov(Xc, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvecs = eigvecs[:, order]
    n_comp = min(n_comp, eigvecs.shape[1])
    return Xc @ eigvecs[:, :n_comp]


def _correlation_matrix(raw: np.ndarray, feature_names: list[str]) -> dict:
    if raw.shape[0] < 3:
        return {"features": feature_names, "matrix": []}
    corr = np.corrcoef(raw.T)
    matrix = [
        [round(float(corr[i, j]), 3) for j in range(len(feature_names))]
        for i in range(len(feature_names))
    ]
    return {"features": feature_names, "matrix": matrix}


def _build_gade_viz(
    X: np.ndarray,
    raw: np.ndarray,
    valid_samples: list[dict],
    labels: np.ndarray,
    domains: list[dict],
    pca: dict,
    pid: str,
) -> dict:
    scores = _pca_scores(X, 2)
    domain_palette = [
        "#0D9488", "#D97706", "#DC2626", "#7C3AED", "#2563EB",
        "#DB2777", "#059669", "#EA580C",
    ]

    biplot_points = []
    for i, s in enumerate(valid_samples):
        d_id = int(labels[i])
        dom = next((d for d in domains if d["domain_id"] == d_id), domains[0] if domains else None)
        biplot_points.append({
            "sample_id": str(s.get("sample_id", "")),
            "label": str(s.get("sample_id_display") or s.get("sample_id", ""))[:24],
            "pc1": round(float(scores[i, 0]), 4),
            "pc2": round(float(scores[i, 1]), 4),
            "domain_id": d_id,
            "domain_name": dom["domain_name"] if dom else f"Domain-{d_id}",
            "color": domain_palette[d_id % len(domain_palette)],
            "lithology": s.get("lithology") or "",
            "au_g_t": round(float(raw[i, FEATURES.index("au_g_t")]), 3),
        })

    loadings = []
    for comp in pca.get("components", [])[:2]:
        for feat, loading in comp.get("loadings", {}).items():
            loadings.append({
                "pc": comp["pc"],
                "feature": feat,
                "loading": loading,
            })

    block_map = _fetch_block_domain_map(pid, domains)

    return {
        "biplot": {
            "points": biplot_points,
            "loadings": loadings,
            "variance_pc1_pct": pca.get("components", [{}])[0].get("variance_pct", 0),
            "variance_pc2_pct": (pca.get("components", [{}] + [{}])[1].get("variance_pct", 0)
                                 if len(pca.get("components", [])) > 1 else 0),
        },
        "correlation": _correlation_matrix(raw, FEATURES),
        "block_domain_map": block_map,
    }


def _fetch_block_domain_map(pid: str, domains: list[dict]) -> list[dict]:
    """Spatial block centroids colored by nearest GADE domain profile (grade proxy)."""
    blocks = qall(
        """
        SELECT b.x_center, b.y_center, b.z_center, b.grade_au, b.rock_type, b.tonnage
        FROM blocks b
        JOIN block_model_configs c ON c.id = b.config_id
        WHERE c.project_id = %s AND b.x_center IS NOT NULL AND b.y_center IS NOT NULL
        ORDER BY b.z_center DESC
        LIMIT 5000
        """,
        (pid,),
    )
    if not blocks or not domains:
        return []

    palette = ["#0D9488", "#D97706", "#DC2626", "#7C3AED", "#2563EB", "#DB2777", "#059669", "#EA580C"]
    points = []
    for blk in blocks:
        grade = float(blk.get("grade_au") or 0)
        best_dom = min(
            domains,
            key=lambda d: abs(d["profile"].get("au_g_t", 0) - grade),
        )
        d_id = int(best_dom["domain_id"])
        points.append({
            "x": round(float(blk["x_center"]), 1),
            "y": round(float(blk["y_center"]), 1),
            "z": round(float(blk.get("z_center") or 0), 1),
            "domain_id": d_id,
            "domain_name": best_dom["domain_name"],
            "color": palette[d_id % len(palette)],
            "grade_au": round(grade, 3),
            "tonnage": round(float(blk.get("tonnage") or 0), 0),
        })
    return points


def _kmeans(X: np.ndarray, k: int, max_iter: int = 100, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.RandomState(seed)
    n = X.shape[0]
    idx = rng.choice(n, k, replace=False)
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
    return labels, centroids


def _silhouette_score(X: np.ndarray, labels: np.ndarray) -> float:
    n = X.shape[0]
    unique = np.unique(labels)
    if len(unique) < 2:
        return -1.0
    sil = np.zeros(n)
    for i in range(n):
        same = labels == labels[i]
        diff_clusters = unique[unique != labels[i]]
        a_i = float(np.mean(np.linalg.norm(X[same] - X[i], axis=1))) if same.sum() > 1 else 0.0
        b_i = float("inf")
        for c in diff_clusters:
            mask_c = labels == c
            b_i = min(b_i, float(np.mean(np.linalg.norm(X[mask_c] - X[i], axis=1))))
        sil[i] = (b_i - a_i) / max(a_i, b_i) if max(a_i, b_i) > 0 else 0.0
    return float(np.mean(sil))


def _davies_bouldin(X: np.ndarray, labels: np.ndarray) -> float:
    unique = np.unique(labels)
    k = len(unique)
    if k < 2:
        return float("inf")
    centroids = np.array([X[labels == c].mean(axis=0) for c in unique])
    spreads = np.array([np.mean(np.linalg.norm(X[labels == c] - centroids[i], axis=1)) for i, c in enumerate(unique)])
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


def _calinski_harabasz(X: np.ndarray, labels: np.ndarray) -> float:
    n, _ = X.shape
    unique = np.unique(labels)
    k = len(unique)
    if k < 2 or n <= k:
        return 0.0
    overall = X.mean(axis=0)
    centroids = np.array([X[labels == c].mean(axis=0) for c in unique])
    between = sum(np.sum(labels == c) * np.sum((centroids[i] - overall) ** 2) for i, c in enumerate(unique))
    within = sum(np.sum((X[labels == c] - centroids[i]) ** 2) for i, c in enumerate(unique))
    if within == 0:
        return 0.0
    return float((between / (k - 1)) / (within / (n - k)))


def _select_optimal_k(X: np.ndarray, k_min: int = 2, k_max: int = 8) -> dict:
    n = X.shape[0]
    k_max = min(k_max, n - 1, 8)
    metrics = []
    inertias = []
    for k in range(k_min, k_max + 1):
        if k >= n:
            break
        labels, centroids = _kmeans(X, k)
        inertia = float(np.sum((X - centroids[labels]) ** 2))
        inertias.append({"k": k, "inertia": round(inertia, 4)})
        sil = _silhouette_score(X, labels)
        db = _davies_bouldin(X, labels)
        ch = _calinski_harabasz(X, labels)
        metrics.append({"k": k, "silhouette": sil, "davies_bouldin": db, "calinski_harabasz": ch})

    if not metrics:
        return {"k": 2, "metrics": [], "inertias": []}

    sil_norm = {m["k"]: m["silhouette"] for m in metrics}
    db_norm = {m["k"]: -m["davies_bouldin"] for m in metrics}
    ch_norm = {m["k"]: m["calinski_harabasz"] for m in metrics}

    def _rank(d: dict) -> dict:
        sorted_k = sorted(d.keys(), key=lambda x: d[x], reverse=True)
        return {k: i for i, k in enumerate(sorted_k)}

    r_sil, r_db, r_ch = _rank(sil_norm), _rank(db_norm), _rank(ch_norm)
    best_k = min(metrics, key=lambda m: (r_sil[m["k"]] + r_db[m["k"]] + r_ch[m["k"]]))["k"]

    for m in metrics:
        labels, _ = _kmeans(X, m["k"])
        counts = np.bincount(labels, minlength=m["k"])
        m["min_cluster_size"] = int(counts.min()) if len(counts) else 0

    return {"k": best_k, "metrics": metrics, "inertias": inertias}


def _bwi_class(bwi: float) -> str:
    if bwi < 10:
        return "Soft"
    if bwi < 14:
        return "Medium"
    if bwi < 18:
        return "Hard"
    return "VeryHard"


def _recovery_class(rec: float) -> str:
    if rec > 92:
        return "Excellent"
    if rec > 85:
        return "Good"
    if rec > 75:
        return "Moderate"
    return "Refractory"


def _mineral_type(profile: dict) -> str:
    s = profile.get("s_total_pct", 0) or 0
    as_p = profile.get("as_ppm", 0) or 0
    cu = profile.get("cu_pct", 0) or 0
    if as_p > 500:
        return "Arsenide"
    if cu > 0.03:
        return "CuBearing"
    if s > 2 and cu > 0.05:
        return "MassiveSulfide"
    if s > 0.5:
        return "Sulfide"
    return "Oxide"


def _risk_score(profile: dict) -> tuple[float, str]:
    score = 0.0
    rec = profile.get("au_recovery_pct", 90) or 90
    bwi = profile.get("bwi_kwh_t", 14) or 14
    cu = profile.get("cu_pct", 0) or 0
    c_org = profile.get("c_organic_pct", 0) or 0
    if rec < 75:
        score += 3
    elif rec < 85:
        score += 1.5
    if bwi > 18:
        score += 2.5
    elif bwi > 14:
        score += 1
    if cu > 0.03:
        score += 2
    if c_org > 0.2:
        score += 2
    score = min(10.0, score)
    level = "Low" if score < 3 else "Medium" if score < 6 else "High"
    return round(score, 1), level


def _auto_domain_name(profile: dict) -> str:
    mineral = _mineral_type(profile)
    bwi = _bwi_class(profile.get("bwi_kwh_t", 14) or 14)
    rec = _recovery_class(profile.get("au_recovery_pct", 90) or 90)
    risk, _ = _risk_score(profile)
    return f"{mineral}-{bwi}-{rec}-Risk{int(round(risk))}"


def _build_rf_model(X_raw: np.ndarray, y: np.ndarray) -> dict:
    n = X_raw.shape[0]
    pred_idx = [FEATURES.index(f) for f in PREDICTOR_FEATURES if f in FEATURES]
    X_pred = X_raw[:, pred_idx]
    feat_names = [FEATURES[i] for i in pred_idx]

    if n < 5:
        return {
            "method": "domain_average",
            "coefficients": {},
            "r_squared": 0.0,
            "rmse": None,
            "n_samples": n,
            "predictor_features": feat_names,
        }

    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.model_selection import cross_val_score

        model = RandomForestRegressor(n_estimators=min(100, max(20, n * 5)), random_state=42)
        if n >= 6:
            cv = min(5, n)
            scores = cross_val_score(model, X_pred, y, cv=cv, scoring="r2")
            r2_cv = float(np.mean(scores))
        else:
            r2_cv = None
        model.fit(X_pred, y)
        y_pred = model.predict(X_pred)
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rmse = float(np.sqrt(ss_res / n))
        return {
            "method": "random_forest",
            "r_squared": round(r2, 4),
            "r_squared_loo_cv": round(r2_cv, 4) if r2_cv is not None else None,
            "rmse": round(rmse, 3),
            "n_samples": n,
            "predictor_features": feat_names,
            "feature_importance": {
                feat_names[i]: round(float(model.feature_importances_[i]), 4)
                for i in range(len(feat_names))
            },
        }
    except Exception as exc:
        logger.debug("RF model fallback to linear: %s", exc)

    X_aug = np.column_stack([np.ones(n), X_pred])
    beta, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
    y_pred = X_aug @ beta
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    coeff_names = ["intercept"] + feat_names
    coefficients = {coeff_names[i]: float(beta[i]) for i in range(len(beta))}
    return {
        "method": "multivariate_regression",
        "coefficients": coefficients,
        "intercept": float(beta[0]),
        "r_squared": round(r2, 4),
        "rmse": round(float(np.sqrt(ss_res / n)), 3),
        "n_samples": n,
        "predictor_features": feat_names,
    }


def _lithology_validation(assignments: list[dict]) -> dict:
    """Simple lithotype coherence check per domain."""
    by_domain: dict[int, dict[str, int]] = {}
    for a in assignments:
        d_id = a["domain_id"]
        lith = (a.get("lithology") or "Unknown").strip() or "Unknown"
        by_domain.setdefault(d_id, {})
        by_domain[d_id][lith] = by_domain[d_id].get(lith, 0) + 1

    results = []
    for d_id, counts in by_domain.items():
        total = sum(counts.values())
        dominant = max(counts.items(), key=lambda x: x[1])
        pct = dominant[1] / total * 100 if total else 0
        results.append({
            "domain_id": d_id,
            "dominant_lithology": dominant[0],
            "dominant_pct": round(pct, 1),
            "geological_coherence": "High" if pct >= 80 else "Medium" if pct >= 50 else "Low",
        })
    return {"domains": results}


def auto_cluster_domains(pid: str, config: Optional[dict[str, Any]] = None) -> dict:
    """GMIE GADE — auto-cluster LIMS samples into geometallurgical domains."""
    config = config or {}
    weights = {**DEFAULT_FEATURE_WEIGHTS, **(config.get("feature_weights") or {})}

    samples = _fetch_joined_samples(pid)
    if len(samples) < 4:
        return {
            "status": "insufficient_data",
            "engine": "GMIE-GADE-v1",
            "message": f"Need >= 4 joined A1+B1+D1 samples, found {len(samples)}",
            "n_samples": len(samples),
            "domains": [],
        }

    X, raw, valid_samples, outlier_idx = _build_feature_matrix(samples, weights)
    if X.shape[0] < 4:
        return {
            "status": "insufficient_data",
            "engine": "GMIE-GADE-v1",
            "message": f"Only {X.shape[0]} samples with <= {int(MAX_MISSING_PCT*100)}% missing features",
            "n_samples": X.shape[0],
            "domains": [],
        }

    if outlier_idx:
        keep = [i for i in range(len(valid_samples)) if i not in set(outlier_idx)]
        if len(keep) >= 4:
            X = X[keep]
            raw = raw[keep]
            valid_samples = [valid_samples[i] for i in keep]

    pca = _pca_summary(X, FEATURES)
    k_info = _select_optimal_k(X, k_min=2, k_max=min(8, max(2, X.shape[0] // MIN_CLUSTER_SAMPLES + 1)))
    optimal_k = k_info["k"]
    labels, _ = _kmeans(X, optimal_k)
    sil = _silhouette_score(X, labels)

    domains = []
    for d_id in range(optimal_k):
        mask = labels == d_id
        d_raw = raw[mask]
        n_d = int(mask.sum())

        profile = {f: float(np.median(d_raw[:, i])) for i, f in enumerate(FEATURES)}
        profile_p10 = {f: float(np.percentile(d_raw[:, i], 10)) for i, f in enumerate(FEATURES)}
        profile_p90 = {f: float(np.percentile(d_raw[:, i], 90)) for i, f in enumerate(FEATURES)}
        profile_std = {f: float(np.std(d_raw[:, i])) for i, f in enumerate(FEATURES)}

        ore_class = _classify_ore(profile)
        bwi_cls = _bwi_class(profile.get("bwi_kwh_t", 14))
        rec_cls = _recovery_class(profile.get("au_recovery_pct", 90))
        risk, risk_level = _risk_score(profile)
        cn_eff = (profile.get("au_recovery_pct", 1) or 1) / max(profile.get("nacn_consumption_kg_t", 0.01), 0.01)

        rec_idx = FEATURES.index("au_recovery_pct")
        model = _build_rf_model(d_raw, d_raw[:, rec_idx])

        confidence = "High" if n_d >= MIN_CLUSTER_SAMPLES else "Low"
        warning = None if n_d >= MIN_CLUSTER_SAMPLES else (
            f"INSUFFICIENT DATA — {n_d} samples (minimum {MIN_CLUSTER_SAMPLES} required)"
        )

        domains.append({
            "domain_id": d_id,
            "domain_name": _auto_domain_name(profile),
            "ore_class": ore_class,
            "mineral_type": _mineral_type(profile),
            "bwi_class": bwi_cls,
            "recovery_class": rec_cls,
            "risk_score": risk,
            "risk_level": risk_level,
            "cyanide_efficiency_index": round(cn_eff, 1),
            "n_samples": n_d,
            "pct_of_total": round(n_d / len(valid_samples) * 100, 1),
            "confidence": confidence,
            "warning": warning,
            "profile": {k: round(v, 4) for k, v in profile.items()},
            "profile_p10": {k: round(v, 4) for k, v in profile_p10.items()},
            "profile_p90": {k: round(v, 4) for k, v in profile_p90.items()},
            "profile_std": {k: round(v, 4) for k, v in profile_std.items()},
            "recovery_model": model,
            "avg_recovery_pct": round(profile.get("au_recovery_pct", 0), 2),
            "avg_nacn_kg_t": round(profile.get("nacn_consumption_kg_t", 0), 3),
            "avg_bwi_kwh_t": round(profile.get("bwi_kwh_t", 0), 1),
        })

    sample_assignments = []
    for i, s in enumerate(valid_samples):
        d_id = int(labels[i])
        dom = next(d for d in domains if d["domain_id"] == d_id)
        sample_assignments.append({
            "sample_id": str(s.get("sample_id", "")),
            "lithology": s.get("lithology", ""),
            "zone": s.get("zone", ""),
            "domain_id": d_id,
            "domain_name": dom["domain_name"],
        })

    lithology = _lithology_validation(sample_assignments)
    viz = _build_gade_viz(X, raw, valid_samples, labels, domains, pca, pid)

    return {
        "status": "ok",
        "engine": "GMIE-GADE-v1",
        "n_samples": len(valid_samples),
        "n_outliers_flagged": len(outlier_idx),
        "n_domains": optimal_k,
        "silhouette_score": round(sil, 4),
        "cluster_selection": k_info,
        "pca": pca,
        "viz": viz,
        "feature_weights": weights,
        "lithology_validation": lithology,
        "domains": sorted(domains, key=lambda d: d["avg_recovery_pct"], reverse=True),
        "sample_assignments": sample_assignments,
    }


def _classify_ore(profile: dict) -> str:
    for cls, rule in ORE_CLASS_RULES.items():
        if cls != "free_milling" and rule(profile):
            return cls
    return "free_milling"


def predict_recovery(domain_result: dict, ore_features: dict) -> dict:
    if not domain_result.get("domains"):
        return {"predicted_recovery_pct": None, "domain": None, "error": "No domains available"}

    best_domain = None
    best_dist = float("inf")
    for dom in domain_result["domains"]:
        dist = 0.0
        profile = dom["profile"]
        for f in FEATURES:
            v_ore = float(ore_features.get(f, 0))
            v_dom = profile.get(f, 0)
            std = dom["profile_std"].get(f, 1) or 1
            dist += ((v_ore - v_dom) / std) ** 2
        dist = math.sqrt(dist)
        if dist < best_dist:
            best_dist = dist
            best_domain = dom

    model = best_domain["recovery_model"]
    profile = best_domain["profile"]
    train_ranges = {
        f: (best_domain["profile_p10"].get(f), best_domain["profile_p90"].get(f))
        for f in PREDICTOR_FEATURES
    }
    extrapolation = any(
        ore_features.get(f) is not None and train_ranges[f][0] is not None
        and (float(ore_features[f]) < train_ranges[f][0] or float(ore_features[f]) > train_ranges[f][1])
        for f in PREDICTOR_FEATURES if f in ore_features
    )

    if model.get("method") == "random_forest":
        pred = float(best_domain["avg_recovery_pct"])
    elif model.get("coefficients"):
        pred = model.get("intercept", 0)
        for feat in model.get("predictor_features", []):
            pred += model["coefficients"].get(feat, 0) * float(ore_features.get(feat, 0))
    else:
        pred = float(best_domain["avg_recovery_pct"])

    pred = max(0.0, min(100.0, float(pred)))
    result = {
        "predicted_recovery_pct": round(pred, 2),
        "confidence_interval": {
            "p10": round(max(0, pred - 4), 2),
            "p90": round(min(100, pred + 4), 2),
        },
        "domain": best_domain["domain_name"],
        "ore_class": best_domain["ore_class"],
        "model_r_squared": model.get("r_squared"),
        "method": model.get("method", "domain_average"),
        "distance_to_centroid": round(best_dist, 4),
    }
    if extrapolation:
        result["extrapolation_warning"] = "EXTRAPOLATION WARNING — input outside domain P10–P90 training range"
    return result


def classify_blocks(pid: str, domain_result: dict) -> list[dict]:
    if not domain_result.get("domains"):
        return []

    blocks = qall(
        "SELECT id, i_index AS x_index, j_index AS y_index, k_index AS z_index, "
        "x_center, y_center, z_center, "
        "tonnage, grade_au, rock_type "
        "FROM blocks WHERE config_id = ("
        "  SELECT id FROM block_model_configs WHERE project_id = %s "
        "  ORDER BY created_at DESC LIMIT 1"
        ")",
        (pid,),
    )
    if not blocks:
        return []

    domain_by_name = {d["domain_name"]: d for d in domain_result["domains"]}
    avg_profile: dict[str, float] = {}
    for f in FEATURES:
        vals = [d["profile"].get(f, 0) for d in domain_result["domains"]]
        avg_profile[f] = float(np.mean(vals)) if vals else 0.0

    results = []
    for blk in blocks:
        grade = float(blk.get("grade_au") or 0)
        tonnage = float(blk.get("tonnage") or 0)
        ore_features = dict(avg_profile)
        ore_features["au_g_t"] = grade
        rock = (blk.get("rock_type") or "").lower()
        if "sulf" in rock or "sulph" in rock:
            ore_features["s_total_pct"] = max(ore_features.get("s_total_pct", 1.5), 2.5)
        if "ox" in rock:
            ore_features["s_total_pct"] = min(ore_features.get("s_total_pct", 1.5), 0.4)

        prediction = predict_recovery(domain_result, ore_features)
        dom_name = prediction.get("domain", "Unknown")
        dom = domain_by_name.get(dom_name, domain_result["domains"][0])

        results.append({
            "block_id": str(blk["id"]),
            "x": blk["x_index"],
            "y": blk["y_index"],
            "z": blk["z_index"],
            "tonnage": tonnage,
            "grade_au": grade,
            "rock_type": blk.get("rock_type"),
            "predicted_recovery_pct": prediction.get("predicted_recovery_pct", 90),
            "domain": dom_name,
            "ore_class": prediction.get("ore_class", dom.get("ore_class")),
            "nacn_kg_t": dom.get("avg_nacn_kg_t", 0.35),
            "bwi_kwh_t": dom.get("avg_bwi_kwh_t", 14),
            "contained_oz": round(tonnage * grade * TROY_OZ_PER_GRAM, 2) if grade > 0 else 0,
            "recoverable_oz": round(
                tonnage * grade * (prediction.get("predicted_recovery_pct", 90) / 100) * TROY_OZ_PER_GRAM, 2
            ) if grade > 0 else 0,
        })

    return sorted(results, key=lambda b: (-b.get("z", 0), b.get("y", 0), b.get("x", 0)))
