# Qualitative Feedback Analysis Pipeline

This project automates the first phase of qualitative analysis for student feedback. It uses Large Language Models (LLMs) to identify recurring themes and apply them to thousands of responses, producing one-hot encoded datasets ready for statistical analysis.

## Key Features

- **Multi-Model Support:** Works with OpenAI (GPT-4), Google Gemini (1.5/2.0), and local models via Ollama.
- **Single Source of Truth:** Codebooks are stored as human-readable Markdown (`.md`) files. You can edit definitions and rules in plain text, and the pipeline immediately respects your changes.
- **Configurable streams:** Analyse any number of questions/columns, each with its own question, codebook, and output prefix.
- **Configurable prompts:** Edit the plain-text templates in `prompts/` (or pass another directory with `--prompt-dir`) without changing Python code.
- **Evaluation Suite:** Includes built-in Cohen's Kappa and confusion matrix generation to validate AI results against human "Gold Standard" coding.

---

## Repository Layout

- `src/survey_coder/` — Python package and command implementations
- `tests/` — automated tests
- `codebooks/` — maintained benchmark codebooks
- `scripts/` — pipeline orchestration scripts
- `docs/` — supporting documentation

## Getting Started

### 1. Prerequisites
The project uses `uv` for lightning-fast, reproducible dependency management. [Install uv](https://github.com/astral-sh/uv) if you haven't already.

### 2. Setup
Clone the repository and install dependencies:
```bash
uv sync
```

### Web interface

The web interface has one **Survey user** role. Users can upload a spreadsheet
and existing Markdown codebook, code one question and download their own results.
Sign-in, runs, source files and downloads enforce ownership on the server.
There are no public registration, administrator or separate analyst roles.

Install the optional web dependencies and provision an account:

```bash
uv sync --extra web
uv run --extra web survey-coder-web create-user james
```

The command prompts for a password of at least 12 characters and confirmation;
it does not accept passwords as command-line arguments. For local development:

```bash
uv run --extra web survey-coder-web serve --local-http
```

From **My runs**, choose **New coding run**:

1. Upload an XLSX workbook and an existing UTF-8 Markdown codebook. Limits are
   10 MiB for the workbook, 1 MiB for the codebook, and 10,000 data rows / 200
   columns / 20 MiB of decoded cell text in the selected sheet. Code names use
   level-two Markdown headings.
2. Select a worksheet, preview its data, choose the response and optional unique
   ID columns, and enter the question wording. If the survey uses a non-response
   value such as `D/A`, enter it in **Non-response value (optional)**. Matching
   uses the whole cell, trims surrounding whitespace and ignores letter case;
   `D/A` and ` d/a ` match, but a sentence containing `D/A` does not. Leaving
   the setting blank disables marker matching. Empty and whitespace-only cells
   are always excluded. Imported formulas use cached cell values; formatting
   is outside this CSV workflow.
3. Check eligible, blank and configured non-response counts and the configured provider/model, then choose
   **Start coding**. The status page refreshes while processing. You can close
   the browser and return through My runs.
4. Review any failures on the status page: reasons are grouped with counts and
   guidance on whether retrying may help. Expand **Failed response IDs** to match
   failures to `Survey_Row_ID` in the CSV. Choose **Retry failed responses** on a
   finished partial or failed run to queue only failed and unprocessed responses.
   Successful coding and excluded rows are preserved. Retries send one response
   per request using the original model and settings, and may incur additional
   provider charges. Repeated code-name failures need a codebook or model review;
   credential or request-setting errors need a maintainer fix first.
5. Download the coded CSV and run manifest. The CSV retains source columns and
   row order, adds generated `Survey_Row_ID` values, nullable `AI_<code>` columns,
   `AI_Codes_Applied`, `AI_Status` and `AI_Error`. Explicit empty code sets get
   zeros; blank, configured non-response and failed rows have missing code cells.
   Configured non-response rows have `AI_Status=non_response`, no `AI_Error`, and
   are never sent to the coding model. The manifest records the marker and matching
   rule. A partial run remains labelled as partial. These settings apply to new
   runs; existing run results do not change.

The default model is OpenAI `gpt-4o-mini`, using `OPENAI_API_KEY` from the server
environment or `.env`. To use another server-approved model, supply an existing
analysis setup JSON containing `context`, `streams` and `llm`:

```bash
uv run --extra web survey-coder-web serve --local-http --config survey_config.example.json
```

For the group's vLLM server at `100.101.42.121:8000`, use the supplied
`survey_config.vllm.json`:

```bash
env -u VLLM_API_KEY uv run --extra web survey-coder-web serve --local-http --config survey_config.vllm.json
```

This command removes an inherited API-key variable for this process so the
application can load `VLLM_API_KEY` from `~/.env` or the project `.env`. It does
not change either file or the calling shell. The web config uses `"model": "auto"`.
When you check a new coding run, the web service queries the server's model list
and selects its single advertised model. It shows the discovered ID before
submission and pins that ID in the run manifest. If the server advertises more
than one model, set an explicit model ID instead of assuming a default. The
config contains no credential. Automatic model selection applies to the web
workflow; CLI runs still require an explicit model ID.

Web coding with vLLM and JSON mode now requests a JSON schema that restricts
returned codes to exact codebook names and returned IDs to the current batch.
This uses [vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/),
including for retries of older runs without a saved capability setting. It
prevents invented or paraphrased code names when the server enforces the schema;
the application still validates IDs, duplicate codes and all returned results.
The manifest records whether this constraint was requested. New prompts use
actual response IDs and empty code-list examples instead of placeholder names.

Set `llm.structured_outputs` explicitly to `false` for new web runs on a vLLM
server that does not support this feature. Other OpenAI-compatible providers
can opt in with `true` when supported; it requires `llm.json_mode=true`.
This option currently applies to the web worker, not CLI coding or Google.
A rejected schema request remains a visible failure with maintainer guidance;
the worker does not silently fall back to unconstrained output or make extra
requests. Retries retain explicit settings saved in the original run.

The web service uses that setup's context and LLM settings; users choose their
worksheet and question in the browser. `SURVEY_CODER_WEB_CONFIG` also sets this
path. For vLLM/OpenWebUI, use the existing `llm.base_url` or provider base-URL
environment variable and keep keys in the server environment. Endpoint URLs
must not embed credentials or query parameters. Model selection and credentials
remain server-managed. The setup page blocks submission when a required
OpenAI/Google key is absent; it does not claim that an untested key or endpoint
works. A successful coding request establishes connectivity.

The service starts a separate worker process and persists its queue and row
checkpoints in SQLite. Workers send response text and generated row IDs, plus
the question, codebook and prompts, to the configured model; they do not send
unrelated spreadsheet columns. The manifest snapshots the provider settings,
prompts, codebook, operator and file hashes without API keys. Provider errors
appear as row failures with safe explanations, without exposing provider messages
or generated invalid labels. New manifests also record failure summaries, row IDs,
retry count and request batch size. Existing failures retain their original
explanations; older generic failures cannot be diagnosed retrospectively.
A retry regenerates the CSV and manifest when it finishes; previous downloaded
files remain snapshots of their earlier attempt. There is no cancellation screen.
After interruption, an unfinished job can resume
from its checkpoints when its worker lease expires (up to about one minute).
An interrupted provider request can incur another charge on resumption.

Open [the local web interface](http://127.0.0.1:8000) and sign in. Local HTTP mode
also accepts `http://localhost:8000` and binds to loopback. Keep the same hostname
when loading and submitting a form; cookies are specific to that hostname.
For a hosted installation, use an HTTPS reverse proxy and
specify the public origin:

```bash
uv run --extra web survey-coder-web serve --origin https://survey.example.org
```

This command listens on `127.0.0.1:8000` behind the proxy and requires secure
session cookies. The proxy must preserve the public Host header. Sessions expire
after eight hours; signing out revokes the server-side session. Sign-in forms
and sign-out requests require CSRF tokens. The service limits sign-in attempts
to ten per minute per directly connected client address; a reverse proxy shares
that allowance unless the deployment adds its own trusted-client rate limiting.
Forwarded headers do not override client addresses.

To reset a forgotten password without losing runs or files:

```bash
uv run --extra web survey-coder-web reset-password james
```

Enter the new password twice at the hidden prompts (at least 12 characters).
The command revokes existing sessions and preserves the user's identity, runs
and files. The running server reads the new password on the next sign-in; no
restart is needed. If you previously disabled the account, add `--enable` to the
reset command to re-enable it. Use the same database path as the server.

To re-enable an account without another password reset:

```bash
uv run --extra web survey-coder-web enable-user james
```

To disable an account and revoke its sessions:

```bash
uv run --extra web survey-coder-web disable-user james
```

Account names are case-insensitive. The default SQLite database lives at
`~/.survey-coder/web.sqlite3`; override it with `SURVEY_CODER_WEB_DATABASE` or
the global `--database /path/to/web.sqlite3` option before the subcommand.
The database contains salted scrypt password hashes, hashed session tokens and
private resource records. Keep it outside public file-serving directories and
include it in the deployment's access, backup and retention arrangements.
Account sign-in is a local foundation pending the hosting team's institutional
identity integration. Provider credentials stay in the server environment.

`GET /health/live` checks web-process liveness only. `GET /health/ready` checks
the worker process and required configuration, not live provider connectivity.
Authenticated endpoints are `GET /api/me`, `GET /api/runs`,
`GET /api/runs/{run_id}` and `GET /api/files/{file_id}`. The resource endpoints
enforce ownership on the server. Browser pages use `/runs/new`,
`POST /runs/upload`, `/runs/{id}/setup`, `POST /runs/{id}/start` and `/runs/{id}`.
Uploads, submission and `POST /runs/{id}/retry` require the current session's CSRF
token. The run API includes safe failure summaries and failed row IDs.

Run the web tests together with the existing suite:

```bash
uv run --extra web pytest
```

### Graphical interface

A portable Tkinter GUI is included for Windows, macOS, and Linux. It selects CSV/Excel input files and worksheets, lets you choose columns and runtime settings, and runs discovery/coding in-process (without shelling out to the CLI):

```bash
uv run survey-coder-gui
```

The GUI defaults to `Documents\Survey Coder Results` on Windows. Provider API
keys are loaded from `.env` and are never written to analysis setup files.

#### Windows installer

End users do not need Python, `uv`, or a terminal. Build the application on a
Windows x64 machine with [uv](https://docs.astral.sh/uv/) and
[Inno Setup 6](https://jrsoftware.org/isinfo.php) installed:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

The script creates a portable application in `dist\Survey Coder` and, when
Inno Setup is available, a per-user installer in `dist\installer`. The
installer creates a Start Menu shortcut and offers an optional Desktop
shortcut; it does not require administrator rights.

PyInstaller builds are platform-specific, so the Windows installer must be
built on Windows rather than macOS or Linux. For wider distribution, sign the
generated executable and installer with your organisation's code-signing
certificate to reduce Microsoft SmartScreen warnings.

Choose each response column’s Markdown codebook file and result-column prefix on the first step. Bare codebook filenames are stored in the output folder; full or relative paths can point to existing handcrafted codebooks. The GUI rejects unsafe or duplicate filenames.

Choose **OpenAI**, **Google**, **vLLM**, or **OpenWebUI** on the model step. The GUI automatically remembers the last provider, model, endpoint, and output-token limit as local model preferences. Model choices for OpenAI and Google are listed in the root `models.json`; edit that file or point `SURVEY_CODER_MODELS_CONFIG` at another JSON file to customize them. For vLLM, enter its OpenAI-compatible `/v1` endpoint. For OpenWebUI, enter its `/api` endpoint (for example, `http://localhost:3000/api`). You can also set `VLLM_BASE_URL` or `OPENWEBUI_BASE_URL` before launching the GUI. Choose **Refresh models** to retrieve model IDs from either server; if discovery fails, type a model ID manually.

**Save analysis setup** stores the model settings, context, stream mappings, output and prompt folders, and processing settings. To restore one, select the matching data file, then choose **Load analysis setup** beside the column mappings. Loading applies the saved values and reports mappings unavailable in the selected data. Data-file paths and API keys are deliberately excluded; keys are always read from the environment.

### 3. API Keys
Create a `.env` file in your **home directory** (`~/.env`) or the **project root** (`./.env`) with your keys. The application loads these with `python-dotenv`; environment variables already set in your shell take precedence.
```text
OPENAI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
VLLM_API_KEY=your_vllm_key_here
OPENWEBUI_API_KEY=your_openwebui_key_here
OPENWEBUI_BASE_URL=http://localhost:3000/api
```
*Note: If using Ollama, no key is required.*

### vLLM Server

The pipeline script defaults to the group's OpenAI-compatible vLLM endpoint:

```bash
export VLLM_HOST="100.101.42.121"
export VLLM_BASE_URL="http://${VLLM_HOST}:8000/v1"
export VLLM_API_KEY="your-key-here"

curl "$VLLM_BASE_URL/models" \
  -H "Authorization: Bearer $VLLM_API_KEY"
bash scripts/run_pipeline.sh
```

The configured model is `Qwen/Qwen3.5-9B`. Never commit the API key.

---

## The Workflow

### Phase 1: Theme Discovery
Generate draft qualitative codebooks from a sample of your data.
```bash
uv run generate-codebooks \
  --input "feedback.xlsx" \
  --output-dir "outputs" \
  --config survey_config.example.json \
  --model "gpt-4o-mini"
```
The config contains a `streams` list and can optionally hold reusable LLM defaults in `llm`. Add as many stream entries as needed:
```json
{
  "context": "student module feedback",
  "llm": {
    "provider": "vllm",
    "model": "Qwen/Qwen3.5-9B",
    "base_url": "https://your-vllm-host.example/v1",
    "temperature": 0.0
  },
  "streams": [
    {"name": "Teaching", "column": "TeachingComment", "question": "What did you think of the teaching?", "codebook": "outputs/teaching_codebook.md", "prefix": "Teaching"}
  ]
}
```
Supported provider values are `openai`, `google`, `vllm`, and `openwebui`. The GUI’s editable max-output-token field defaults to `32768` when vLLM is selected; other providers leave it blank. CLI flags such as `--provider`, `--model`, `--openai-base-url`, `--max-output-tokens`, and `--temperature` override the corresponding config defaults. Set optional `llm.temperature` to a finite nonnegative number (default `0.0`). Set optional `llm.max_output_tokens` to a positive integer to configure an explicit limit, or omit it to let the provider/server choose. OpenWebUI uses an `/api` base URL supplied in config, with `--openai-base-url`, or through `OPENWEBUI_BASE_URL`; set `OPENWEBUI_API_KEY` when the server requires authentication.

For OpenAI-compatible `apply-codebooks` requests, optional `llm.think` passes a boolean `think` option to the endpoint (for example, set it to `false` for Ollama models that support thinking control). JSON response formatting is enabled by default; set `llm.json_mode` to `false` for endpoints that do not support the OpenAI JSON response format. These settings avoid inferring endpoint capabilities from model names or URL hostnames.
**Output:** one codebook per configured stream.

### Phase 1.5: Human Refinement (Optional)
Open the generated `.md` files in any text editor. You can:
- Rename codes.
- Merge themes.
- Refine "Include/Exclude" rules to sharpen the AI's logic.

### Phase 2: Row-Level Coding
Apply your refined codebook to the full dataset.
```bash
uv run apply-codebooks \
  --input "feedback.xlsx" \
  --config survey_config.example.json \
  --output "final_results.csv" \
  --model "gpt-4o-mini"
```
**Output:** `final_results.csv` with one-hot encoded columns for every code (e.g., `Pos_Clear_Explanation`).

For troubleshooting, pass `--diagnostic-log diagnostics.jsonl` to append each batch's prompts, response, and any error as JSONL. These logs can contain survey comments and model output, so store and share them with the same care as the source data.

### Phase 3: Evaluation
Compare the LLM results against manual coding to check for reliability.
```bash
uv run evaluate-results \
  --input "final_results.csv" \
  --output "evaluation_report.txt"
```
**Output:** A report containing Accuracy, F1-Score, and Cohen's Kappa agreement.

---

## Benchmark Evaluation
To ensure alignment with the human "Gold Standard" coding during benchmark runs, specialized codebooks must be used. These are designed to map precisely to the dimensions used in the manual validation.

Put the specialised codebook paths in a config file, then run:
```bash
uv run apply-codebooks --input "feedback.xlsx" --config benchmark_config.json \
  --output "benchmark_results.csv" --model "gemini-flash-latest" --provider "google"
```

---

## Command Reference

| Script               | Purpose                 | Key Flags                                     |
|:---------------------|:------------------------|:----------------------------------------------|
| `generate-codebooks` | Initial theme discovery | `--config`, `--sample-size`, `--batch-size` |
| `apply-codebooks`    | Full dataset coding     | `--config`, `--batch-size`                 |
| `evaluate-results`   | Validation & Metrics    | `--input`, `--output`                         |

### Tests

Use `uv run` to run tests in the project's managed environment:

```bash
uv run pytest
```

Or run the standard-library test runner:

```bash
uv run python -m unittest discover -s tests
```

### Provider Support
Toggle providers using the `--provider` and `--model` flags:
- **OpenAI:** `--provider openai --model gpt-4o`
- **Gemini:** `--provider google --model gemini-2.0-flash`
- **Ollama:** `--provider openai --model gemma4:latest --openai-base-url http://localhost:11434/v1`
- **vLLM:** `--provider vllm --model Qwen/Qwen3.5-9B --openai-base-url "$VLLM_BASE_URL"`
- **OpenWebUI:** `--provider openwebui --model your-model-id --openai-base-url "$OPENWEBUI_BASE_URL"`

---

## Security and Privacy
- **Student Data:** Student comments may contain personal data. The scripts are designed to process data locally and do not log full comments to the console.
- **Credentials:** Always use `.env` files and never commit API keys to shared repositories.
