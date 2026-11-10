import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from survey_coder.apply_codebooks import add_code_columns, call_llm, process_stream


class ApplyCodebooksProviderTests(unittest.TestCase):
    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_openai_compatible_request_uses_configured_thinking_and_json_mode(self, openai):
        openai.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(finish_reason="stop", message=Mock(content='{"results": []}'))])

        call_llm("prompt", "system", "qwen3.8:latest", 0,
                 "http://localhost:11434/v1", think=False)

        request = openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(request["extra_body"], {"think": False})
        self.assertEqual(request["response_format"], {"type": "json_object"})

    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_json_mode_can_be_disabled(self, openai):
        openai.return_value.chat.completions.create.return_value = Mock(
            choices=[Mock(finish_reason="stop", message=Mock(content='{"results": []}'))])

        call_llm("prompt", "system", "model", 0, json_mode=False)

        request = openai.return_value.chat.completions.create.call_args.kwargs
        self.assertNotIn("response_format", request)

    @patch("survey_coder.apply_codebooks.genai.Client")
    def test_google_provider_uses_supported_genai_client(self, client_class):
        client_class.return_value.models.generate_content.return_value.text = '{"results": []}'
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "secret"}):
            response = call_llm("prompt", "system", "gemini-model", 0.2,
                                provider="google", max_tokens=123)

        self.assertEqual(response, '{"results": []}')
        client_class.assert_called_once_with(api_key="secret")
        request = client_class.return_value.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-model")
        self.assertEqual(request["contents"], "system\n\nprompt")
        self.assertEqual(request["config"].response_mime_type, "application/json")
        self.assertEqual(request["config"].max_output_tokens, 123)

    @patch("survey_coder.apply_codebooks.OpenAI")
    def test_openwebui_accepts_completion_text_returned_directly(self, openai):
        openai.return_value.chat.completions.create.return_value = '{"results": []}'

        response = call_llm("prompt", "system", "model", 0,
                            "http://localhost:3000/api", "openwebui")

        self.assertEqual(response, '{"results": []}')


class DiagnosticLogTests(unittest.TestCase):
    @patch("survey_coder.apply_codebooks.call_llm", return_value='{"results": []}')
    def test_process_stream_appends_prompt_and_response_as_jsonl(self, call_llm_mock):
        with tempfile.TemporaryDirectory() as directory:
            codebook_path = Path(directory) / "codebook.md"
            codebook_path.write_text("## Helpful", encoding="utf-8")
            log_path = Path(directory) / "diagnostics.jsonl"
            args = Namespace(
                max_rows=None, batch_size=1, model="test", temperature=0,
                openai_base_url=None, provider="openai", max_output_tokens=None,
                think=None, json_mode=True, diagnostic_log=str(log_path),
            )
            prompts = {"system": "Code {stream_name}", "batch": "{comments}"}
            frame = pd.DataFrame([{"ID": "R1", "Comment": "A useful comment"}])

            process_stream(
                frame,
                {"name": "Feedback", "codebook": str(codebook_path), "column": "Comment"},
                "ID", args, "survey feedback", prompts,
            )

            entry = json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(entry["stream"], "Feedback")
        self.assertEqual(entry["batch"], 1)
        self.assertIn("A useful comment", entry["messages"][1]["content"])
        self.assertEqual(entry["response"], '{"results": []}')
        call_llm_mock.assert_called_once()


class CodeColumnTests(unittest.TestCase):
    def test_code_columns_keep_names_values_and_stream_order(self):
        frame = pd.DataFrame({"ID": ["R1", "R2"], "Comment": ["one", "two"]})
        results = [
            ({"name": "Positive", "prefix": "Pos"},
             {"R1": ["Helpful", "Clear"], "R2": ["Clear"]}, ["Helpful", "Clear"]),
            ({"name": "Improvement", "prefix": "Imp"},
             {"R2": ["Pace"]}, ["Pace"]),
        ]

        coded = add_code_columns(frame, "ID", results)

        self.assertEqual(list(coded.columns), [
            "ID", "Comment", "Pos_Helpful", "Pos_Clear", "Pos_Codes_Applied",
            "Imp_Pace", "Imp_Codes_Applied",
        ])
        self.assertEqual(coded[["Pos_Helpful", "Pos_Clear", "Imp_Pace"]].values.tolist(),
                         [[1, 1, 0], [0, 1, 1]])
        self.assertEqual(coded["Pos_Codes_Applied"].tolist(), ["Helpful, Clear", "Clear"])
        self.assertEqual(coded["Imp_Codes_Applied"].tolist(), ["", "Pace"])

    def test_many_code_columns_do_not_emit_fragmentation_warning(self):
        frame = pd.DataFrame({"ID": ["R1"]})
        codes = [f"Code_{index}" for index in range(150)]
        results = [({"name": "Feedback"}, {"R1": codes}, codes)]

        with self.assertNoLogs("pandas", level="WARNING"):
            coded = add_code_columns(frame, "ID", results)

        self.assertEqual(coded.shape[1], len(codes) + 2)


class UnknownCodeWarningTests(unittest.TestCase):
    @patch("survey_coder.apply_codebooks.call_llm", return_value=(
            '{"results": [{"id": "R1", "codes": ["Helpful", "Unclear / vague", '
            '"Unclear / vague"]}]}'))
    def test_warns_with_stream_ids_and_counts_while_retaining_valid_codes(self, _call_llm):
        with tempfile.TemporaryDirectory() as directory:
            codebook_path = Path(directory) / "codebook.md"
            codebook_path.write_text("## Helpful\n## Unclear_vague\n", encoding="utf-8")
            args = Namespace(
                max_rows=None, batch_size=1, model="test", temperature=0,
                openai_base_url=None, provider="openai", max_output_tokens=None,
                think=None, json_mode=True, diagnostic_log=None,
            )
            frame = pd.DataFrame([{"ID": "R1", "Comment": "A useful comment"}])
            prompts = {"system": "Code {stream_name}", "batch": "{comments}"}

            with self.assertLogs("survey_coder.apply_codebooks", level="WARNING") as logs:
                results, codes = process_stream(
                    frame, {"name": "Feedback", "codebook": str(codebook_path),
                            "column": "Comment"},
                    "ID", args, "survey feedback", prompts,
                )

        self.assertEqual(codes, ["Helpful", "Unclear_vague"])
        self.assertEqual(results, {"R1": ["Helpful"]})
        warning = "\n".join(logs.output)
        self.assertIn("Feedback", warning)
        self.assertIn("R1", warning)
        self.assertIn("Unclear / vague", warning)
        self.assertIn("(2)", warning)

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
