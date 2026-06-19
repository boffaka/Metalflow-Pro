from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.routes.blockmodel import BlockIn, BlockModelConfigIn


class BlockModelValidationTests(unittest.TestCase):
    def test_block_model_config_rejects_non_positive_block_size(self) -> None:
        with self.assertRaises(ValidationError):
            BlockModelConfigIn(x_block_size=0)

    def test_block_rejects_negative_grade(self) -> None:
        with self.assertRaises(ValidationError):
            BlockIn(grade_au=-0.1)

    def test_block_rejects_non_positive_density(self) -> None:
        with self.assertRaises(ValidationError):
            BlockIn(density=0)

    def test_block_accepts_structured_attributes(self) -> None:
        block = BlockIn(attributes={"domain": "ore", "bench": 120})
        self.assertEqual(block.attributes["domain"], "ore")
        self.assertEqual(block.attributes["bench"], 120)


if __name__ == "__main__":
    unittest.main()
