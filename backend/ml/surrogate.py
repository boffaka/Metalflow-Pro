# backend/ml/surrogate.py
"""
Random Forest surrogate model for fast simulation approximation.

Trained on rigorous engine outputs (LHS sampling, 1,000 design points).
10x faster than rigorous engine — used for interactive parameter exploration.
Serialized to model_artifacts table (BYTEA column) with joblib.
"""
from __future__ import annotations
import io
from typing import Optional
import numpy as np


class SurrogateModel:
    """
    Random Forest surrogate wrapping sklearn RandomForestRegressor.

    Feature order (must be consistent between train and predict):
      [0] p80_um
      [1] nacn_mg_l
      [2] do_mg_l
      [3] ph
      [4] srt_h
      [5] tph
      [6] bwi_kwh_t  (Bond Work Index)
      [7] f80_um
    """
    FEATURE_NAMES = ["p80_um", "nacn_mg_l", "do_mg_l", "ph",
                     "srt_h", "tph", "bwi_kwh_t", "f80_um"]

    def __init__(self, n_estimators: int = 200, random_state: int = 42):
        from sklearn.ensemble import RandomForestRegressor
        self._rf = RandomForestRegressor(
            n_estimators=n_estimators,
            random_state=random_state,
            n_jobs=-1,
        )
        self.is_trained = False
        self.training_score: Optional[float] = None

    def train(self, X: np.ndarray, y: np.ndarray) -> float:
        """
        Train the surrogate.

        Args:
            X: Feature matrix shape (n_samples, 8) — see FEATURE_NAMES
            y: Target array shape (n_samples,) — recovery % or energy kWh/t
        Returns:
            R² score on training set
        """
        self._rf.fit(X, y)
        self.is_trained = True
        self.training_score = float(self._rf.score(X, y))
        return self.training_score

    def predict(self, X: np.ndarray) -> dict:
        """
        Predict with ±2σ confidence interval using forest variance.

        Args:
            X: Feature matrix (n_samples, 8)
        Returns:
            dict: {value, ci_low, ci_high, std}
        """
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call train() first.")
        preds = np.array([tree.predict(X) for tree in self._rf.estimators_])
        mean = preds.mean(axis=0)
        std = preds.std(axis=0)
        return {
            "value": float(mean[0]),
            "ci_low": float(mean[0] - 2 * std[0]),
            "ci_high": float(mean[0] + 2 * std[0]),
            "std": float(std[0]),
        }

    def serialize(self) -> bytes:
        """Serialize model to bytes for storage in model_artifacts.artifact."""
        import joblib
        buf = io.BytesIO()
        joblib.dump(self._rf, buf)
        return buf.getvalue()

    @classmethod
    def deserialize(cls, blob: bytes) -> "SurrogateModel":
        """Deserialize from bytes retrieved from model_artifacts table."""
        import joblib
        instance = cls.__new__(cls)
        instance._rf = joblib.load(io.BytesIO(blob))
        instance.is_trained = True
        instance.training_score = None
        return instance
