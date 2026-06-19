"""Tests for centralized physical constants."""


def test_constants_exist():
    from constants import (
        FARADAY_C_MOL,
        TROY_OZ_PER_GRAM,
        WATER_SG,
        M_AU_G_MOL,
        AU_ELECTRONS,
        AP_FACTOR_PYRITE,
    )
    assert FARADAY_C_MOL == 96_485
    assert abs(TROY_OZ_PER_GRAM - 1 / 31.1035) < 1e-10
    assert WATER_SG == 1.0
    assert M_AU_G_MOL == 196.97
    assert AU_ELECTRONS == 3
    assert AP_FACTOR_PYRITE == 31.25


def test_troy_oz_conversion_roundtrip():
    from constants import TROY_OZ_PER_GRAM
    grams = 31.1035
    oz = grams * TROY_OZ_PER_GRAM
    assert abs(oz - 1.0) < 1e-10
