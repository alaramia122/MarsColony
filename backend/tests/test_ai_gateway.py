import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.ai_gateway import (
    ProjectProposal, ProviderError, YandexAIStudioProvider, configured_provider,
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

    def test_yandex_provider_configuration_requires_api_key_and_folder(self):
        with patch.dict(os.environ, {
            "MARS_AI_PROVIDER": "yandex_ai_studio",
            "MARS_AI_YANDEX_API_KEY": "test-key",
        }, clear=True):
            with self.assertRaises(ProviderError):
                configured_provider()

    def test_yandex_provider_uses_native_completion_contract(self):
        import io
        import json
        from unittest.mock import patch
        response = io.BytesIO(json.dumps({
            "alternatives": [{
                "status": "ALTERNATIVE_STATUS_FINAL",
                "message": {"role": "assistant", "text": json.dumps({"ok": True})}
            }]
        }).encode("utf-8"))
        with patch("backend.app.ai_gateway.urlopen", return_value=response) as send:
            provider = YandexAIStudioProvider("secret", "folder-1", "yandexgpt/latest", 5)
            self.assertEqual(provider.generate_json("system", {"task": "test"}), {"ok": True})
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, "https://ai.api.cloud.yandex.net/foundationModels/v1/completion")
            self.assertEqual(request.get_header("Authorization"), "Api-Key secret")
            self.assertEqual(request.get_header("X-folder-id"), "folder-1")

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
