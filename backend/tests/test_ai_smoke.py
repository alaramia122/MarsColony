import unittest
from unittest.mock import patch

from app.ai_gateway import ProviderError
from app.ai_smoke import run_smoke_test


class FakeProvider:
    def __init__(self, response):
        self.response = response

    def generate_json(self, system_prompt, payload):
        self.system_prompt = system_prompt
        self.payload = payload
        return self.response


class AISmokeTestTests(unittest.TestCase):
    def test_smoke_test_accepts_valid_structured_response(self):
        provider = FakeProvider({"status": "ok", "message": "Связь работает"})
        with patch("app.ai_smoke.configured_provider", return_value=provider) as configured:
            result = run_smoke_test("briefing")

        configured.assert_called_once_with("briefing")
        self.assertEqual(result, {"status": "ok", "message": "Связь работает"})
        self.assertIn("JSON object", provider.system_prompt)
        self.assertIn("purpose", provider.payload)

    def test_smoke_test_rejects_invalid_provider_contract(self):
        provider = FakeProvider({"status": "failed", "message": "Нет"})
        with patch("app.ai_smoke.configured_provider", return_value=provider):
            with self.assertRaises(ProviderError):
                run_smoke_test("briefing")

    def test_smoke_test_rejects_non_text_message(self):
        provider = FakeProvider({"status": "ok", "message": None})
        with patch("app.ai_smoke.configured_provider", return_value=provider):
            with self.assertRaises(ProviderError):
                run_smoke_test("briefing")


if __name__ == "__main__":
    unittest.main()
