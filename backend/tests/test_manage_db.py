from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend import manage_db


class ManageDbTests(unittest.TestCase):
    def test_usage_without_command(self) -> None:
        self.assertEqual(manage_db.main(["manage_db.py"]), 1)

    def test_unknown_command_returns_error(self) -> None:
        self.assertEqual(manage_db.main(["manage_db.py", "nope"]), 1)

    def test_upgrade_dispatches_to_alembic(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/mpdpms", "ADMIN_PASSWORD": "Str0ngPass!"}, clear=False):
            with patch("alembic.command.upgrade") as upgrade:
                result = manage_db.main(["manage_db.py", "upgrade"])
        self.assertEqual(result, 0)
        upgrade.assert_called_once()


if __name__ == "__main__":
    unittest.main()
