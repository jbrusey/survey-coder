import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from survey_coder.gui import (
    SurveyCoderApp,
    analysis_config,
    codebook_path,
    codebook_ready,
    default_codebook_filenames,
    validate_codebook_paths,
)


class FakeButton:
    def __init__(self):
        self.enabled = None
        self.text = ""
        self.focused = False

    def configure(self, **options):
        self.text = options.get("text", self.text)

    def state(self, states):
        self.enabled = "!disabled" in states

    def focus_set(self):
        self.focused = True


class FakeVariable:
    def set(self, value):
        self.value = value


class FakeProgress:
    def __init__(self):
        self.stopped = False
        self.visible = True

    def stop(self):
        self.stopped = True

    def grid_remove(self):
        self.visible = False


class AnalysisSetupTests(unittest.TestCase):
    def test_setup_contains_visible_run_settings_but_not_data_or_credentials(self):
        settings = {
            "input": "/private/responses.xlsx", "sheet": "Sheet1",
            "context": "module feedback", "streams": [{"column": "Comment"}],
            "provider": "vllm", "model": "local-model", "base_url": "http://localhost/v1",
            "max_output_tokens": 1024, "output_dir": "results", "prompt_dir": "prompts",
            "batch_size": 8, "sample_size": 200, "min_length": 5, "temperature": 0.2,
            "api_key": "secret",
        }

        setup = analysis_config(settings)

        self.assertEqual(setup["analysis"]["batch_size"], 8)
        self.assertEqual(setup["analysis"]["output_dir"], "results")
        self.assertNotIn("input", setup)
        self.assertNotIn("api_key", str(setup))
        self.assertNotIn("secret", str(setup))


class CodebookMappingTests(unittest.TestCase):
    def test_default_filenames_are_safe_and_unique(self):
        names = default_codebook_filenames(["Question / answer", "question \\ answer", "CON"])

        self.assertEqual(len({name.casefold() for name in names}), 3)
        self.assertTrue(all(name.endswith("_codebook.md") for name in names))
        self.assertFalse(any("/" in name or "\\" in name for name in names))

    def test_bare_codebook_filename_uses_output_directory(self):
        self.assertEqual(
            codebook_path({"name": "Comments", "codebook": "comments.md"}, "results"),
            Path("results/comments.md"),
        )

    def test_duplicate_codebook_paths_are_rejected_case_insensitively(self):
        streams = [
            {"column": "One", "name": "One", "codebook": "shared.md"},
            {"column": "Two", "name": "Two", "codebook": "SHARED.md"},
        ]

        with self.assertRaisesRegex(ValueError, "unique codebook path"):
            validate_codebook_paths(streams, "results")


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

    def test_partial_generation_hides_progress_without_claiming_success(self):
        app = self.make_app([])
        app.busy = True
        app.page_index = 2
        app.progress = FakeProgress()
        app.progress_var = FakeVariable()
        app.back_button = FakeButton()
        app.next_button = FakeButton()
        app.run_status = FakeButton()
        app.status_var = FakeVariable()
        app._append_log = Mock()
        app.refresh_summary = Mock()

        app._finished(
            "generate",
            "Codebook generation: 1/2 succeeded.\nGenerated: one\nFailed: two",
            None,
        )

        self.assertFalse(app.progress.visible)
        self.assertEqual(app.progress_var.value, 0)
        self.assertEqual(app.run_status.text, "Partial result — some codebooks failed.")
        self.assertIn("Partial result", app.status_var.value)
        self.assertEqual(app.stage, "review")


if __name__ == "__main__":
    unittest.main()
