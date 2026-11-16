# Progress

## Current state

- Target: pooled held-out Cohen's κ ≥ 0.60 for both staff-pulse questions. The initial final validation has now been run: q1 κ=0.27398 and q2 κ=0.00000; `./check.sh` fails with SCORE 0. This holdout is consumed; do not tune against it.
- Existing historical development scores: question 1 `results.csv` κ=0.5305; question 2 `results.csv` κ=0.5601. Provenance unknown.
- Fresh development runs: live vLLM `Qwen/Qwen2.5-Coder-32B-Instruct`, batch size 5: q1 κ=0.57363 (2,409 cells), q2 κ=0.60738 (3,366 cells), no missing. q1 batch size 2 scored κ=0.49676. Ollama trials failed/underperformed. These are development-only. All are development-only; do not treat as holdout results.
- The checked-in vLLM config says `Qwen/Qwen3.5-9B`; authenticated discovery reports live `Qwen/Qwen2.5-Coder-32B-Instruct`. Fresh development-only inference was run against the live model; keep config and served model distinguished.
- `--train-only` is available, but both the original holdout and the repeatedly compared development rows are now consumed for model selection. A repository search found no additional gold/reference CSVs. Pause comparative tuning until a fresh labeled sample/untouched holdout is supplied; only work on evaluator correctness and reproducibility meanwhile.

## Next steps

1. Prefer a fast, low-cost development candidate/model that can emit exact codebook labels without truncation; avoid repeating known-slow Ollama configurations.
2. Review historical q1/q2 diagnostics (unknown provenance) and eligible-row live vLLM reports `outputs/staff_pulse/handcoded_comparison/question_{1,2}_vllm_development_diagnostic.md`; use only labeled development rows when interpreting per-code scores.
3. Do not reuse the consumed holdout or continue adapting on the repeatedly sampled development set. `docs/fresh-validation-protocol.md` specifies replacement data and one-time scoring; resume comparative model work only with a newly designated sample.
