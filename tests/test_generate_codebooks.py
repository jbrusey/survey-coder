import unittest

from survey_coder.generate_codebooks import clean_comment, parse_json_response


class GenerateCodebooksTests(unittest.TestCase):
    def test_clean_comment_rejects_placeholders(self):
        self.assertIsNone(clean_comment("N/A", 3))
        self.assertEqual(clean_comment("Useful feedback", 3), "Useful feedback")

    def test_parse_json_response_accepts_code_fences(self):
        self.assertEqual(parse_json_response('```json\n{"themes": []}\n```'), {"themes": []})


if __name__ == "__main__":
    unittest.main()
