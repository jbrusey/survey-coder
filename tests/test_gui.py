import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from survey_coder.gui import SurveyCoderApp, codebook_ready


class FakeButton:
    def __init__(self):
        self.enabled = None
        self.text = ""

    def configure(self, **options):
        self.text = options.get("text", self.text)

    def state(self, states):
        self.enabled = "!disabled" in states


class FakeVariable:
    def set(self, value):
        self.value = value


class WorkflowActionTests(unittest.TestCase):
    def make_app(self, streams):
        app = SurveyCoderApp.__new__(SurveyCoderApp)
        app.busy = False
        app.stage = "generate"
        app.action_button = FakeButton()
        app.review_button = FakeButton()
        app.apply_button = FakeButton()
        app.settings_from_ui = lambda: {"streams": streams, "output_dir": "unused"}
        return app

    def test_apply_requires_a_valid_codebook_for_every_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.md" for name in ("one", "two")]
            streams = [{"name": path.stem, "codebook": str(path)} for path in paths]
            app = self.make_app(streams)

            app._update_workflow_actions()
            self.assertFalse(app.apply_button.enabled)

            paths[0].write_text("## First code\n", encoding="utf-8")
            app._update_workflow_actions()
            self.assertFalse(app.apply_button.enabled)

            paths[1].write_text("## Second code\n", encoding="utf-8")
            app._update_workflow_actions()
            self.assertTrue(app.apply_button.enabled)

    def test_empty_markdown_is_not_a_ready_codebook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.md"
            path.write_text("# Notes only\n", encoding="utf-8")
            self.assertFalse(codebook_ready(path))

    def test_review_opens_output_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ready.md"
            path.write_text("## A code\n", encoding="utf-8")
            app = self.make_app([{"name": "ready", "codebook": str(path)}])
            app.root = None
            app.run_status = FakeButton()
            app.status_var = FakeVariable()
            app.open_output_dir = Mock()

            app.review_codebooks()

            app.open_output_dir.assert_called_once_with()
            self.assertTrue(app.apply_button.enabled)


if __name__ == "__main__":
    unittest.main()
