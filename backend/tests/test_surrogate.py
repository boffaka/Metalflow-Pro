# backend/tests/test_surrogate.py
"""Tests for Random Forest surrogate model."""
import pytest

def get_surrogate():
    try:
        from backend.ml.surrogate import SurrogateModel
    except ImportError:
        from ml.surrogate import SurrogateModel
    return SurrogateModel


def test_surrogate_trains_on_synthetic_data():
    SM = get_surrogate()
    model = SM()
    import numpy as np
    np.random.seed(42)
    n = 200
    X = np.column_stack([
        np.random.uniform(50, 200, n),
        np.random.uniform(200, 500, n),
        np.random.uniform(5, 12, n),
        np.random.uniform(9.5, 11, n),
        np.random.uniform(12, 48, n),
        np.random.uniform(200, 2000, n),
        np.random.uniform(10, 20, n),
        np.random.uniform(1000, 5000, n),
    ])
    y = np.random.uniform(75, 95, n)
    model.train(X, y)
    assert model.is_trained

def test_surrogate_predict_returns_value_and_confidence():
    SM = get_surrogate()
    import numpy as np
    model = SM()
    X_train = np.random.uniform(0, 1, (100, 8))
    y_train = np.random.uniform(80, 95, 100)
    model.train(X_train, y_train)

    sample = np.array([[75, 350, 8, 10.5, 24, 500, 14, 3000]], dtype=float)
    result = model.predict(sample)
    assert "value" in result
    assert "ci_low" in result
    assert "ci_high" in result
    assert result["ci_low"] <= result["value"] <= result["ci_high"]

def test_surrogate_serialization():
    SM = get_surrogate()
    import numpy as np
    model = SM()
    X = np.random.uniform(0, 1, (50, 8))
    y = np.random.uniform(80, 95, 50)
    model.train(X, y)

    blob = model.serialize()
    assert isinstance(blob, bytes)
    assert len(blob) > 0

    model2 = SM.deserialize(blob)
    assert model2.is_trained
