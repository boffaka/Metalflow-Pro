"""
Tests for db.py security hardening:
  - build_update_sets requires `allowed` keyword
  - build_update_sets rejects unlisted columns
  - build_update_sets validates column name patterns
  - paginated_qall caps the limit
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_build_update_sets_requires_allowed():
    from db import build_update_sets
    with pytest.raises(TypeError):
        build_update_sets({"name": "test"})


def test_build_update_sets_rejects_unlisted_columns():
    from db import build_update_sets
    fields, vals = build_update_sets(
        {"name": "ok", "evil_col": "hack"},
        allowed=frozenset(["name"]),
    )
    assert len(fields) == 1
    assert fields[0] == "name=%s"
    assert vals == ["ok"]


def test_build_update_sets_validates_column_names():
    from db import build_update_sets
    with pytest.raises(ValueError, match="Invalid column name"):
        build_update_sets(
            {"Robert'; DROP TABLE--": "hack"},
            allowed=frozenset(["Robert'; DROP TABLE--"]),
        )


def test_build_update_sets_empty_data():
    from db import build_update_sets
    fields, vals = build_update_sets({}, allowed=frozenset(["name"]))
    assert fields == []
    assert vals == []


def test_build_update_sets_valid_columns():
    from db import build_update_sets
    fields, vals = build_update_sets(
        {"name": "a", "description": "b", "status": "c"},
        allowed=frozenset(["name", "description", "status"]),
    )
    assert len(fields) == 3
    assert all("=%s" in f for f in fields)
    assert vals == ["a", "b", "c"]


def test_paginated_qall_caps_limit():
    from db import paginated_qall
    from unittest.mock import patch
    with patch("db.qall", return_value=[]) as mock:
        paginated_qall("SELECT 1", (), limit=5000, offset=0)
        call_params = mock.call_args[0][1]
        assert call_params[-2] == 1000
        assert call_params[-1] == 0


def test_paginated_qall_clamps_negative_offset():
    from db import paginated_qall
    from unittest.mock import patch
    with patch("db.qall", return_value=[]) as mock:
        paginated_qall("SELECT 1", (), limit=10, offset=-5)
        call_params = mock.call_args[0][1]
        assert call_params[-2] == 10
        assert call_params[-1] == 0


def test_paginated_qall_clamps_zero_limit():
    from db import paginated_qall
    from unittest.mock import patch
    with patch("db.qall", return_value=[]) as mock:
        paginated_qall("SELECT 1", (), limit=0, offset=0)
        call_params = mock.call_args[0][1]
        assert call_params[-2] == 1  # min limit is 1


def test_paginated_qall_no_params():
    from db import paginated_qall
    from unittest.mock import patch
    with patch("db.qall", return_value=[]) as mock:
        paginated_qall("SELECT 1", limit=50, offset=10)
        call_sql = mock.call_args[0][0]
        call_params = mock.call_args[0][1]
        assert "LIMIT %s OFFSET %s" in call_sql
        assert call_params == (50, 10)


def test_paginated_qall_with_existing_params():
    from db import paginated_qall
    from unittest.mock import patch
    with patch("db.qall", return_value=[]) as mock:
        paginated_qall("SELECT * FROM t WHERE id=%s", ("abc",), limit=25, offset=5)
        call_params = mock.call_args[0][1]
        assert call_params == ("abc", 25, 5)
