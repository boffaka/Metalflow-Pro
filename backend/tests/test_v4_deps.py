# backend/tests/test_v4_deps.py
"""Verify all v4 Python dependencies are importable."""

def test_celery_importable():
    from packaging.version import Version
    import celery
    assert Version(celery.__version__) >= Version("5.4.0")

def test_scipy_importable():
    import scipy
    assert hasattr(scipy, "__version__")

def test_pymoo_importable():
    import pymoo
    assert hasattr(pymoo, "__version__")

def test_sklearn_importable():
    import sklearn
    assert hasattr(sklearn, "__version__")

def test_joblib_importable():
    import joblib
    assert hasattr(joblib, "__version__")

def test_numpy_importable():
    from packaging.version import Version
    import numpy as np
    assert Version(np.__version__) >= Version("1.26.4")

def test_pandas_importable():
    import pandas as pd
    assert pd.__version__ >= "2.0"

def test_redis_importable():
    import redis
    assert hasattr(redis, "__version__")

def test_httpx_importable():
    from packaging.version import Version
    import httpx
    assert Version(httpx.__version__) >= Version("0.27.0")

def test_websockets_importable():
    from packaging.version import Version
    import websockets
    assert Version(websockets.__version__) >= Version("12.0")
