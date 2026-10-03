import tempfile
import unittest
from pathlib import Path

from survey_coder.generate_codebooks import clean_comment, parse_json_response, save_markdown_codebook


class GenerateCodebooksTests(unittest.TestCase):
    def test_clean_comment_rejects_placeholders(self):
        self.assertIsNone(clean_comment("N/A", 3))
        self.assertEqual(clean_comment("Useful feedback", 3), "Useful feedback")

    def test_parse_json_response_accepts_code_fences(self):
        self.assertEqual(parse_json_response('```json\n{"themes": []}\n```'), {"themes": []})

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
