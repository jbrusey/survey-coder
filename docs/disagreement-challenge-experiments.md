# Qwen disagreement challenge experiments

These intentionally error-enriched sets contain eligible comments where the
recorded Qwen2.5-Coder-32B-Instruct prediction differs from the human labels on
at least one code. They are useful for diagnosing and directly targeting known
failure cases. Because rows were selected by looking at Qwen's errors, challenge
scores are diagnostic and must not be presented as unbiased performance on new
survey comments.

## Build the challenge inputs

    uv run python scripts/create_disagreement_challenges.py

The script joins the saved development and one-time holdout predictions by
UserID, checks the reference code columns, and creates for each question:

- input.csv: comments and IDs for all rows with at least one Qwen mismatch.
- reference.csv: corresponding human code labels (blank labels are encoded as
  zero, per the benchmark definition).
- disagreements.csv: human labels, baseline Qwen predictions, and mismatch
  indicators for each code.
- metadata.json: source model, counts, and baseline challenge metrics.

Default output is outputs/staff_pulse/disagreement_challenge_qwen25coder32b/.
The script can be rerun to rebuild these generated, gitignored artifacts.

## Run a model or prompt candidate on the challenge set

Pass the challenge input/reference together with the matching base config and a
candidate manifest. --train-only --all-rows explicitly scores every row
provided and does not invoke final validation. Keep each run in its own empty
output directory. Use this for controlled A/B comparisons: change only model
or prompt, and hold comments, codebook, batch size, temperature, output-token
limit, and structured-output mode constant.

For Ollama, the coding CLI can use its native /api/chat route and JSON Schema
constrained format. Add the following to that candidate's llm override:

    {
      "provider": "openai",
      "model": "qwen2.5-coder:32b",
      "base_url": "http://localhost:11434",
      "ollama_native": true,
      "structured_output": true,
      "temperature": 0.0,
      "max_output_tokens": 4096
    }

When enabled, the schema restricts each result ID to the IDs in its current
batch and each returned code to an exact codebook header. This prevents malformed
JSON and invented code strings from being accepted, while leaving the semantic
classification decision to the model.

The checked-in parity manifest keeps batch size, temperature, token cap,
codebook, prompt, and schema mode aligned between vLLM and Ollama. Review and
adjust the vLLM base URL if it has changed. Example for question 1:

    uv run python -m survey_coder.auto_research \
      --input outputs/staff_pulse/disagreement_challenge_qwen25coder32b/question_1/input.csv \
      --reference outputs/staff_pulse/disagreement_challenge_qwen25coder32b/question_1/reference.csv \
      --config outputs/staff_pulse/handcoded_comparison/question_1_config.json \
      --candidates auto_research_candidates.backend_parity.example.json \
      --output-dir outputs/staff_pulse/disagreement_runs/question_1_backend_parity \
      --all-rows --train-only --batch-size 5 --target-kappa 0.6 --max-output-tokens 4096

Repeat with question 2's input, reference, and config in a separate output
directory. This compares backend behavior on the same curated examples. The
selected-candidate summary and each candidate result include Kappa and coverage;
compare them with the baseline challenge metrics in metadata.json. Because every
included comment was a Qwen disagreement, row-exact match starts at zero by
construction and the pooled Kappa is challenge-set-specific.
