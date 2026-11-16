"""Run repeatable staff-pulse codebook experiments against a held-out reference set.

The experiment runner delegates coding to ``apply-codebooks`` and evaluates the
result against the supplied hand-coded reference. Experiment candidates are JSON
objects in a manifest; see ``docs/auto-research-staff-pulse.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.metrics import cohen_kappa_score
from .apply_codebooks import extract_codes_from_md


MIN_COVERAGE = 0.8


def prediction_coverage(evaluated: int, missing: int) -> float:
    total = evaluated + missing
    return evaluated / total if total else 0.0


def has_minimum_coverage(evaluated: int, missing: int) -> bool:
    return prediction_coverage(evaluated, missing) >= MIN_COVERAGE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run staff-pulse coding experiments and report holdout Cohen's kappa."
    )
    parser.add_argument("--input", required=True, help="400-row CSV supplied to apply-codebooks")
    parser.add_argument("--reference", required=True, help="400-row hand-coded reference CSV")
    parser.add_argument("--config", required=True, help="Base Survey Coder JSON config")
    parser.add_argument("--candidates", required=True, help="JSON list of candidate config overrides")
    parser.add_argument("--output-dir", required=True, help="Directory for run artifacts and summary")
    parser.add_argument("--id-column", default="UserID")
    parser.add_argument("--comment-column", default="Comment")
    parser.add_argument("--train-rows", type=int, default=200)
    parser.add_argument("--target-kappa", type=float, default=0.6)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--prompt-dir", default="prompts")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--limit", type=int, help="Run only the first N candidates (for a smoke run)")
    parser.add_argument("--train-only", action="store_true",
                        help="Evaluate candidates on development rows only; do not code or score holdout rows")
    return parser.parse_args()


def read_frame(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"UserID": str})


def validate_benchmark_frames(input_df: pd.DataFrame, reference_df: pd.DataFrame,
                               id_column: str, comment_column: str,
                               allow_reference_subset: bool = False) -> None:
    for name, frame in (("input", input_df), ("reference", reference_df)):
        for column in (id_column, comment_column):
            if column not in frame:
                raise ValueError(f"{name} is missing required column {column!r}")
        raw_ids = frame[id_column]
        if raw_ids.isna().any() or raw_ids.astype(str).str.strip().eq("").any():
            raise ValueError(f"{name} contains blank IDs")
        ids = raw_ids.astype(str)
        if ids.duplicated().any():
            raise ValueError(f"{name} contains duplicate IDs")
    input_ids = input_df[id_column].astype(str)
    reference_ids = reference_df[id_column].astype(str)
    if not allow_reference_subset and set(input_ids) != set(reference_ids):
        raise ValueError("Input and reference ID sets do not match")
    if not set(reference_ids).issubset(input_ids):
        raise ValueError("Reference IDs must be present in the input")
    input_comments = input_df.assign(**{id_column: input_ids}).set_index(id_column)[comment_column]
    reference_comments = reference_df.assign(**{id_column: reference_ids}).set_index(id_column)[comment_column]
    input_comments = input_comments.fillna("").astype(str).str.strip()
    reference_comments = reference_comments.fillna("").astype(str).str.strip()
    missing_markers = {"", "D/A", "N/A"}
    input_comments = input_comments.mask(input_comments.str.upper().isin(missing_markers), "")
    reference_comments = reference_comments.mask(reference_comments.str.upper().isin(missing_markers), "")
    if not reference_comments.equals(input_comments.reindex(reference_comments.index)):
        raise ValueError("Input and reference comments do not match by ID")


def validate_reference_codes(reference_df: pd.DataFrame, id_column: str,
                             comment_column: str) -> None:
    for column in reference_df.columns:
        if column in {id_column, comment_column}:
            continue
        raw = reference_df[column]
        values = pd.to_numeric(raw, errors="coerce")
        nonblank = raw.notna() & raw.astype(str).str.strip().ne("")
        if ((~values.isin((0, 1))) & nonblank).any():
            raise ValueError(f"Reference code {column!r} must contain only 0, 1, or blank")


def eligible_ids(input_df: pd.DataFrame, reference_df: pd.DataFrame,
                 id_column: str, comment_column: str) -> set[str]:
    reference_ids = reference_df[id_column].astype(str)
    ref_comments = reference_df.assign(**{id_column: reference_ids}).set_index(id_column)[comment_column]
    joined = input_df[[id_column, comment_column]].copy()
    joined[id_column] = joined[id_column].astype(str)
    joined = joined.merge(ref_comments.rename("_reference_comment"),
                          left_on=id_column, right_index=True, how="inner")
    mask = joined[comment_column].fillna("").astype(str).str.strip().ne("")
    mask &= ~joined[comment_column].fillna("").astype(str).str.strip().str.upper().isin({"D/A", "N/A"})
    ref_mask = joined["_reference_comment"].fillna("").astype(str).str.strip().ne("")
    ref_mask &= ~joined["_reference_comment"].fillna("").astype(str).str.strip().str.upper().isin({"D/A", "N/A"})
    return set(joined.loc[mask & ref_mask, id_column])


def evaluate_detailed(coded: pd.DataFrame, reference: pd.DataFrame, streams: list[dict[str, Any]],
                      ids: set[str], id_column: str, comment_column: str = "Comment"
                      ) -> tuple[float | None, int, int, dict[str, dict[str, Any]]]:
    """Return pooled and per-code metrics; missing predictions are excluded from kappa."""
    coded = coded.copy()
    reference = reference.copy()
    validate_reference_codes(reference, id_column, comment_column)
    coded[id_column] = coded[id_column].astype(str)
    reference[id_column] = reference[id_column].astype(str)
    codes = [column for column in reference.columns if column not in {id_column, comment_column}]
    prediction_columns = [f"{stream.get('prefix', stream['name'])}_{code}"
                          for stream in streams for code in codes]
    if len(prediction_columns) != len(set(prediction_columns)):
        raise ValueError("Streams produce duplicate prediction columns")
    left = reference[reference[id_column].isin(ids)].merge(
        coded, on=id_column, how="left", suffixes=("_human", "_model"), validate="one_to_one"
    )
    human_cells: list[int] = []
    model_cells: list[int] = []
    missing = 0
    per_code = {}
    for stream in streams:
        prefix = stream.get("prefix", stream["name"])
        for column in reference.columns:
            if column in {id_column, comment_column}:
                continue
            pred_column = f"{prefix}_{column}"
            true = pd.to_numeric(left[column], errors="coerce").fillna(0).astype(int)
            if pred_column not in left.columns:
                missing += len(left)
                per_code[pred_column] = {"kappa": None, "eligible_cells": len(left),
                                         "evaluated_cells": 0, "missing_predictions": len(left),
                                         "human_positive": int(true.sum()),
                                         "human_negative": len(left) - int(true.sum()),
                                         "model_positive": 0, "model_negative": 0}
                continue
            pred = pd.to_numeric(left[pred_column], errors="coerce")
            # Human blanks mean zero only for eligible respondents.
            valid = pred.isin((0, 1))
            missing_count = int((~valid).sum())
            missing += missing_count
            code_true = true[valid].tolist()
            code_pred = pred[valid].astype(int).tolist()
            human_cells.extend(code_true)
            model_cells.extend(code_pred)
            same_labels = code_true and len(set(code_true + code_pred)) == 1
            code_kappa = cohen_kappa_score(code_true, code_pred) if code_true and not same_labels else None
            if code_kappa is not None and not math.isfinite(float(code_kappa)):
                code_kappa = None
            per_code[pred_column] = {
                "kappa": float(code_kappa) if code_kappa is not None else None,
                "eligible_cells": len(left), "evaluated_cells": len(code_true),
                "missing_predictions": missing_count,
                "human_positive": int(true.sum()), "human_negative": len(left) - int(true.sum()),
                "model_positive": int(sum(code_pred)), "model_negative": len(code_pred) - int(sum(code_pred))}
    all_labels_same = human_cells and len(set(human_cells + model_cells)) == 1
    kappa = cohen_kappa_score(human_cells, model_cells) if human_cells and not all_labels_same else None
    if kappa is not None and not math.isfinite(float(kappa)):
        kappa = None
    return (float(kappa) if kappa is not None else None, len(human_cells), missing, per_code)


def evaluate(coded: pd.DataFrame, reference: pd.DataFrame, streams: list[dict[str, Any]],
             ids: set[str], id_column: str, comment_column: str = "Comment"
             ) -> tuple[float | None, int, int]:
    return evaluate_detailed(coded, reference, streams, ids, id_column, comment_column)[:3]


def run_candidate(args: argparse.Namespace, candidate: dict[str, Any], index: int,
                  train_ids: set[str], reference: pd.DataFrame,
                  base_config: dict[str, Any]) -> dict[str, Any]:
    out = Path(args.output_dir) / f"candidate_{index:03d}"
    out.mkdir(parents=True, exist_ok=True)
    candidate_config = json.loads(json.dumps(base_config))
    candidate_config.update(candidate.get("config", {}))
    if "llm" in candidate:
        candidate_config.setdefault("llm", {}).update(candidate["llm"])
    for key in ("provider", "model", "base_url", "max_output_tokens", "temperature"):
        if getattr(args, key, None) is not None:
            candidate_config.setdefault("llm", {})[{"base_url": "base_url"}.get(key, key)] = getattr(args, key)
    if "streams" in candidate:
        candidate_config["streams"] = candidate["streams"]
    elif "codebooks" in candidate:
        for stream in candidate_config["streams"]:
            if stream["name"] in candidate["codebooks"]:
                stream["codebook"] = candidate["codebooks"][stream["name"]]
    prompt_dir = out / "prompts"
    shutil.copytree(args.prompt_dir, prompt_dir, dirs_exist_ok=True)
    for filename, content in candidate.get("prompts", {}).items():
        target = (prompt_dir / filename).resolve()
        if not target.is_relative_to(prompt_dir.resolve()):
            raise ValueError(f"Prompt override path escapes snapshot directory: {filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    prompt_path = str(prompt_dir)

    for stream_index, stream in enumerate(candidate_config["streams"]):
        if "codebook" not in stream:
            continue
        source = Path(stream["codebook"]).resolve()
        if source.suffix.lower() != ".md":
            raise ValueError(f"Codebook must be a Markdown file: {source}")
        snapshot = out / "codebooks" / f"{stream_index}_{source.name}"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, snapshot)
        codes = extract_codes_from_md(str(snapshot))
        expected = set(reference.columns) - {args.id_column, args.comment_column}
        if len(codes) != len(expected) or set(codes) != expected:
            raise ValueError(f"Codebook codes do not match reference columns: {source}")
        stream["codebook"] = str(snapshot.resolve())

    config_path = out / "config.json"
    config_path.write_text(json.dumps(candidate_config, indent=2), encoding="utf-8")
    input_df = read_frame(args.input)
    train_mask = input_df[args.id_column].astype(str).isin(train_ids)
    train_path = out / "train_input.csv"
    input_df.loc[train_mask].to_csv(train_path, index=False)
    coded_path = out / "train_coded.csv"
    cmd = [sys.executable, "-m", "survey_coder.apply_codebooks", "--input", str(train_path),
           "--config", str(config_path), "--output", str(coded_path), "--id-column", args.id_column,
           "--batch-size", str(args.batch_size), "--prompt-dir", prompt_path]
    if args.max_output_tokens:
        cmd += ["--max-output-tokens", str(args.max_output_tokens)]
    cmd += ["--temperature", str(candidate_config.get("llm", {}).get("temperature", 0.0))]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        elapsed = time.monotonic() - started
        (out / "stdout.log").write_text(proc.stdout, encoding="utf-8")
        (out / "stderr.log").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0 or not coded_path.exists():
            return {"candidate": index, "success": False, "seconds": round(elapsed, 2), "kappa": None,
                    "reason": "coding process failed"}
        coded = read_frame(str(coded_path))
        streams = candidate_config["streams"]
        train_kappa, train_cells, train_missing, per_code = evaluate_detailed(
            coded, reference, streams, train_ids, args.id_column, args.comment_column)
        # A candidate is selected using training rows only. Holdout evaluation is
        # deliberately run only once, below, after selection.
        result = {"candidate": index, "success": train_kappa is not None and train_kappa >= args.target_kappa
                  and has_minimum_coverage(train_cells, train_missing),
                  "seconds": round(elapsed, 2), "kappa": train_kappa,
                  "evaluated_cells": train_cells, "missing_predictions": train_missing,
                  "prediction_coverage": round(prediction_coverage(train_cells, train_missing), 4),
                  "per_code": per_code, "target_kappa": args.target_kappa, "config": str(config_path),
                  "coded_output": str(coded_path)}
        (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        elapsed = time.monotonic() - started
        return {"candidate": index, "success": False, "seconds": round(elapsed, 2), "kappa": None,
                "reason": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    args = parse_args()
    input_df, reference = read_frame(args.input), read_frame(args.reference)
    validate_benchmark_frames(input_df, reference, args.id_column, args.comment_column,
                              allow_reference_subset=args.train_only)
    validate_reference_codes(reference, args.id_column, args.comment_column)
    base_config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Candidate manifest must be a non-empty JSON list")
    if any(not isinstance(candidate, dict) for candidate in candidates):
        raise ValueError("Each candidate must be a JSON object")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be a positive integer")
        candidates = candidates[:args.limit]
    all_ids = input_df[args.id_column].astype(str).tolist()
    if not 0 < args.train_rows < len(all_ids):
        raise ValueError(f"--train-rows must be between 1 and {len(all_ids) - 1}")
    train_ids = set(all_ids[:args.train_rows])
    holdout_ids = set(all_ids[args.train_rows:])
    reference_ids = set(reference[args.id_column].astype(str))
    if args.train_only and not reference_ids.issubset(train_ids):
        raise ValueError("--train-only reference must contain development IDs only")
    valid = eligible_ids(input_df, reference, args.id_column, args.comment_column)
    train_ids &= valid
    holdout_ids &= valid
    if not train_ids or (not args.train_only and not holdout_ids):
        raise ValueError("Required development/holdout partitions need eligible labeled rows")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {out}")
    (out / "candidates.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
    summary_path = out / "summary.jsonl"
    summary_path.write_text("", encoding="utf-8")
    results = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            result = run_candidate(args, candidate, index, train_ids, reference, base_config)
        except Exception as exc:
            result = {"candidate": index, "success": False, "seconds": 0, "kappa": None,
                      "reason": f"{type(exc).__name__}: {exc}"}
        candidate_dir = out / f"candidate_{index:03d}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append(result)
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({k: result.get(k) for k in (
                "candidate", "success", "seconds", "kappa", "evaluated_cells",
                "missing_predictions", "prediction_coverage", "reason")}) + "\n")
        print(json.dumps({k: result.get(k) for k in (
            "candidate", "success", "seconds", "kappa", "evaluated_cells",
            "missing_predictions", "prediction_coverage", "reason")}), flush=True)
    viable = [result for result in results if result.get("kappa") is not None
              and has_minimum_coverage(result["evaluated_cells"], result["missing_predictions"])]
    if not viable:
        print(json.dumps({"success": False, "seconds": sum(x["seconds"] for x in results), "kappa": None}))
        return
    best = max(viable, key=lambda item: item["kappa"])
    if args.train_only:
        print(json.dumps({"success": best["kappa"] >= args.target_kappa
                          and has_minimum_coverage(best["evaluated_cells"], best["missing_predictions"]),
                          "seconds": sum(x["seconds"] for x in results),
                          "development_best_kappa": best["kappa"],
                          "development_candidate": best["candidate"],
                          "evaluated_cells": best["evaluated_cells"],
                          "missing_predictions": best["missing_predictions"],
                          "prediction_coverage": round(prediction_coverage(
                              best["evaluated_cells"], best["missing_predictions"]), 4)}), flush=True)
        return
    # Single final validation on rows 201-400 after development candidate choice.
    # Recode only the holdout with the selected config and prompt snapshot.
    final_dir = out / "final_validation"
    final_dir.mkdir(exist_ok=True)
    input_holdout = input_df[input_df[args.id_column].astype(str).isin(holdout_ids)]
    holdout_input_path = final_dir / "holdout_input.csv"
    input_holdout.to_csv(holdout_input_path, index=False)
    coded_holdout_path = final_dir / "holdout_coded.csv"
    best_config = Path(best["config"])
    selected_prompt = str(Path(args.output_dir) / f"candidate_{best['candidate']:03d}" / "prompts")
    cmd = [sys.executable, "-m", "survey_coder.apply_codebooks", "--input", str(holdout_input_path),
           "--config", str(best_config), "--output", str(coded_holdout_path), "--id-column", args.id_column,
           "--batch-size", str(args.batch_size), "--prompt-dir", selected_prompt]
    if args.max_output_tokens:
        cmd += ["--max-output-tokens", str(args.max_output_tokens)]
    cmd += ["--temperature", str(json.loads(best_config.read_text()).get("llm", {}).get("temperature", 0.0))]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.monotonic() - started
    (final_dir / "stdout.log").write_text(proc.stdout, encoding="utf-8")
    (final_dir / "stderr.log").write_text(proc.stderr, encoding="utf-8")
    final_kappa = None
    if proc.returncode == 0 and coded_holdout_path.exists():
        final_kappa, cells, missing, per_code = evaluate_detailed(
            read_frame(str(coded_holdout_path)), reference,
            json.loads(best_config.read_text())["streams"], holdout_ids,
            args.id_column, args.comment_column)
    else:
        cells, missing = 0, len(holdout_ids)
    outcome = {"success": final_kappa is not None and final_kappa >= args.target_kappa
               and has_minimum_coverage(cells, missing),
               "seconds": round(sum(x["seconds"] for x in results) + elapsed, 2),
               "kappa": final_kappa, "target_kappa": args.target_kappa,
               "development_best_kappa": best["kappa"], "development_candidate": best["candidate"],
               "holdout_rows": len(holdout_ids), "evaluated_cells": cells,
               "missing_predictions": missing,
               "prediction_coverage": round(prediction_coverage(cells, missing), 4),
               "per_code": per_code if proc.returncode == 0 and coded_holdout_path.exists() else {}}
    (out / "final_result.json").write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    print(json.dumps({k: outcome[k] for k in ("success", "seconds", "kappa")}), flush=True)


if __name__ == "__main__":
    main()
