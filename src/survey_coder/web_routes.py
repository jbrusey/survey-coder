"""Spreadsheet upload, setup and results pages for the single Survey user role."""
from __future__ import annotations

import hashlib
from html import escape as e
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool

from .web_coding import (MAX_CODEBOOK_BYTES, MAX_WORKBOOK_BYTES, configuration_problem,
                         make_spec, read_codebook, read_sheet, resolve_model, sheets)
from .web_failures import failure_report


def register_coding_routes(app, store, current_user, check_csrf, check_origin, form, page, config):
    def owned_job(user, run_id):
        job = store.job(user["id"], run_id)
        if job is None:
            raise HTTPException(404, "Run not found.")
        return job

    def hidden(user):
        return f'<input type="hidden" name="csrf" value="{user["csrf"]}">'

    def new_page(user, error=""):
        warning = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
        response = page("New coding run", f'''<section class="card"><p><a href="/">← My runs</a></p>
<h1>New coding run</h1><p class="muted">1. Upload files → 2. Select responses → 3. Code and download</p>{warning}
<form action="/runs/upload" method="post" enctype="multipart/form-data">{hidden(user)}
<label for="name">Run name</label><input id="name" name="name" maxlength="150" placeholder="e.g. Module feedback, autumn" required>
<label for="workbook">Spreadsheet (.xlsx)</label><input id="workbook" name="workbook" type="file" accept=".xlsx" required>
<p class="muted">Up to 10 MiB; selected sheet up to 10,000 rows, 200 columns and 20 MiB of decoded cell text. Use column names in the first row. Imports use cached cell values.</p>
<label for="codebook">Existing codebook (.md)</label><input id="codebook" name="codebook" type="file" accept=".md" required>
<p class="muted">UTF-8 Markdown, up to 1 MiB. Give each code a level-two heading, such as <code>## 1. Clear explanation</code>.</p>
<p><button type="submit">Upload and continue</button></p></form></section>''')
        if error:
            response.status_code = 422
        return response

    @app.get("/runs/new")
    def new(user=Depends(current_user)):
        return new_page(user)

    @app.post("/runs/upload")
    async def upload(request: Request, user=Depends(current_user)):
        check_origin(request)
        if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
            raise HTTPException(415, "Upload the spreadsheet and codebook using the form.")
        # Bound the entire request even when no Content-Length header is supplied.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_WORKBOOK_BYTES + MAX_CODEBOOK_BYTES + 65536:
                raise HTTPException(413, "The upload is too large. Workbook limit: 10 MiB; codebook limit: 1 MiB.")

        async def receive():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        parsed_request = Request(request.scope, receive)
        async with parsed_request.form(max_files=2, max_fields=2, max_part_size=4096) as values:
            if any(len(values.getlist(key)) != 1 for key in values):
                raise HTTPException(400, "Duplicate form fields.")
            csrf = values.get("csrf", "")
            if not isinstance(csrf, str):
                raise HTTPException(400, "Invalid form.")
            check_csrf(user["csrf"], csrf)
            source, codebook = values.get("workbook"), values.get("codebook")
            name = values.get("name", "")
            if not isinstance(name, str) or not name.strip() or len(name) > 150:
                return new_page(user, "Enter a run name up to 150 characters.")
            if not isinstance(source, UploadFile) or not isinstance(codebook, UploadFile):
                return new_page(user, "Choose both a spreadsheet and codebook.")
            if not (source.filename or "").lower().endswith(".xlsx") or not (codebook.filename or "").lower().endswith(".md"):
                return new_page(user, "Choose an XLSX spreadsheet and a Markdown (.md) codebook.")
            workbook_bytes = await source.read(MAX_WORKBOOK_BYTES + 1)
            codebook_bytes = await codebook.read(MAX_CODEBOOK_BYTES + 1)
            try:
                await run_in_threadpool(sheets, workbook_bytes)
                await run_in_threadpool(read_codebook, codebook_bytes)
            except ValueError as exc:
                response = new_page(user, str(exc))
                response.status_code = 422
                return response
            run_id = store.draft(user["id"], name.strip(), workbook_bytes, codebook_bytes)
        return RedirectResponse(f"/runs/{run_id}/setup", status_code=303)

    def setup_page(user, job, sheet=None, error="", non_response_value=""):
        source = store.file(user["id"], job["workbook_id"])["content"]
        markdown, codes = read_codebook(store.file(user["id"], job["codebook_id"])["content"])
        sheet_names = sheets(source)
        selected = sheet if sheet in sheet_names else sheet_names[0]
        sheet_options = "".join(f'<option value="{e(name)}" {"selected" if name == selected else ""}>{e(name)}</option>' for name in sheet_names)
        try:
            frame = read_sheet(source, selected)
        except ValueError as exc:
            return page("Select worksheet", f'''<section class="card"><p><a href="/">← My runs</a></p><h1>Select worksheet</h1>
<p class="error" role="alert">{e(str(exc))}</p><form method="get"><label for="sheet">Worksheet</label><select id="sheet" name="sheet">{sheet_options}</select>
<p><button type="submit">Preview worksheet</button></p></form></section>''')
        options = "".join(f'<option value="{e(column)}">{e(column)}</option>' for column in frame.columns)
        code_list = "".join(f"<li>{e(code)}</li>" for code in codes)
        problem = configuration_problem(config)
        warning = f'<p class="error" role="alert">{e(error or problem)}</p>' if error or problem else ""
        return page("Select responses", f'''<section class="card"><p><a href="/">← My runs</a></p>
<h1>{e(job['name'])}</h1><p class="muted">2. Select responses · {len(frame)} source rows</p>{warning}
<form method="get"><label for="preview-sheet">Worksheet</label><select id="preview-sheet" name="sheet">{sheet_options}</select>
<p><button type="submit">Preview worksheet</button></p></form>
<div class="table-scroll" tabindex="0" role="region" aria-label="Worksheet preview">{frame.head(5).fillna('').to_html(index=False, escape=True)}</div>
<form action="/runs/{job['run_id']}/start" method="post">{hidden(user)}<input type="hidden" name="sheet" value="{e(selected)}">
<label for="column">Response column</label><select id="column" name="column" required><option value="" disabled selected>Choose a response column</option>{options}</select>
<label for="id-column">Source ID column</label><select id="id-column" name="id_column"><option value="">Generate row IDs</option>{options}</select>
<p class="muted">We include stable Survey_Row_ID values in the output and keep your source columns and row order.</p>
<label for="question">Question wording</label><textarea id="question" name="question" rows="3" maxlength="2000" required></textarea>
<label for="non-response-value">Non-response value (optional)</label><input id="non-response-value" name="non_response_value" maxlength="200" placeholder="e.g. D/A" value="{e(non_response_value)}" aria-describedby="non-response-help">
<p id="non-response-help" class="muted">Responses matching this whole value are not sent for coding. Matching ignores surrounding whitespace and letter case. Leave blank to use no special value; empty and whitespace-only responses are always excluded.</p>
<h2>Codebook: {len(codes)} codes</h2><ul>{code_list}</ul><details><summary>Read complete codebook</summary><pre>{e(markdown)}</pre></details>
<p>Configured model: <strong>{e(config['provider'])} / {e(config['model'])}</strong></p>
<p>The server sends eligible response text and generated row IDs to this provider. Blank and configured non-response rows keep missing code values. A coded row with no applicable codes receives zeros.</p>
<p><button type="submit" {"disabled" if problem else ""}>Check and continue</button></p></form></section>''')

    @app.get("/runs/{run_id}/setup")
    def setup(run_id: str, sheet: str | None = None, user=Depends(current_user)):
        job = owned_job(user, run_id)
        if job["status"] != "draft":
            return RedirectResponse(f"/runs/{run_id}", status_code=303)
        return setup_page(user, job, sheet)

    @app.post("/runs/{run_id}/start")
    async def start(run_id: str, request: Request, user=Depends(current_user)):
        values = await form(request, max_fields=8)
        check_csrf(user["csrf"], values.get("csrf", ""))
        job = owned_job(user, run_id)
        if job["status"] != "draft":
            return RedirectResponse(f"/runs/{run_id}", status_code=303)
        problem = configuration_problem(config)
        if problem:
            raise HTTPException(503, problem)
        sheet = values.get("sheet", "")
        try:
            source = store.file(user["id"], job["workbook_id"])["content"]
            content = store.file(user["id"], job["codebook_id"])["content"]
            markdown, codes = read_codebook(content)
            run_config = await run_in_threadpool(resolve_model, config)
            frame = await run_in_threadpool(read_sheet, source, sheet)
            spec = await run_in_threadpool(make_spec, frame, sheet=sheet, column=values.get("column", ""),
                             id_column=values.get("id_column", ""), question=values.get("question", ""),
                             codebook=markdown, codes=codes, config=run_config,
                             non_response_value=values.get("non_response_value", ""))
            spec.update(source_sha256=hashlib.sha256(source).hexdigest(), codebook_sha256=hashlib.sha256(content).hexdigest())
        except ValueError as exc:
            response = setup_page(user, job, sheet, error=str(exc), non_response_value=values.get("non_response_value", ""))
            response.status_code = 422
            return response
        # Confirmation uses the same values; only the final POST queues the run.
        if (values.get("confirmed") != "yes"
                or (config["model"] == "auto" and values.get("resolved_model") != run_config["model"])):
            fields = "".join(f'<input type="hidden" name="{e(key)}" value="{e(value)}">' for key, value in values.items() if key not in ("confirmed", "resolved_model"))
            fields += f'<input type="hidden" name="resolved_model" value="{e(run_config["model"])}">'
            eligible = sum(spec["eligible"])
            blank = spec["row_states"].count("blank")
            non_response = spec["row_states"].count("non_response")
            marker_summary = (f"{e(spec['non_response_value'])} (trimmed, case-insensitive whole-value match)"
                              if spec["non_response_value"] else "None configured")
            return page("Confirm coding run", f'''<section class="card"><h1>Ready to code</h1>
<p><strong>{eligible}</strong> eligible responses; <strong>{blank}</strong> blank rows; <strong>{non_response}</strong> configured non-response rows.</p>
<p>Non-response value: {marker_summary}. Excluded rows are not sent to the model.</p>
<p>Worksheet: {e(sheet)} · Response column: {e(spec['column'])}</p><p>Question: {e(spec['question'])}</p>
<p>{len(codes)} codes · {e(run_config['provider'])} / {e(run_config['model'])}</p>
<p>Coding continues on the server if you close the browser. Download results as CSV when it finishes.</p>
<form method="post">{fields}<input type="hidden" name="confirmed" value="yes"><button type="submit">Start coding</button></form>
<p><a href="/runs/{run_id}/setup?{e(urlencode({'sheet': sheet}))}">Back to mapping</a></p></section>''')
        if not store.submit(user["id"], run_id, spec):
            raise HTTPException(404, "Run not found.")
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/retry")
    async def retry(run_id: str, request: Request, user=Depends(current_user)):
        check_origin(request)
        values = await form(request)
        check_csrf(user["csrf"], values.get("csrf", ""))
        owned_job(user, run_id)
        if not store.retry_failed(user["id"], run_id):
            raise HTTPException(409, "Only a finished run with unsuccessful responses can be retried. Refresh its status.")
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}")
    def status(run_id: str, user=Depends(current_user)):
        job = owned_job(user, run_id)
        if job["status"] == "draft":
            return RedirectResponse(f"/runs/{run_id}/setup", status_code=303)
        counts = job["counts"]
        count_list = "".join(f'<div><dt>{e(key.replace("_", " ").title())}</dt><dd>{value}</dd></div>' for key, value in counts.items())
        downloads = "".join(f'<p><a class="button" href="/api/files/{job[key]}">{label}</a></p>'
                            for key, label in (("result_id", "Download coded CSV"), ("manifest_id", "Download run manifest")) if job[key])
        error = f'<p class="error" role="alert">{e(job["error"])}</p>' if job["error"] else ""
        diagnostics = ""
        if counts.get("failed"):
            error += '<p class="error">Some responses failed. Their code cells are blank.</p>'
            report = failure_report(store.row_results(run_id))
            summary = "".join(f'<tr><td>{item["count"]}</td><td>{e(item["reason"])}</td><td>{e(item["guidance"])}</td></tr>'
                              for item in report["summary"])
            rows = "".join(f'<tr><td>{item["row_id"]}</td><td>{e(item["reason"])}</td></tr>' for item in report["rows"])
            diagnostics = f'''<h2>Why responses failed</h2>
<div class="table-scroll"><table><thead><tr><th>Responses</th><th>Reason</th><th>Would retrying help?</th></tr></thead><tbody>{summary}</tbody></table></div>
<details><summary>Failed response IDs ({len(report['rows'])})</summary>
<p>Match these IDs to Survey_Row_ID in the CSV. IDs follow the original worksheet order.</p>
<div class="table-scroll"><table><thead><tr><th>Survey row ID</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div></details>'''
        retry_form = ""
        if job["status"] in ("failed", "completed_with_errors") and (counts.get("failed") or counts.get("remaining")):
            retry_form = f'''<h2>Retry unsuccessful responses</h2>
<p>Successful results, blank rows and configured non-responses are preserved. Failed and unprocessed responses are sent one at a time using the original model and settings. New CSV and manifest downloads appear when this attempt finishes. Additional provider charges may apply.</p>
<form action="/runs/{run_id}/retry" method="post">{hidden(user)}<button type="submit">Retry failed responses</button></form>'''
        state = job["status"].replace("_", " ").title()
        response = page(job["name"], f'''<section class="card"><p><a href="/">← My runs</a></p><h1>{e(job['name'])}</h1>
<p role="status"><strong>{e(state)}</strong></p>{error}<dl class="counts">{count_list}</dl>{diagnostics}{retry_form}{downloads}
<p><a href="/runs/{run_id}">Refresh status</a></p><p class="muted">You can close the browser and return from My runs.</p></section>''')
        if job["status"] in ("queued", "running"):
            response.headers["Refresh"] = "5"
        return response
