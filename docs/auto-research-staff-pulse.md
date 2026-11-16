# Automated staff-pulse coding research

`auto-research` runs candidate codebook/prompt/model configurations against the
first 200 rows for development, picks the best candidate by pooled binary-cell
Cohen's kappa, and codes rows 201–400 once for final validation. The final output
line is JSON containing only `success`, `seconds`, and `kappa`. Per-candidate logs,
configs, prompt snapshots, codebook snapshots, and coded data are retained under the output directory for auditability. The effective candidate list is saved as `candidates.json`. Candidate and final result JSON, console summaries, and candidate `summary.jsonl` include evaluated/missing cell counts and prediction coverage. Candidate/final result JSON also report per-code kappa, eligible/evaluated cells, human/model positive counts, and missing predictions. Candidate setup failures are recorded with a `reason` and do not stop later candidates.

## Run

Start with the benchmark's question-specific input, reference, and config. Put
candidate overrides in a JSON list; supported keys are `llm`, `config`,
`codebooks` (mapping stream names to codebook paths), `streams`, and `prompts`
(mapping prompt filenames to full replacement text).

```bash
uv run python -m survey_coder.auto_research \
  --input outputs/staff_pulse/handcoded_comparison/question_1_input.csv \
  --reference outputs/staff_pulse/handcoded_comparison/question_1_reference.csv \
  --config outputs/staff_pulse/handcoded_comparison/question_1_config.json \
  --candidates auto_research_candidates.example.json \
  --output-dir outputs/staff_pulse/auto_research/question_1_dev_only \
  --train-rows 200 --target-kappa 0.6 --train-only
```

The example is development-only: it evaluates candidates on development rows
and returns without coding or scoring holdout. With `--train-only`, the
reference must contain development IDs only, so holdout labels are not
available to candidate runs. Do not remove `--train-only` for these consumed
benchmark files. Use ordinary final validation only with a newly designated
untouched holdout under the approved protocol.

For final validation, the reference must contain exactly one row per input ID. In
`--train-only` mode, it must contain development IDs only. Both forms require a
`Comment` column and one binary/blank column per human code; values outside
`0`, `1`, and blank are rejected. Each candidate codebook's
headers must exactly match the reference code columns. The coded output must have matching
`<stream-prefix>_<human-code>` columns. The runner excludes blank, `D/A`, and
`N/A` comments from evaluation, treats an unmarked human code as zero, and drops
missing model predictions rather than counting them as negative. A candidate is
not viable if it predicts fewer than 80% of eligible binary cells. It fails to
meet the target if Kappa is undefined or below 0.6.

## Important design constraints

- Rows 1–200 are the development set. Rows 201–400 remain sealed from candidate
  selection and are evaluated once using the best development candidate; use
  `--train-only` to iterate without touching holdout rows.
- A successful final result means holdout pooled-cell Kappa is at least the
  requested threshold. It does not guarantee that any particular individual
  category meets that threshold.
- Candidate model names and endpoints are supplied by the operator; no model is
  silently substituted. Ensure the endpoint and credentials are available.
- Each candidate is coded in a fresh subprocess. An endpoint can therefore see
  repeated calls for each candidate and once more for final validation.
- Iteration is manifest-driven rather than self-editing: the runner executes
  the supplied candidates and reports each score so new candidates can be
  authored without changing evaluator code. Keep reference labels out of the
  candidate prompts and codebooks to avoid leakage.
- The input/reference pair and intended aggregate metric need confirmation for
  any production claim. This example uses the two existing 400-row comparison
  sets; run the mechanism separately for question 1 and question 2.
- Initial validation using the live `Qwen/Qwen2.5-Coder-32B-Instruct` scored
  holdout κ=0.27398 for question 1 and κ=0.00000 for question 2 (both with no
  missing predictions). These holdouts are consumed; do not tune on or rerun
  them. Use a newly designated untouched holdout for further validation.
