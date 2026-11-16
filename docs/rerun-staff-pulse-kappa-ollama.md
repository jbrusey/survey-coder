# AI task: rerun staff-pulse human-vs-AI agreement with Ollama

Rerun the two 400-row comparisons using the local Ollama server at port `11434` and model `qwen3.8:latest`. Do not edit the source workbook, human reference files, or existing results. Save new results separately.

## 1. Check the local model endpoint

Confirm Ollama is running and that `ollama list` contains `qwen3.8:latest`. The Survey Coder CLI uses Ollama’s OpenAI-compatible API at `http://localhost:11434/v1`. Do not silently substitute another model if the requested model is unavailable; report the issue and stop.

## 2. Rerun Survey Coder

From the repository root, run both commands. Explicit CLI options override the provider/model in the existing configs:

```bash
uv run apply-codebooks \
  --input outputs/staff_pulse/handcoded_comparison/question_1_input.csv \
  --config outputs/staff_pulse/handcoded_comparison/question_1_config.json \
  --output outputs/staff_pulse/handcoded_comparison/question_1_ollama_results.csv \
  --id-column UserID --batch-size 25 \
  --provider openai --model qwen3.8:latest \
  --openai-base-url http://localhost:11434/v1

uv run apply-codebooks \
  --input outputs/staff_pulse/handcoded_comparison/question_2_input.csv \
  --config outputs/staff_pulse/handcoded_comparison/question_2_config.json \
  --output outputs/staff_pulse/handcoded_comparison/question_2_ollama_results.csv \
  --id-column UserID --batch-size 25 \
  --provider openai --model qwen3.8:latest \
  --openai-base-url http://localhost:11434/v1
```

The inputs contain the first 400 data rows from the two question sheets; `D/A` values were blanked for coding. Keep the original `question_1_reference.csv` and `question_2_reference.csv` as the hand-coded labels. Confirm each new results file has 400 rows and inspect CLI logs for failed batches before calculating metrics. If a batch failed, report that rather than treating the missing model output as a true negative.

## 3. Calculate Cohen’s kappa

Join each new results file to its corresponding reference file by `UserID`. For each question, use rows with a non-empty comment other than `D/A`/`N/A`, and retain the established comparison rule: compare comments with at least one hand-coded positive category. For each category, compare the human 0/1 label with the matching model one-hot column (`question_1_<category>` or `question_2_<category>`). Report:

- Per-category Cohen’s kappa and number of compared comments.
- Pooled binary-cell Cohen’s kappa across the categories, plus raw cell agreement.
- Counts of eligible comments, compared comments, and any missing/failed model predictions.

Use the standard Cohen’s kappa for two raters on binary labels. If a category has no variation and kappa is undefined, report `NA` and its sample size—do not replace it with zero. Save the fresh per-category results and a short summary under `outputs/staff_pulse/handcoded_comparison/` with `ollama` in their filenames. Do not overwrite `code_alignment.csv` or `alignment_summary.txt`.

The existing inputs, codebooks, configs, and references are in `outputs/staff_pulse/handcoded_comparison/`. No additional Survey Coder code is needed: its OpenAI-compatible provider option supports the Ollama endpoint.
