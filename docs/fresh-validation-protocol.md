# Fresh validation protocol

The original 200-row holdout for each staff-pulse question has been evaluated and must not be reused. Candidate comparisons also repeatedly used the original 200-row development partition, so it is no longer suitable for further tuning. The current Qwen2.5-Coder candidate did not meet κ ≥ 0.60. This protocol defines the next valid evaluation set; it does not create or relabel data.

## Data required

For each question, provide a newly sampled, representative set of at least 400 comments with:

- An input CSV with unique IDs and `Comment`.
- A separate reference CSV with matching IDs, `Comment`, and the existing binary/blank code columns. For each ID, comments must match; blank, `D/A`, and `N/A` are treated as equivalent missing responses.
- Human coding completed independently of model predictions. Keep the holdout labels inaccessible to candidate authors until final scoring.
- A documented sampling frame, date, inclusion/exclusion rules, and coder/adjudication process.

Exclude comments/IDs from both consumed benchmark partitions. Shuffle the fresh sample with a recorded random seed; use the first 200 IDs as development and the remaining rows as a sealed holdout. Preserve both ID lists and exclude blank/D/A/N/A comments consistently.

## Run and record

1. Confirm ID alignment, uniqueness, code columns, and at least one eligible row in both partitions.
2. Iterate model/prompt/codebook candidates using `auto-research --train-only` on the development partition, passing a reference file containing only development IDs. Holdout labels stay outside candidate-author access. Preserve manifests and artifacts.
3. Select one candidate before exposing holdout labels. Run the ordinary auto-research command once per question to score the holdout.
4. Report pooled binary-cell Cohen's κ, evaluated cells, missing predictions, model/provider/endpoint, temperature, batch size, token limits, exact prompt/codebook, and commands. Undefined κ or fewer than 80% of eligible cells predicted is failure; always report missingness separately.
5. Do not revise a candidate based on this holdout. Any further iteration requires another untouched validation set.

Example input/reference paths and the correct approval for using a new sample must be supplied by the data owner; do not fabricate data or labels.