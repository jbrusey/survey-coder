# Goal: Improve human–AI coding agreement to κ ≥ 0.60

## Objective

Iteratively improve this project's qualitative survey coding workflow until pooled binary-cell Cohen's kappa on the designated staff-pulse held-out validation set is at least **0.60**. Preserve a leakage-free development/holdout boundary. The loop is continuous: after reaching the threshold, continue seeking measured improvement until the operator stops it; never claim completion solely from development-set results.

## Scope

- Primary benchmark: each of `question_1` and `question_2` in `outputs/staff_pulse/handcoded_comparison/`, using its matching input, reference, and config.
- Evaluation: pooled binary cells as implemented in `src/survey_coder/auto_research.py`; blank human labels are zero for eligible labeled respondents, missing model predictions are excluded and counted. A candidate must predict at least 80% of eligible binary cells to be considered viable; report sample sizes, coverage, missing predictions, and per-category results when available.
- Improve codebooks, prompts, candidate configurations, language-model choice, inference settings, and necessary evaluator defects. Model comparison is in scope: the checked-in benchmark config says `Qwen/Qwen3.5-9B`, while authenticated `/v1/models` discovery verified the live vLLM model as `Qwen/Qwen2.5-Coder-32B-Instruct`. Treat these as distinct baseline/candidate settings. Compare against explicitly available Ollama models on `moore` (reachable via `ssh -L 11434:localhost:11434 moore`) when authorized and available. Treat provider, model ID, endpoint, and settings as recorded experimental variables; do not silently substitute models. Keep experiments reproducible and preserve artifacts.
- Assess each question separately; overall target is met only when both held-out kappas are at least 0.60.

## Non-goals

- Do not change or fabricate reference labels, tune against holdout labels, or leak reference labels into prompts/codebooks.
- Do not claim success from training/development kappa, accuracy, or a score on a different dataset.
- No broad pipeline rewrite, new provider/dependency, unrelated feature work, or destructive cleanup.
- Do not overwrite the original benchmark input/reference or existing comparison results.

## Measurable criteria and check

`./check.sh` prints `SCORE: <n>` where `n` is the best eligible held-out κ in hundredths (0–100), or 0 if none is present. It exits 0 only when both question 1 and question 2 have finite `kappa >= 0.60` and at least 80% prediction coverage (`evaluated_cells / (evaluated_cells + missing_predictions)`) in a `final_result.json` beneath `outputs/staff_pulse/auto_research/question_1/` and `.../question_2/`. Otherwise it exits nonzero. Missing, malformed, non-finite, or unsuccessful results do not pass. Keep candidate outputs in those directories (or update the checker and this specification together if the runner's layout changes).

The score is an objective gate, not a reason to stop: after both thresholds pass, continue experiments that plausibly improve held-out agreement, reporting the best validated score and avoiding repeated holdout tuning. The operator controls termination.

## Milestone roadmap

1. **Baseline and verify:** inspect both benchmark files, configs, codebooks and prior reports; run existing tests. Record current scores if reproducible without spending unexpected API/compute resources. Verify evaluator joins, code-column mapping, eligibility, and train/holdout separation.
2. **Establish safe experiment:** run the existing auto-research workflow independently per question with an explicit candidate manifest and outputs under the paths above. Confirm development selection uses only development rows and holdout is evaluated once after selection. Include model-selection candidates: first record the current vLLM model and settings (the checked-in benchmark configs currently say `Qwen/Qwen3.5-9B`; verify the live endpoint), then verify Ollama availability through the operator-provided SSH tunnel and record the exact Ollama model ID before adding it. Never infer a model ID or endpoint.
3. **Diagnose:** use development results and error analysis to identify high-impact confusion/coverage issues. Do not inspect holdout errors to design candidates. Include category-level support and missing predictions in reasoning.
4. **Iterate small changes:** make one or a few justified prompt/codebook/config changes per candidate batch. Keep prior artifacts; document candidate, rationale, development score, and resources used.
5. **Validate:** after selection, evaluate the selected candidate on the reserved holdout using the existing runner. Do not use that holdout result to revise that candidate; any subsequent iteration requires a fresh operator-approved validation protocol or a newly designated untouched holdout.
6. **Finish each cycle:** run relevant tests, inspect `git diff`, document reproducible commands/results, and commit coherent changes with a concise message. Never commit secrets or private raw survey data.
7. **Continue:** compare validated scores over time and repeat while the operator has not stopped the loop. Report blockers and ask the operator when credentials, compute, data authorization, or benchmark interpretation is needed.

## Quality standards

- Tests: run `uv run pytest`; add focused tests for any evaluator or automation logic changed. Do not silently weaken tests.
- Reproducibility: preserve candidate manifest, effective config, prompt/codebook snapshot, logs, and result JSON for every experiment; record model/provider and relevant parameters. No hidden model substitution.
- Integrity: holdout labels remain evaluation-only. Avoid repeated adaptive tuning against the same holdout; state this limitation plainly.
- Docs: update relevant experiment docs when commands, metric definitions, or artifact locations change.
- Git: review status/diff; commit only task-related source, docs, and manifests. Never include credentials, generated private data, or user changes already present.
- Reliability: undefined/non-finite κ or prediction coverage below 80% is failure, not zero-as-success. Report evaluated cell counts, missing predictions, and per-category caveats.

## Explicit assumptions

- The target means pooled-cell Cohen's κ on each of the two designated staff-pulse question holdouts, not a per-category guarantee.
- Benchmark files currently present in `outputs/staff_pulse/handcoded_comparison/` are authorized for local evaluation and are the intended data; confirm with the operator before external transmission. Initial inspection found 400 unique rows (IDs 0–399) in each question's input and reference. Checked-in configs specify vLLM `Qwen/Qwen3.5-9B`, but authenticated model discovery confirmed the endpoint serves `Qwen/Qwen2.5-Coder-32B-Instruct`.
- The default train/holdout split described by `docs/auto-research-staff-pulse.md` is valid only if each input/reference pair has the expected 400 ordered rows and the runner truly seals the latter partition. Verify before running; never assume this based only on filenames. The initial held-out partition has already been evaluated for both questions (q1 κ=0.27398; q2 κ=0.00000); do not use it for further tuning. Run development-only work until an operator-approved fresh validation protocol or a newly designated untouched holdout is available.
- Model endpoints/credentials and any API/compute budget are operator-controlled. Do not launch costly or externally transmitting experiments without the necessary authorization. Ollama on `moore` may be accessed by the operator-established tunnel at `http://localhost:11434/v1` if that Ollama instance exposes the OpenAI-compatible API; verify connectivity and model IDs first, and do not start SSH tunnels or remote model runs without authorization.
- The original holdout is consumed, and repeated candidate comparisons have also used the same 200 development rows. Pause further model/prompt/codebook selection on these rows to limit adaptive overfitting. Resume comparative tuning only with a fresh representative labeled sample and untouched holdout under `docs/fresh-validation-protocol.md`; otherwise restrict work to reproducibility, evaluator correctness, and reporting.
- The loop ends only when the operator stops it; reaching κ 0.60 is a threshold, not termination instruction.
