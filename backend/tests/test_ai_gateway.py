import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.ai_gateway import (
    ProjectProposal, ProviderError, configured_provider,
)


class AIGatewayContractTests(unittest.TestCase):
    def test_valid_proposal_is_typed_and_extra_fields_are_rejected(self):
        proposal = ProjectProposal.model_validate({
            "title": "Резервная линия кислорода",
            "description": "Подготовить проект резервной линии.",
            "project_type": "infrastructure",
            "domain_codes": ["life_support"],
            "duration_periods": 2,
            "budget_credits": 100,
        })
        self.assertEqual(proposal.duration_periods, 2)
        self.assertEqual(proposal.resource_requirements, {})

        with self.assertRaises(ValidationError):
            ProjectProposal.model_validate({
                "title": "Резервная линия кислорода",
                "description": "Проект.",
                "project_type": "infrastructure",
                "domain_codes": ["life_support"],
                "duration_periods": 2,
                "unexpected_command": "advance_period",
            })

    def test_invalid_bounds_are_rejected(self):
        with self.assertRaises(ValidationError):
            ProjectProposal.model_validate({
                "title": "Некорректный проект",
                "description": "Проект.",
                "project_type": "infrastructure",
                "domain_codes": ["energy"],
                "duration_periods": 0,
            })

    def test_provider_is_disabled_without_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError):
                configured_provider()

    def test_openai_compatible_provider_requires_all_configuration(self):
        with patch.dict(os.environ, {
            "MARS_AI_PROVIDER": "openai_compatible",
            "MARS_AI_BASE_URL": "https://example.invalid/v1",
            "MARS_AI_MODEL": "model-name",
        }, clear=True):
            with self.assertRaises(ProviderError):
                configured_provider()


if __name__ == "__main__":
    unittest.main()
