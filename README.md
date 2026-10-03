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

### Graphical interface

A portable Tkinter GUI is included for Windows, macOS, and Linux. It selects CSV/Excel input files and worksheets, lets you choose columns and runtime settings, and runs discovery/coding in-process (without shelling out to the CLI):

```bash
uv run survey-coder-gui
```

Choose **OpenAI**, **Google**, or **vLLM** on the model step. The GUI automatically remembers the last provider, model, endpoint, and output-token limit as local model preferences. For vLLM, enter an OpenAI-compatible `/v1` endpoint, or set `VLLM_BASE_URL` before launching the GUI, then choose **Refresh models** to retrieve model IDs from the server. If discovery fails, you can still type a model ID manually. Set `VLLM_API_KEY` in the environment for an authenticated endpoint.

**Save analysis setup** stores the model settings, context, stream mappings, output and prompt folders, and processing settings. To restore one, select the matching data file, then choose **Load analysis setup** beside the column mappings. Loading applies the saved values and reports mappings unavailable in the selected data. Data-file paths and API keys are deliberately excluded; keys are always read from the environment.

### 3. API Keys
Create a `.env` file in your **home directory** (`~/.env`) or the **project root** (`./.env`) with your keys:
```text
OPENAI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
VLLM_API_KEY=your_vllm_key_here
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
    "base_url": "https://your-vllm-host.example/v1"
  },
  "streams": [
    {"name": "Teaching", "column": "TeachingComment", "question": "What did you think of the teaching?", "prefix": "Teaching"}
  ]
}
```
Supported provider values are `openai`, `google`, and `vllm`. The GUI’s editable max-output-token field defaults to `32768` when vLLM is selected; other providers leave it blank. CLI flags such as `--provider`, `--model`, `--openai-base-url`, and `--max-output-tokens` override the corresponding config defaults. Set optional `llm.max_output_tokens` to a positive integer to configure an explicit limit, or omit it in CLI/config use to let the provider/server choose. The GUI saves this setting with the endpoint. vLLM uses the OpenAI-compatible endpoint and `VLLM_API_KEY` environment variable when authentication is enabled.
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

```bash
uv run python -m unittest discover -s tests
```

### Provider Support
Toggle providers using the `--provider` and `--model` flags:
- **OpenAI:** `--provider openai --model gpt-4o`
- **Gemini:** `--provider google --model gemini-2.0-flash`
- **Ollama:** `--provider openai --model gemma4:latest --openai-base-url http://localhost:11434/v1`
- **vLLM:** `--provider vllm --model Qwen/Qwen3.5-9B --openai-base-url "$VLLM_BASE_URL"`

---

## Security and Privacy
- **Student Data:** Student comments may contain personal data. The scripts are designed to process data locally and do not log full comments to the console.
- **Credentials:** Always use `.env` files and never commit API keys to shared repositories.
