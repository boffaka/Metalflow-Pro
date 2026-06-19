from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.models import ProjectIn, SampleIn, UserCreate


class ModelValidationTests(unittest.TestCase):
    def test_project_accepts_valid_numeric_ranges(self) -> None:
        project = ProjectIn(
            project_name="Industrial Project",
            project_code="IND-001",
            availability_pct=95,
            operating_hours_day=22,
            electricity_rate=0.12,
        )
        self.assertEqual(project.availability_pct, 95)
        self.assertEqual(project.operating_hours_day, 22)

    def test_project_rejects_invalid_availability(self) -> None:
        with self.assertRaises(ValidationError):
            ProjectIn(project_name="Industrial Project", project_code="IND-001", availability_pct=120)

    def test_project_rejects_operating_hours_above_24(self) -> None:
        with self.assertRaises(ValidationError):
            ProjectIn(project_name="Industrial Project", project_code="IND-001", operating_hours_day=25)

    def test_user_password_requires_minimum_length(self) -> None:
        with self.assertRaises(ValidationError):
            UserCreate(email="ops@example.com", password="short")

    def test_sample_rejects_negative_mass(self) -> None:
        with self.assertRaises(ValidationError):
            SampleIn(sample_id_display="S-01", mass_kg=-5)


if __name__ == "__main__":
    unittest.main()
