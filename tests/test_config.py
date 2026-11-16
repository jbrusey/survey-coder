import json
import tempfile
import unittest
from pathlib import Path

from survey_coder.config import load_config


class ConfigTests(unittest.TestCase):
    def test_temperature_must_be_finite_nonnegative_number(self):
        base = {"streams": [{"name": "Q1", "column": "Comment", "question": "Q?"}]}
        for temperature in (-0.1, float("nan"), float("inf"), 10**1000, True, "0.2"):
            with self.subTest(temperature=temperature), tempfile.TemporaryDirectory() as directory:
                config = {**base, "llm": {"temperature": temperature}}
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, "llm.temperature"):
                    load_config(str(path))

    def test_temperature_may_be_omitted_or_zero(self):
        base = {"streams": [{"name": "Q1", "column": "Comment", "question": "Q?"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({**base, "llm": {"temperature": 0}}))
            self.assertEqual(load_config(str(path))["llm"]["temperature"], 0)
