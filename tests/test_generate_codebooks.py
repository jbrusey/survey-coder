import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from survey_coder.generate_codebooks import call_llm, clean_comment, parse_json_response, save_markdown_codebook


class GenerateCodebooksTests(unittest.TestCase):
    def test_clean_comment_rejects_placeholders(self):
        self.assertIsNone(clean_comment("N/A", 3))
        self.assertEqual(clean_comment("Useful feedback", 3), "Useful feedback")

    def test_parse_json_response_accepts_code_fences(self):
        self.assertEqual(parse_json_response('```json\n{"themes": []}\n```'), {"themes": []})

    @patch("survey_coder.generate_codebooks.OpenAI")
    def test_openwebui_uses_its_api_key_and_compatible_endpoint(self, openai):
        openai.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(finish_reason="stop", message=Mock(content='{"themes": []}'))])
        with patch.dict("os.environ", {"OPENWEBUI_API_KEY": "secret"}, clear=False):
            response = call_llm("prompt", "system", "model", 0, "http://localhost:3000/api", "openwebui")

        self.assertEqual(response, '{"themes": []}')
        openai.assert_called_once_with(api_key="secret", base_url="http://localhost:3000/api",
                                       timeout=120, max_retries=0)

    @patch("survey_coder.generate_codebooks.OpenAI")
    def test_openwebui_allows_servers_without_authentication(self, openai):
        openai.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(finish_reason="stop", message=Mock(content='{"themes": []}'))])
        with patch.dict("os.environ", {}, clear=True):
            call_llm("prompt", "system", "model", 0, "http://localhost:3000/api", "openwebui")

        self.assertEqual(openai.call_args.kwargs["api_key"], "dummy-key-for-local-llm")

    @patch("survey_coder.generate_codebooks.OpenAI")
    def test_openwebui_accepts_completion_text_returned_directly(self, openai):
        openai.return_value.chat.completions.create.return_value = '{"themes": []}'

        response = call_llm("prompt", "system", "model", 0,
                            "http://localhost:3000/api", "openwebui")

        self.assertEqual(response, '{"themes": []}')

    @patch("survey_coder.generate_codebooks.OpenAI")
    def test_openwebui_accepts_dictionary_openai_response(self, openai):
        openai.return_value.chat.completions.create.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"themes": []}'}}]
        }

        response = call_llm("prompt", "system", "model", 0,
                            "http://localhost:3000/api", "openwebui")

        self.assertEqual(response, '{"themes": []}')

    def test_save_codebook_preserves_existing_file_unless_replacement_is_explicit(self):
        codebook = {"codebook_name": "Draft", "stream": "Comments", "codes": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comments_codebook.md"
            path.write_text("reviewed", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                save_markdown_codebook(str(path), codebook)
            self.assertEqual(path.read_text(encoding="utf-8"), "reviewed")

            save_markdown_codebook(str(path), codebook, replace=True)
            self.assertIn("# Draft", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
