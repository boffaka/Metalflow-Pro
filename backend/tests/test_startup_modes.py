from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.settings import reset_settings_cache


class StartupModeTests(unittest.TestCase):
    def test_startup_prefers_migrations_when_enabled(self) -> None:
        with patch.dict(os.environ, {"AUTO_MIGRATE": "1", "BOOTSTRAP_SCHEMA": "0"}, clear=False):
            import importlib
            reset_settings_cache()
            module = importlib.import_module("backend.main")
            importlib.reload(module)

            with patch.object(module, "run_migrations") as run_migrations, patch.object(module, "bootstrap_schema") as bootstrap_schema, patch.object(module, "seed_admin_user") as seed_admin_user:
                module.startup()

            run_migrations.assert_called_once()
            bootstrap_schema.assert_not_called()
            seed_admin_user.assert_called_once()

    def test_startup_bootstraps_schema_by_default(self) -> None:
        with patch.dict(os.environ, {"AUTO_MIGRATE": "0", "BOOTSTRAP_SCHEMA": "1"}, clear=False):
            import importlib
            reset_settings_cache()
            module = importlib.import_module("backend.main")
            importlib.reload(module)

            with patch.object(module, "run_migrations") as run_migrations, patch.object(module, "bootstrap_schema") as bootstrap_schema, patch.object(module, "seed_admin_user") as seed_admin_user:
                module.startup()

            run_migrations.assert_not_called()
            bootstrap_schema.assert_called_once()
            seed_admin_user.assert_called_once()

    def test_startup_can_seed_only_when_both_schema_modes_disabled(self) -> None:
        with patch.dict(os.environ, {"AUTO_MIGRATE": "0", "BOOTSTRAP_SCHEMA": "0"}, clear=False):
            import importlib
            reset_settings_cache()
            module = importlib.import_module("backend.main")
            importlib.reload(module)

            with patch.object(module, "run_migrations") as run_migrations, patch.object(module, "bootstrap_schema") as bootstrap_schema, patch.object(module, "seed_admin_user") as seed_admin_user:
                module.startup()

            run_migrations.assert_not_called()
            bootstrap_schema.assert_not_called()
            seed_admin_user.assert_called_once()


if __name__ == "__main__":
    unittest.main()
