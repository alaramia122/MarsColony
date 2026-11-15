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


    def test_provider_registry_supports_multiple_openai_compatible_endpoints(self):
        import json
        config = {
            "local": {"type": "openai_compatible", "base_url": "http://ollama:11434/v1",
                      "model": "qwen2.5:7b", "requires_api_key": False,
                      "capabilities": {"json_object": False}},
            "cloud": {"type": "openai_compatible", "base_url": "https://api.example.test/v1",
                      "model": "reasoning-model", "api_key_env": "CLOUD_TEST_KEY"},
        }
        with patch.dict(os.environ, {
            "MARS_AI_PROVIDERS": json.dumps(config),
            "MARS_AI_TASK_ROUTING": json.dumps({"briefing": ["local", "cloud"]}),
            "CLOUD_TEST_KEY": "test-secret",
        }, clear=True):
            provider = configured_provider("briefing")
            self.assertEqual(len(provider.providers), 2)
            self.assertEqual(provider.providers[0].model, "qwen2.5:7b")
            self.assertFalse(provider.providers[0].json_mode)

    def test_provider_registry_rejects_unknown_routed_provider(self):
        import json
        with patch.dict(os.environ, {
            "MARS_AI_PROVIDERS": json.dumps({"local": {
                "type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "model", "requires_api_key": False}}),
            "MARS_AI_TASK_ROUTING": json.dumps({"briefing": "missing"}),
        }, clear=True):
            with self.assertRaises(ProviderError):
                configured_provider("briefing")

    def test_provider_chain_uses_next_provider_after_failure(self):
        from backend.app.ai_gateway import ProviderChain
        class BrokenProvider:
            def generate_json(self, system_prompt, payload):
                raise ProviderError("offline")
        class WorkingProvider:
            def generate_json(self, system_prompt, payload):
                return {"ok": True}
        chain = ProviderChain([BrokenProvider(), WorkingProvider()])
        self.assertEqual(chain.generate_json("system", {}), {"ok": True})

    def test_provider_profile_requires_http_url_and_model(self):
        import json
        with patch.dict(os.environ, {
            "MARS_AI_PROVIDERS": json.dumps({"bad": {
                "type": "openai_compatible", "base_url": "file:///tmp",
                "model": "model", "requires_api_key": False}})
        }, clear=True):
            with self.assertRaises(ProviderError):
                configured_provider()

if __name__ == "__main__":
    unittest.main()
