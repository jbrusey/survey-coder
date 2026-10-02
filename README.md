# Qualitative Feedback Analysis Pipeline

This project automates the first phase of qualitative analysis for student feedback. It uses Large Language Models (LLMs) to identify recurring themes and apply them to thousands of responses, producing one-hot encoded datasets ready for statistical analysis.

## Key Features

- **Multi-Model Support:** Works with OpenAI (GPT-4), Google Gemini (1.5/2.0), and local models via Ollama.
- **Single Source of Truth:** Codebooks are stored as human-readable Markdown (`.md`) files. You can edit definitions and rules in plain text, and the pipeline immediately respects your changes.
- **Two-Stream Analysis:** Processes Positive and Improvement feedback separately to maintain thematic clarity.
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

### 3. API Keys
Create a `.env` file in your **home directory** (`~/.env`) or the **project root** (`./.env`) with your keys:
```text
OPENAI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
```
*Note: If using Ollama, no key is required.*

---

## The Workflow

### Phase 1: Theme Discovery
Generate draft qualitative codebooks from a sample of your data.
```bash
uv run generate-codebooks \
  --input "feedback.xlsx" \
  --output-dir "outputs" \
  --positive-column "Comment1Positive" \
  --improvement-column "Comment1Improvement" \
  --model "gpt-4o-mini"
```
**Output:** `positive_codebook.md` and `improvement_codebook.md`.

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
  --positive-codebook "outputs/positive_codebook.md" \
  --improvement-codebook "outputs/improvement_codebook.md" \
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

```bash
uv run apply-codebooks \
  --input "feedback.xlsx" \
  --positive-codebook "codebooks/specialized_positive.md" \
  --improvement-codebook "codebooks/specialized_improvement.md" \
  --output "benchmark_results.csv" \
  --model "gemini-flash-latest" \
  --provider "google"
```

---

## Command Reference

| Script | Purpose | Key Flags |
| :--- | :--- | :--- |
| `generate-codebooks` | Initial theme discovery | `--sample-size`, `--batch-size`, `--provider` |
| `apply-codebooks` | Full dataset coding | `--positive-codebook`, `--batch-size` |
| `evaluate-results` | Validation & Metrics | `--input`, `--output` |

### Tests

```bash
uv run python -m unittest discover -s tests
```

### Provider Support
Toggle providers using the `--provider` and `--model` flags:
- **OpenAI:** `--provider openai --model gpt-4o`
- **Gemini:** `--provider google --model gemini-2.0-flash`
- **Ollama:** `--provider openai --model gemma4:latest --openai-base-url http://localhost:11434/v1`

---

## Security and Privacy
- **Student Data:** Student comments may contain personal data. The scripts are designed to process data locally and do not log full comments to the console.
- **Credentials:** Always use `.env` files and never commit API keys to shared repositories.
