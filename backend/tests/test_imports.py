from __future__ import annotations

import importlib
import unittest


class ImportRegressionTests(unittest.TestCase):
    def test_backend_main_imports_as_package(self) -> None:
        module = importlib.import_module("backend.main")
        self.assertEqual(module.app.title, "MPDPMS API")

    def test_router_modules_import_as_package(self) -> None:
        for module_name in (
            "backend.routes.admin",
            "backend.routes.risks",
            "backend.routes.costs",
            "backend.routes.stagegates",
            "backend.routes.ni43101",
            "backend.routes.blockmodel",
        ):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)


if __name__ == "__main__":
    unittest.main()
