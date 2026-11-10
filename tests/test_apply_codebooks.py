import unittest
from unittest.mock import Mock, patch

from survey_coder.apply_codebooks import call_llm


class ApplyCodebooksProviderTests(unittest.TestCase):
    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_openwebui_accepts_completion_text_returned_directly(self, openai):
        openai.return_value.chat.completions.create.return_value = '{"results": []}'

        response = call_llm("prompt", "system", "model", 0,
                            "http://localhost:3000/api", "openwebui")

        self.assertEqual(response, '{"results": []}')

    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_openwebui_reports_unexpected_response_shape(self, openai):
        openai.return_value.chat.completions.create.return_value = ["unexpected"]

        with self.assertRaisesRegex(TypeError, "OpenWebUI returned an unsupported response shape"):
            call_llm("prompt", "system", "model", 0,
                     "http://localhost:3000/api", "openwebui")

    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_openai_sdk_response_remains_supported(self, openai):
        openai.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(finish_reason="stop", message=Mock(content='{"results": []}'))])

        response = call_llm("prompt", "system", "model", 0)

        self.assertEqual(response, '{"results": []}')


if __name__ == "__main__":
    unittest.main()
