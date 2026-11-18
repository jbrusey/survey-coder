"""Create Qwen disagreement challenge inputs from the recorded 400-row runs.

The resulting challenge sets are intentionally selected on errors and are for
diagnostic/model-prompt iteration, not unbiased performance estimates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from sklearn.metrics import cohen_kappa_score


ROOT = Path("outputs/staff_pulse")


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"UserID": str})


def make_challenge(question: str, output_root: Path) -> dict[str, int | str]:
    source = ROOT / "handcoded_comparison"
    input_df = read_csv(source / f"{question}_input.csv")
    reference_df = read_csv(source / f"{question}_reference.csv")
    development = read_csv(ROOT / "auto_research" / question / "candidate_001" / "train_coded.csv")
    holdout = read_csv(ROOT / "auto_research" / question / "final_validation" / "holdout_coded.csv")

    if input_df["UserID"].duplicated().any() or reference_df["UserID"].duplicated().any():
        raise ValueError(f"{question}: duplicate IDs in input/reference")
    predictions = pd.concat([development, holdout], ignore_index=True)
    if predictions["UserID"].duplicated().any():
        raise ValueError(f"{question}: development and holdout predictions overlap")
    code_columns = [name for name in reference_df if name not in {"UserID", "Comment"}]
    pred_columns = [f"{question}_{name}" for name in code_columns]
    absent = [name for name in pred_columns if name not in predictions]
    if absent:
        raise ValueError(f"{question}: predictions missing code columns: {absent}")

    reference_df = reference_df.set_index("UserID")
    input_df = input_df.set_index("UserID")
    predictions = predictions.set_index("UserID")
    common_ids = input_df.index.intersection(reference_df.index).intersection(predictions.index)
    comments = input_df.loc[common_ids, "Comment"].fillna("").astype(str).str.strip()
    eligible = comments.ne("") & ~comments.str.upper().isin({"D/A", "N/A"})
    ids = common_ids[eligible.to_numpy()]

    human = reference_df.loc[ids, code_columns].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    model = predictions.loc[ids, pred_columns].apply(pd.to_numeric, errors="coerce")
    model.columns = code_columns
    invalid = ~model.isin((0, 1))
    if invalid.any().any():
        raise ValueError(f"{question}: baseline predictions contain missing/nonbinary eligible cells")
    model = model.astype(int)
    differs = human.ne(model)
    mismatch_ids = differs.any(axis=1)
    baseline_kappa = cohen_kappa_score(human.to_numpy().ravel(), model.to_numpy().ravel())
    baseline_cell_agreement = float((~differs).to_numpy().mean())

    target = output_root / question
    target.mkdir(parents=True, exist_ok=True)
    challenge_ids = human.index[mismatch_ids]
    challenge_input = input_df.loc[challenge_ids, ["Comment"]].copy()
    challenge_input.insert(0, "UserID", challenge_ids)
    challenge_reference = reference_df.loc[challenge_ids, ["Comment", *code_columns]].copy()
    challenge_reference.insert(0, "UserID", challenge_ids)
    challenge_reference[code_columns] = challenge_reference[code_columns].fillna(0).astype(int)
    challenge_diagnostics = challenge_input.copy()
    for code in code_columns:
        challenge_diagnostics[f"human::{code}"] = human.loc[challenge_ids, code].to_numpy()
        challenge_diagnostics[f"qwen::{code}"] = model.loc[challenge_ids, code].to_numpy()
        challenge_diagnostics[f"mismatch::{code}"] = differs.loc[challenge_ids, code].to_numpy()
    challenge_input.to_csv(target / "input.csv", index=False)
    challenge_reference.to_csv(target / "reference.csv", index=False)
    challenge_diagnostics.to_csv(target / "disagreements.csv", index=False)

    meta = {
        "question": question,
        "baseline_model": "Qwen/Qwen2.5-Coder-32B-Instruct (vLLM)",
        "source_predictions": [
            str(ROOT / "auto_research" / question / "candidate_001" / "train_coded.csv"),
            str(ROOT / "auto_research" / question / "final_validation" / "holdout_coded.csv"),
        ],
        "selection": "Eligible comments with at least one binary code disagreement against human labels.",
        "eligible_comments": int(len(ids)),
        "challenge_comments": int(mismatch_ids.sum()),
        "reference_codes": len(code_columns),
        "mismatched_cells": int(differs.to_numpy().sum()),
        "eligible_cells": int(differs.size),
        "baseline_challenge_kappa": float(baseline_kappa),
        "baseline_challenge_cell_agreement": baseline_cell_agreement,
        "use": "Intentionally error-enriched diagnostic set; scores are not unbiased generalization estimates.",
    }
    (target / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="outputs/staff_pulse/disagreement_challenge_qwen25coder32b")
    args = parser.parse_args()
    output_root = Path(args.output_dir)
    results = [make_challenge(question, output_root) for question in ("question_1", "question_2")]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
