import unittest
from unittest.mock import Mock

from survey_coder.gui import SurveyCoderApp


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
    def test_review_is_required_before_apply(self):
        app = SurveyCoderApp.__new__(SurveyCoderApp)
        app.busy = False
        app.action_button = FakeButton()
        app.review_button = FakeButton()
        app.apply_button = FakeButton()

        app.stage = "generate"
        app._update_workflow_actions()
        self.assertEqual((app.action_button.enabled, app.review_button.enabled,
                          app.apply_button.enabled), (True, True, False))

        app.stage = "review"
        app._update_workflow_actions()
        self.assertEqual((app.review_button.enabled, app.apply_button.enabled), (True, False))

        app.stage = "apply"
        app._update_workflow_actions()
        self.assertEqual((app.review_button.enabled, app.apply_button.enabled), (True, True))

        app.page_index = 2
        app._show_page = lambda _index: None
        app.back()
        self.assertEqual(app.stage, "generate")
        self.assertFalse(app.apply_button.enabled)

    def test_review_opens_output_folder(self):
        app = SurveyCoderApp.__new__(SurveyCoderApp)
        app.busy = False
        app.stage = "review"
        app.root = None
        app.action_button = FakeButton()
        app.review_button = FakeButton()
        app.apply_button = FakeButton()
        app.run_status = FakeButton()
        app.status_var = FakeVariable()
        app.open_output_dir = Mock()

        app.review_codebooks()

        app.open_output_dir.assert_called_once_with()
        self.assertEqual(app.stage, "apply")
        self.assertTrue(app.apply_button.enabled)


if __name__ == "__main__":
    unittest.main()
