"""End-to-end spreadsheet coding and durable worker checks with a fake provider."""
from io import BytesIO
from html import unescape
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    import python_multipart  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest("Install the web extra: uv sync --extra web") from exc

from openpyxl import Workbook
import pandas as pd

from survey_coder.web import create_app
from survey_coder.web_coding import make_spec, read_codebook, read_sheet, resolve_model, server_config
from survey_coder.web_worker import execute_job, validate_batch, work_once

CODEBOOK = b"# Feedback\n\n## 1. Explanation\nClear teaching.\n\n## 2. Practice\nPractical exercises.\n"


def xlsx(rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Feedback"
    for row in rows or [
        ["ID", "Comment", "Metadata"],
        ["001", "Clear teaching", "DO_NOT_SEND"],
        ["002", "More practice", "DO_NOT_SEND"],
        ["003", "Both", "DO_NOT_SEND"],
        ["004", "No theme", "DO_NOT_SEND"],
        ["005", "Clear teaching", "DO_NOT_SEND"],
        ["006", None, "DO_NOT_SEND"],
    ]:
        sheet.append(row)
    book.create_sheet("Second").append(["Another column"])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class CodingWebTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": "fake-test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.database = Path(directory.name) / "web.sqlite3"
        self.app = create_app(self.database, origin="http://testserver", secure_cookie=False)
        self.store = self.app.state.store
        self.alice = self.store.add_user("alice", "alice-password-123")
        self.bob = self.store.add_user("bob", "bob-password-456")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.get("/login")
        response = self.client.post("/login", data={"username": "alice", "password": "alice-password-123",
                                                    "csrf": self.client.cookies.get("survey_login_csrf")})
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)

    def upload(self, source=None, codebook=CODEBOOK, client=None, csrf=None):
        return (client or self.client).post("/runs/upload", data={"name": "Test feedback", "csrf": csrf or self.csrf},
            files={"workbook": ("feedback.xlsx", source if source is not None else xlsx(), "application/octet-stream"),
                   "codebook": ("codes.md", codebook, "text/markdown")}, follow_redirects=False)

    def draft(self, source=None):
        response = self.upload(source)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers["location"].split("/")[2]

    def start(self, run_id, **overrides):
        data = {"csrf": self.csrf, "sheet": "Feedback", "column": "Comment", "id_column": "ID",
                "question": "What did you think of the teaching?", "confirmed": "yes", **overrides}
        return self.client.post(f"/runs/{run_id}/start", data=data, follow_redirects=False)

    @staticmethod
    def fake_provider(prompt, system, *_):
        assert "DO_NOT_SEND" not in prompt + system
        labels = {"R000001": ["Explanation"], "R000002": ["Practice"],
                  "R000003": ["Explanation", "Practice"], "R000004": [], "R000005": ["Explanation"]}
        return json.dumps({"results": [{"id": identifier, "codes": labels[identifier]}
                                       for identifier in re.findall(r"ID: (R\d+)", prompt)]})

    def test_full_upload_preview_confirm_code_download_and_ownership(self):
        self.assertIn("New coding run", self.client.get("/").text)
        run_id = self.draft()
        setup = self.client.get(f"/runs/{run_id}/setup")
        self.assertIn("Explanation", setup.text)
        self.assertIn("6 source rows", setup.text)
        confirm = self.start(run_id, confirmed="")
        self.assertEqual(confirm.status_code, 200)
        self.assertIn("5</strong> eligible", confirm.text)
        self.assertEqual(self.store.run(self.alice, run_id)["status"], "draft")
        self.assertEqual(self.start(run_id).status_code, 303)
        self.assertEqual(self.start(run_id).status_code, 303)
        self.assertEqual(self.store.run(self.alice, run_id)["status"], "queued")
        self.assertEqual(self.client.get(f"/runs/{run_id}").headers["refresh"], "5")
        self.assertTrue(work_once(self.store, call=self.fake_provider))
        self.assertFalse(work_once(self.store, call=self.fake_provider))
        detail = self.client.get(f"/api/runs/{run_id}").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["counts"], {"total": 6, "eligible": 5, "completed": 5, "failed": 0, "blank": 1, "non_response": 0, "remaining": 0})
        output = pd.read_csv(BytesIO(self.client.get(f"/api/files/{detail['result_id']}").content), dtype={"ID": str})
        self.assertEqual(list(output["ID"]), ["001", "002", "003", "004", "005", "006"])
        self.assertEqual(list(output["Metadata"]), ["DO_NOT_SEND"] * 6)
        self.assertEqual(list(output["AI_Explanation"][:5]), [1, 0, 1, 0, 1])
        self.assertEqual(list(output["AI_Practice"][:5]), [0, 1, 1, 0, 0])
        self.assertTrue(pd.isna(output.iloc[5]["AI_Explanation"]))
        self.assertEqual(list(output["AI_Status"]), ["coded"] * 5 + ["blank"])
        manifest = self.client.get(f"/api/files/{detail['manifest_id']}").json()
        self.assertEqual(manifest["configuration"]["model"], "gpt-4o-mini")
        self.assertEqual(manifest["counts"], {"coded": 5, "blank": 1, "non_response": 0, "failed": 0})
        self.assertNotIn("fake-test-key", json.dumps(manifest))
        self.assertNotIn("refresh", self.client.get(f"/runs/{run_id}").headers)
        with TestClient(self.app) as bob:
            bob.get("/login")
            home = bob.post("/login", data={"username": "bob", "password": "bob-password-456",
                                            "csrf": bob.cookies.get("survey_login_csrf")})
            bob_csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
            for url in (f"/runs/{run_id}", f"/runs/{run_id}/setup", f"/api/files/{detail['result_id']}", f"/api/files/{detail['manifest_id']}"):
                self.assertEqual(bob.get(url).status_code, 404)
            self.assertEqual(bob.post(f"/runs/{run_id}/start", data={"csrf": bob_csrf}).status_code, 404)

    def test_validation_errors_do_not_queue_work(self):
        self.assertEqual(self.upload(source=b"not a workbook").status_code, 422)
        self.assertEqual(self.upload(codebook=b"# No codes").status_code, 422)
        self.assertEqual(self.upload(codebook=b"## Duplicate\n## duplicate").status_code, 422)
        run_id = self.draft()
        for values in ({"column": "Missing"}, {"question": " "}, {"sheet": "Missing"}):
            self.assertEqual(self.start(run_id, **values).status_code, 422)
            self.assertEqual(self.store.run(self.alice, run_id)["status"], "draft")
        duplicate = self.draft(xlsx([["ID", "Comment"], ["same", "A"], ["same", "B"]]))
        self.assertEqual(self.start(duplicate).status_code, 422)
        self.assertEqual(self.start(duplicate, id_column="").status_code, 303)
        collision = self.draft(xlsx([["ID", "Comment", "AI_Status"], ["1", "A", "existing"]]))
        self.assertEqual(self.start(collision).status_code, 422)
        self.assertEqual(self.upload(csrf="wrong").status_code, 403)
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get("/runs/new").status_code, 401)
            self.assertEqual(self.upload(client=anonymous).status_code, 401)

    def test_partial_and_failed_model_results_remain_missing(self):
        run_id = self.draft()
        self.start(run_id)
        def partial(*_):
            return json.dumps({"results": [{"id": "R000001", "codes": ["invented"]},
                {"id": "R000002", "codes": []}, {"id": "R000003", "codes": ["Practice"]}]})
        work_once(self.store, call=partial)
        job = self.store.job(self.alice, run_id)
        self.assertEqual(job["status"], "completed_with_errors")
        self.assertEqual(job["counts"]["failed"], 3)
        output = pd.read_csv(BytesIO(self.store.file(self.alice, job["result_id"])["content"]))
        self.assertTrue(pd.isna(output.iloc[0]["AI_Explanation"]))
        self.assertEqual(output.iloc[1]["AI_Explanation"], 0)
        self.assertEqual(output.iloc[0]["AI_Status"], "failed")
        self.assertIn("unknown", output.iloc[0]["AI_Error"])
        second = self.draft()
        self.start(second)
        def failed(*_):
            raise RuntimeError("secret=DO_NOT_EXPOSE")
        work_once(self.store, call=failed)
        job = self.store.job(self.alice, second)
        self.assertEqual(job["status"], "failed")
        output = self.store.file(self.alice, job["result_id"])["content"]
        self.assertNotIn(b"DO_NOT_EXPOSE", output)
        self.assertIn(b"Provider request failed", output)

    def test_failure_breakdown_and_retry_preserve_successes(self):
        run_id = self.draft()
        self.start(run_id)
        def partial(*_):
            return json.dumps({"results": [{"id": "R000001", "codes": ["secret-unknown-name"]},
                {"id": "R000002", "codes": []}, {"id": "R000003", "codes": ["Practice"]}]})
        work_once(self.store, call=partial)
        previous = self.store.job(self.alice, run_id)
        report = self.client.get(f"/api/runs/{run_id}").json()["failures"]
        self.assertEqual([item["row_id"] for item in report["rows"]], ["R000001", "R000004", "R000005"])
        self.assertEqual([item["count"] for item in report["summary"]], [1, 2])
        page = self.client.get(f"/runs/{run_id}").text
        for text in ("Why responses failed", "Would retrying help?", "R000004", "Retry failed responses"):
            self.assertIn(text, page)
        self.assertNotIn("secret-unknown-name", page + json.dumps(report))
        response = self.client.post(f"/runs/{run_id}/retry", data={"csrf": self.csrf}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        queued = self.store.job(self.alice, run_id)
        self.assertEqual(queued["counts"]["completed"], 2)
        self.assertEqual(queued["counts"]["failed"], 0)
        self.assertEqual(queued["counts"]["remaining"], 3)
        self.assertIsNone(queued["result_id"])
        self.assertEqual(self.client.post(f"/runs/{run_id}/retry", data={"csrf": self.csrf}).status_code, 409)
        prompts = []
        def provider(prompt, system, *args):
            prompts.append(prompt)
            self.assertEqual(len(re.findall(r"ID: R\d+", prompt)), 1)
            self.assertIn("Allowed code names", system)
            return self.fake_provider(prompt, system, *args)
        work_once(self.store, call=provider)
        self.assertEqual(len(prompts), 3)
        self.assertNotIn("ID: R000002", "".join(prompts))
        final = self.store.job(self.alice, run_id)
        self.assertEqual(final["status"], "completed")
        output = pd.read_csv(BytesIO(self.store.file(self.alice, final["result_id"])["content"]))
        self.assertEqual(output.iloc[1]["AI_Explanation"], 0)
        self.assertEqual(output.iloc[2]["AI_Explanation"], 0)  # Preserve original Practice-only coding.
        self.assertEqual(output["AI_Status"].tolist(), ["coded"] * 5 + ["blank"])
        manifest = json.loads(self.store.file(self.alice, final["manifest_id"])["content"])
        self.assertEqual(manifest["retry_count"], 1)
        self.assertEqual(manifest["request_batch_size"], 1)
        self.assertEqual(manifest["failures"]["rows"], [])
        self.assertIsNotNone(self.store.file(self.alice, previous["result_id"]))
        self.assertEqual(self.client.post(f"/runs/{run_id}/retry", data={"csrf": self.csrf}).status_code, 409)

    def test_retry_requires_owner_csrf_and_same_origin(self):
        run_id = self.draft()
        self.start(run_id)
        work_once(self.store, call=lambda *_: "bad JSON with secret text")
        for kwargs in ({"data": {"csrf": "wrong"}},
                       {"data": {"csrf": self.csrf}, "headers": {"Origin": "https://elsewhere.test"}}):
            self.assertEqual(self.client.post(f"/runs/{run_id}/retry", **kwargs).status_code, 403)
        with TestClient(self.app) as bob:
            bob.get("/login")
            home = bob.post("/login", data={"username": "bob", "password": "bob-password-456",
                                           "csrf": bob.cookies.get("survey_login_csrf")})
            csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
            self.assertEqual(bob.post(f"/runs/{run_id}/retry", data={"csrf": csrf}).status_code, 404)
            self.assertEqual(bob.get(f"/api/runs/{run_id}").status_code, 404)
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.post(f"/runs/{run_id}/retry", data={"csrf": self.csrf}).status_code, 401)
        self.assertEqual(self.store.job(self.alice, run_id)["status"], "failed")

    def test_job_level_failure_can_retry_pending_rows_and_fences_old_worker(self):
        run_id = self.draft()
        self.start(run_id)
        self.store.claim("old")
        self.store.checkpoint(run_id, "old", [(0, "coded", [], None)])
        self.store.fail(run_id, "old", "Worker stopped.")
        self.assertTrue(self.store.retry_failed(self.alice, run_id))
        self.assertFalse(self.store.renew(run_id, "old"))
        self.store.fail(run_id, "old", "Stale worker")
        self.assertEqual(self.store.job(self.alice, run_id)["status"], "queued")
        work_once(self.store, call=self.fake_provider)
        self.assertEqual(self.store.job(self.alice, run_id)["status"], "completed")

    def test_legacy_failures_have_guidance_without_changing_existing_rows(self):
        run_id = self.draft()
        self.start(run_id)
        self.store.claim("old")
        self.store.checkpoint(run_id, "old", [(0, "failed", None, "Model returned invalid or unknown code names.")])
        response = self.client.get(f"/runs/{run_id}")
        self.assertIn("saved result does not distinguish", response.text)
        self.assertIn("R000001", response.text)
        self.assertNotIn("Retry failed responses", response.text)  # Still running.

    def test_vllm_retry_constrains_codes_even_for_a_legacy_run(self):
        run_id = self.draft()
        self.start(run_id)
        self.store.claim("old")
        outcomes = [(index, "failed", None, "Model returned unknown code names.") if index == 0
                    else (index, "coded", [], None) for index in range(5)]
        self.store.checkpoint(run_id, "old", outcomes)
        self.store.finish(run_id, "old", b"previous CSV", b"{}")
        with self.store.connect() as db:
            spec = self.store.job(self.alice, run_id)["spec"]
            spec["config"]["provider"] = "vllm"
            spec["config"].pop("structured_outputs", None)  # Saved before this change.
            db.execute("UPDATE coding_jobs SET spec = ? WHERE run_id = ?", (json.dumps(spec), run_id))
        self.assertTrue(self.store.retry_failed(self.alice, run_id))
        with patch("survey_coder.apply_codebooks.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[
                SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                    content='{"results": [{"id": "R000001", "codes": ["Explanation"]}]}'))])
            work_once(self.store)
        request = sdk.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(sdk.return_value.chat.completions.create.call_count, 1)
        schema = request["response_format"]["json_schema"]["schema"]
        fields = schema["properties"]["results"]["items"]["properties"]
        self.assertEqual(fields["id"]["enum"], ["R000001"])
        self.assertEqual(fields["codes"]["items"]["enum"], ["Explanation", "Practice"])
        final = self.store.job(self.alice, run_id)
        self.assertEqual(final["status"], "completed")
        manifest = json.loads(self.store.file(self.alice, final["manifest_id"])["content"])
        self.assertTrue(manifest["structured_outputs"])

    def test_rejected_schema_is_visible_and_does_not_silently_fall_back(self):
        import httpx
        from openai import BadRequestError
        run_id = self.draft()
        self.start(run_id)
        with self.store.connect() as db:
            spec = self.store.job(self.alice, run_id)["spec"]
            spec["config"]["structured_outputs"] = True
            db.execute("UPDATE coding_jobs SET spec = ? WHERE run_id = ?", (json.dumps(spec), run_id))
        with patch("survey_coder.apply_codebooks.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.side_effect = BadRequestError(
                "private provider text", response=httpx.Response(400, request=httpx.Request("POST", "https://provider.test")), body=None)
            work_once(self.store)
        self.assertEqual(sdk.return_value.chat.completions.create.call_count, 1)
        report = self.client.get(f"/api/runs/{run_id}").json()["failures"]
        self.assertIn("schema-constrained request", report["summary"][0]["reason"])
        self.assertIn("structured-output support", report["summary"][0]["guidance"])
        self.assertNotIn("private provider text", json.dumps(report))

    def test_checkpoint_and_lease_survive_new_store_and_skip_committed_rows(self):
        from survey_coder.web_store import Store
        run_id = self.draft()
        self.start(run_id)
        job = self.store.claim("old-worker")
        self.assertIsNone(self.store.claim("another-worker"))
        self.assertTrue(self.store.checkpoint(run_id, "old-worker", [(0, "coded", ["Explanation"], None)]))
        with self.store.connect() as db:
            db.execute("UPDATE coding_jobs SET lease_until = ? WHERE run_id = ?", (time.time() - 1, run_id))
        new_store = Store(self.database)
        resumed = new_store.claim("new-worker")
        self.assertEqual(resumed["run_id"], run_id)
        self.assertFalse(self.store.checkpoint(run_id, "old-worker", [(1, "coded", [], None)]))
        prompts = []
        def provider(prompt, *args):
            prompts.append(prompt)
            return self.fake_provider(prompt, *args)
        execute_job(new_store, resumed, "new-worker", call=provider)
        self.assertNotIn("ID: R000001", "".join(prompts))
        self.assertEqual(new_store.job(self.alice, run_id)["status"], "completed")

    def test_model_configuration_is_checked_without_exposing_keys(self):
        run_id = self.draft()
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            response = self.client.get(f"/runs/{run_id}/setup")
            self.assertIn("disabled", response.text)
            self.assertEqual(self.start(run_id).status_code, 503)
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_blank_only_sheet_produces_accounted_for_output_without_model_calls(self):
        run_id = self.draft(xlsx([["ID", "Comment", "Metadata"], ["1", None, "A"], ["2", "   ", "B"]]))
        self.start(run_id)
        with patch("survey_coder.web_worker.engine.call_llm") as provider:
            self.assertTrue(work_once(self.store))
        provider.assert_not_called()
        job = self.store.job(self.alice, run_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["counts"]["blank"], 2)
        output = pd.read_csv(BytesIO(self.store.file(self.alice, job["result_id"])["content"]))
        self.assertTrue(output["AI_Explanation"].isna().all())
        self.assertEqual(output["AI_Status"].tolist(), ["blank", "blank"])

    def test_configured_non_responses_survive_confirmation_but_never_reach_provider(self):
        run_id = self.draft(xlsx([
            ["ID", "Comment"], ["1", "D/A"], ["2", " d/a "], ["3", None], ["4", "   "],
            ["5", "Clear teaching"], ["6", "D/A appears in the instructions"],
        ]))
        setup = self.client.get(f"/runs/{run_id}/setup")
        self.assertIn('name="non_response_value"', setup.text)
        self.assertIn("whole value", setup.text)
        confirm = self.start(run_id, confirmed="", non_response_value=" D/A ")
        self.assertEqual(confirm.status_code, 200)
        self.assertIn("2</strong> eligible responses", confirm.text)
        self.assertIn("2</strong> blank rows", confirm.text)
        self.assertIn("2</strong> configured non-response rows", confirm.text)
        fields = {name: unescape(value) for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">', confirm.text)}
        self.assertEqual(fields["non_response_value"], " D/A ")
        self.assertEqual(len(fields), 8)
        submitted = self.client.post(f"/runs/{run_id}/start", data=fields, follow_redirects=False)
        self.assertEqual(submitted.status_code, 303)
        spec = self.store.job(self.alice, run_id)["spec"]
        self.assertEqual(spec["non_response_value"], "D/A")
        self.assertEqual(spec["eligible"], [False, False, False, False, True, True])
        calls = []
        def provider(prompt, *_):
            identifiers = re.findall(r"ID: (R\d+)", prompt)
            self.assertEqual(identifiers, ["R000005", "R000006"])
            self.assertIn("D/A appears in the instructions", prompt)
            calls.append(prompt)
            return json.dumps({"results": [{"id": identifier, "codes": ["Explanation"]} for identifier in identifiers]})
        self.assertTrue(work_once(self.store, call=provider))
        self.assertEqual(len(calls), 1)
        job = self.store.job(self.alice, run_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["counts"], {"total": 6, "eligible": 2, "completed": 2, "failed": 0,
                                         "blank": 2, "non_response": 2, "remaining": 0})
        output = pd.read_csv(BytesIO(self.store.file(self.alice, job["result_id"])["content"]), keep_default_na=False)
        self.assertEqual(output["AI_Status"].tolist(), ["non_response", "non_response", "blank", "blank", "coded", "coded"])
        self.assertEqual(output["Comment"].tolist()[:2], ["D/A", " d/a "])
        self.assertEqual(output["AI_Explanation"].tolist(), ["", "", "", "", "1", "1"])
        self.assertEqual(output["AI_Error"].tolist(), [""] * 6)
        manifest = json.loads(self.store.file(self.alice, job["manifest_id"])["content"])
        self.assertEqual(manifest["non_response_value"], "D/A")
        self.assertIn("ignoring case", manifest["non_response_matching"])
        self.assertEqual(manifest["counts"], {"coded": 2, "blank": 2, "non_response": 2, "failed": 0})
        self.assertIn("Non Response", self.client.get(f"/runs/{run_id}").text)

    def test_all_blank_or_marker_responses_make_no_provider_calls(self):
        run_id = self.draft(xlsx([["ID", "Comment"], ["1", "D/A"], ["2", " d/a "], ["3", None], ["4", "  "]]))
        self.assertEqual(self.start(run_id, non_response_value="D/A").status_code, 303)
        with patch("survey_coder.web_worker.engine.call_llm") as provider:
            self.assertTrue(work_once(self.store))
        provider.assert_not_called()
        job = self.store.job(self.alice, run_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["counts"], {"total": 4, "eligible": 0, "completed": 0, "failed": 0,
                                         "blank": 2, "non_response": 2, "remaining": 0})
        output = pd.read_csv(BytesIO(self.store.file(self.alice, job["result_id"])["content"]))
        self.assertTrue(output["AI_Explanation"].isna().all())
        self.assertEqual(output["AI_Status"].tolist(), ["non_response", "non_response", "blank", "blank"])

    def test_marker_is_opt_in_and_legacy_specs_still_work(self):
        run_id = self.draft(xlsx([["ID", "Comment"], ["1", "D/A"], ["2", None]]))
        job = self.store.job(self.alice, run_id)
        markdown, codes = read_codebook(CODEBOOK)
        spec = make_spec(read_sheet(xlsx([["ID", "Comment"], ["1", "D/A"], ["2", None]]), "Feedback"),
                         sheet="Feedback", column="Comment", id_column="ID", question="Q?",
                         codebook=markdown, codes=codes, config=server_config())
        self.assertEqual(spec["eligible"], [True, False])
        self.assertIsNone(spec["non_response_value"])
        for field in ("row_states", "non_response_value", "non_response_matching"):
            spec.pop(field)
        spec.update(source_sha256="legacy-source", codebook_sha256="legacy-codebook")
        self.assertTrue(self.store.submit(self.alice, run_id, spec))
        calls = []
        def provider(prompt, *_):
            calls.append(prompt)
            return json.dumps({"results": [{"id": "R000001", "codes": []}]})
        self.assertTrue(work_once(self.store, call=provider))
        self.assertIn("Comment: D/A", calls[0])
        job = self.store.job(self.alice, run_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["counts"]["blank"], 1)
        self.assertEqual(job["counts"]["non_response"], 0)

    def test_auto_model_is_shown_before_submission_and_pinned_for_worker(self):
        config = {**server_config(), "provider": "vllm", "base_url": "http://model.test/v1", "model": "auto"}
        with patch("survey_coder.web_coding.server_config", return_value=config):
            app = create_app(self.database, origin="http://testserver", secure_cookie=False)
        with TestClient(app) as client, patch("openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.models.list.return_value.data = [SimpleNamespace(id="server-selected-model")]
            client.get("/login")
            home = client.post("/login", data={"username": "alice", "password": "alice-password-123",
                                               "csrf": client.cookies.get("survey_login_csrf")})
            csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
            upload = self.upload(client=client, csrf=csrf)
            run_id = upload.headers["location"].split("/")[2]
            data = {"csrf": csrf, "sheet": "Feedback", "column": "Comment", "id_column": "ID", "question": "Q?"}
            confirm = client.post(f"/runs/{run_id}/start", data=data)
            self.assertIn("server-selected-model", confirm.text)
            self.assertEqual(self.store.run(self.alice, run_id)["status"], "draft")
            final = client.post(f"/runs/{run_id}/start", data={**data, "confirmed": "yes", "resolved_model": "server-selected-model"}, follow_redirects=False)
            self.assertEqual(final.status_code, 303)
            self.assertEqual(self.store.job(self.alice, run_id)["spec"]["config"]["model"], "server-selected-model")

            # A new server model requires a new confirmation before it is queued.
            upload = self.upload(client=client, csrf=csrf)
            second = upload.headers["location"].split("/")[2]
            sdk.return_value.__enter__.return_value.models.list.return_value.data = [SimpleNamespace(id="changed-model")]
            changed = client.post(f"/runs/{second}/start", data={**data, "confirmed": "yes", "resolved_model": "server-selected-model"})
            self.assertIn("changed-model", changed.text)
            self.assertEqual(self.store.run(self.alice, second)["status"], "draft")


class InputValidationTests(unittest.TestCase):
    def test_auto_discovery_requires_one_model_and_keeps_failures_private(self):
        config = {**server_config(), "provider": "vllm", "base_url": "http://model.test/v1", "model": "auto"}
        with patch("openai.OpenAI") as sdk:
            client = sdk.return_value.__enter__.return_value
            for models in ([], [SimpleNamespace(id="A"), SimpleNamespace(id="B")]):
                client.models.list.return_value.data = models
                with self.assertRaises(ValueError):
                    resolve_model(config)
            client.models.list.side_effect = RuntimeError("secret-key-value")
            with self.assertRaisesRegex(ValueError, "Could not discover") as error:
                resolve_model(config)
            self.assertNotIn("secret-key-value", str(error.exception))
        explicit = {**config, "model": "chosen"}
        self.assertIs(resolve_model(explicit), explicit)

    def test_repeated_cell_text_cannot_exceed_decoded_import_limit(self):
        with patch("survey_coder.web_coding.MAX_SHEET_CHARACTERS", 30):
            with self.assertRaisesRegex(ValueError, "decoded cell text"):
                read_sheet(xlsx([["ID", "Comment"], ["1", "abcdefghij"], ["2", "abcdefghij"]]), "Feedback")

    def test_invalid_headings_and_output_names(self):
        for content in (b"## Codes_Applied", b"## Name\n## NAME"):
            with self.subTest(content=content):
                try:
                    markdown, codes = read_codebook(content)
                    make_spec(read_sheet(xlsx(), "Feedback"), sheet="Feedback", column="Comment", id_column="ID",
                              question="Q?", codebook=markdown, codes=codes, config=server_config())
                except ValueError:
                    pass
                else:
                    self.fail("Invalid codebook accepted")
        for rows in ([["Same", "same"], [1, 2]], [[None, "Comment"], [1, 2]]):
            with self.assertRaises(ValueError):
                read_sheet(xlsx(rows), "Feedback")

    def test_strict_batch_shapes_and_empty_set(self):
        invalid = [None, {}, {"results": None}, {"results": [{"id": "unexpected", "codes": []}]},
                   {"results": [{"id": "R000001", "codes": "Code"}]},
                   {"results": [{"id": "R000001", "codes": ["Unknown"]}]},
                   {"results": [{"id": "R000001", "codes": []}, {"id": "R000001", "codes": []}]}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(validate_batch(payload, [0], ["Code"])[0][1], "failed")
        self.assertEqual(validate_batch({"results": [{"id": "R000001", "codes": []}]}, [0], ["Code"]),
                         [(0, "coded", [], None)])


class FailureClassificationTests(unittest.TestCase):
    def test_safe_provider_classification(self):
        import httpx
        from openai import APIStatusError, APITimeoutError, APIConnectionError
        from google.genai.errors import APIError
        from survey_coder.web_failures import OutputTruncatedError, provider_failure
        request = httpx.Request("POST", "https://provider.test/v1")
        cases = [(OutputTruncatedError("secret"), "truncated"),
                 (APITimeoutError(request=request), "timed out"),
                 (APIConnectionError(request=request), "connect"),
                 (RuntimeError("secret"), "Provider request failed")]
        for status, expected in [(401, "credentials"), (403, "credentials"), (429, "rate limit"),
                                 (500, "temporarily"), (503, "temporarily"), (504, "timed out"), (400, "settings"), (404, "settings")]:
            cases.append((APIStatusError("secret", response=httpx.Response(status, request=request), body={"secret": "text"}), expected))
            cases.append((APIError(status, {"error": {"message": "secret"}}), expected))
        for exception, expected in cases:
            with self.subTest(exception=type(exception).__name__, expected=expected):
                message = provider_failure(exception)
                self.assertIn(expected, message)
                self.assertNotIn("secret", message)

    def test_invalid_json_is_distinct_from_missing_results(self):
        from survey_coder.apply_codebooks import parse_json_response
        result = validate_batch(parse_json_response("secret invalid JSON"), [0], ["Code"])
        self.assertEqual(result[0][3], "Model returned invalid JSON.")

    def test_distinct_invalid_code_reasons_never_echo_model_output(self):
        for labels, reason in [("secret", "invalid codes list"), ([{}], "invalid codes list"),
                               (["secret"], "unknown code names"), (["Code", "Code"], "duplicate code names")]:
            result = validate_batch({"results": [{"id": "R000001", "codes": labels}]}, [0], ["Code"])
            self.assertIn(reason, result[0][3])
            self.assertNotIn("secret", result[0][3])
