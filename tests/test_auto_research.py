import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from sklearn.metrics import cohen_kappa_score

from survey_coder.auto_research import (eligible_ids, evaluate, evaluate_detailed, has_minimum_coverage,
                                        main, parse_args,
                                        prediction_coverage, run_candidate,
                                        validate_benchmark_frames)


def test_validate_benchmark_frames_checks_columns_and_ids():
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    validate_benchmark_frames(frame, frame.copy(), "UserID", "Comment")
    validate_benchmark_frames(frame, frame.iloc[::-1], "UserID", "Comment")
    with pytest.raises(ValueError, match="blank IDs"):
        validate_benchmark_frames(
            pd.DataFrame({"UserID": ["1", None], "Comment": ["a", "b"]}),
            frame, "UserID", "Comment")
    with pytest.raises(ValueError, match="duplicate IDs"):
        validate_benchmark_frames(
            pd.DataFrame({"UserID": ["1", "1"], "Comment": ["a", "b"]}),
            frame, "UserID", "Comment")
    with pytest.raises(ValueError, match="ID sets do not match"):
        validate_benchmark_frames(
            frame, pd.DataFrame({"UserID": ["1", "3"], "Comment": ["a", "b"]}),
            "UserID", "Comment")
    with pytest.raises(ValueError, match="missing required column 'Comment'"):
        validate_benchmark_frames(frame.drop(columns="Comment"), frame, "UserID", "Comment")
    missing_marker = frame.copy()
    missing_marker.loc[0, "Comment"] = "D/A"
    blank = frame.copy()
    blank.loc[0, "Comment"] = ""
    validate_benchmark_frames(missing_marker, blank, "UserID", "Comment")
    mismatched = frame.copy()
    mismatched.loc[0, "Comment"] = "different response"
    with pytest.raises(ValueError, match="comments do not match by ID"):
        validate_benchmark_frames(frame, mismatched, "UserID", "Comment")


def test_eligible_ids_normalizes_custom_numeric_id_columns():
    frame = pd.DataFrame({"Row": [10, 20], "Response": ["a", "b"]})
    assert eligible_ids(frame, frame.copy(), "Row", "Response") == {"10", "20"}


def test_evaluate_pools_codes_and_excludes_missing_predictions():
    reference = pd.DataFrame({
        "UserID": ["1", "2"], "Comment": ["a", "b"],
        "A": [1, None], "B": [None, 1],
    })
    coded = pd.DataFrame({
        "UserID": ["1", "2"], "stream_A": [1, 0], "stream_B": [0, None],
    })

    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID"
    )

    assert cells == 3
    assert missing == 1
    assert kappa == cohen_kappa_score([1, 0, 0], [1, 0, 0])


def test_evaluate_detailed_reports_per_code_support_and_missing_predictions():
    reference = pd.DataFrame({"UserID": ["1", "2", "3"], "Comment": list("abc"),
                             "A": [1, None, 0]})
    coded = pd.DataFrame({"UserID": ["1", "2", "3"], "stream_A": [1, 2, 0]})
    kappa, cells, missing, per_code = evaluate_detailed(
        coded, reference, [{"name": "stream"}], {"1", "2", "3"}, "UserID")
    assert (kappa, cells, missing) == (1.0, 2, 1)
    assert per_code["stream_A"] == {"kappa": 1.0, "eligible_cells": 3,
                                    "evaluated_cells": 2, "missing_predictions": 1,
                                    "human_positive": 1, "human_negative": 2,
                                    "model_positive": 1, "model_negative": 1}


def test_evaluate_honors_custom_comment_column():
    reference = pd.DataFrame({"UserID": ["1", "2"], "Response": ["a", "b"], "A": [1, 0]})
    coded = pd.DataFrame({"UserID": ["1", "2"], "stream_A": [1, 0]})
    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID", "Response")
    assert kappa == 1.0
    assert cells == 2
    assert missing == 0


def test_evaluate_joins_predictions_by_id_not_row_order():
    reference = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"], "A": [1, 0]})
    coded = pd.DataFrame({"UserID": ["2", "1"], "stream_A": [0, 1]})
    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID")
    assert kappa == 1.0
    assert cells == 2
    assert missing == 0


def test_evaluate_rejects_duplicate_stream_prediction_columns():
    reference = pd.DataFrame({"UserID": ["1"], "Comment": ["a"], "A": [1]})
    coded = pd.DataFrame({"UserID": ["1"], "same_A": [1]})
    with pytest.raises(ValueError, match="duplicate prediction columns"):
        evaluate(coded, reference, [{"name": "same"}, {"name": "same"}], {"1"}, "UserID")


def test_evaluate_rejects_nonbinary_reference_labels():
    reference = pd.DataFrame({"UserID": ["1"], "Comment": ["a"], "A": ["yes"]})
    coded = pd.DataFrame({"UserID": ["1"], "stream_A": [1]})

    with pytest.raises(ValueError, match="0, 1, or blank"):
        evaluate(coded, reference, [{"name": "stream"}], {"1"}, "UserID")


def test_evaluate_constant_disagreement_has_zero_kappa():
    reference = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"], "A": [0, 0]})
    coded = pd.DataFrame({"UserID": ["1", "2"], "stream_A": [1, 1]})
    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID")
    assert kappa == 0.0
    assert cells == 2
    assert missing == 0
    _, _, _, per_code = evaluate_detailed(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID")
    assert per_code["stream_A"]["kappa"] == 0.0
    assert (per_code["stream_A"]["human_negative"],
            per_code["stream_A"]["model_positive"]) == (2, 2)


def test_evaluate_counts_nonbinary_predictions_as_missing():
    reference = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"], "A": [1, 0]})
    coded = pd.DataFrame({"UserID": ["1", "2"], "stream_A": [1, 2]})

    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID"
    )

    assert kappa is None  # One valid comparison cannot define chance agreement.
    assert cells == 1
    assert missing == 1


def test_evaluate_counts_entirely_missing_prediction_column():
    reference = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"], "A": [1, 0]})
    coded = pd.DataFrame({"UserID": ["1", "2"]})

    kappa, cells, missing = evaluate(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID"
    )

    assert kappa is None
    assert cells == 0
    assert missing == 2
    _, _, _, per_code = evaluate_detailed(
        coded, reference, [{"name": "stream"}], {"1", "2"}, "UserID")
    assert per_code["stream_A"] == {"kappa": None, "eligible_cells": 2,
                                    "evaluated_cells": 0, "missing_predictions": 2,
                                    "human_positive": 1, "human_negative": 1,
                                    "model_positive": 0, "model_negative": 0}


def test_minimum_coverage_gate():
    assert prediction_coverage(80, 20) == 0.8
    assert has_minimum_coverage(80, 20)
    assert not has_minimum_coverage(79, 21)
    assert prediction_coverage(0, 0) == 0
    assert not has_minimum_coverage(0, 0)


def test_run_candidate_snapshots_prompts(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("original")
    input_path = tmp_path / "input.csv"
    pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]}).to_csv(input_path, index=False)
    codebook = tmp_path / "codes.md"
    codebook.write_text("## A\noriginal codebook")
    args = SimpleNamespace(output_dir=str(tmp_path / "out"), prompt_dir=str(prompts),
                           input=str(input_path), id_column="UserID", batch_size=1,
                           max_output_tokens=None, provider=None, model=None,
                           base_url=None, temperature=None, comment_column="Comment",
                           target_kappa=0.6)
    def fake_run(command, **kwargs):
        output = command[command.index("--output") + 1]
        pd.DataFrame({"UserID": ["1", "2"], "s_A": [1, 0]}).to_csv(output, index=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch("survey_coder.auto_research.subprocess.run", side_effect=fake_run) as run:
        result = run_candidate(args, {}, 1, {"1", "2"},
                               pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"], "A": [1, 0]}),
                               {"streams": [{"name": "s", "codebook": str(codebook)}]})
    assert result.get("kappa") == 1.0, result.get("reason", result)
    snapshot = tmp_path / "out/candidate_001/prompts/system.txt"
    assert snapshot.read_text() == "original"
    assert run.call_args.args[0][run.call_args.args[0].index("--prompt-dir") + 1] == str(snapshot.parent)
    effective = json.loads((snapshot.parents[1] / "config.json").read_text())
    codebook_snapshot = Path(effective["streams"][0]["codebook"])
    assert codebook_snapshot.read_text() == "## A\noriginal codebook"
    assert codebook_snapshot.parent.name == "codebooks"


def test_final_validation_uses_selected_prompt_snapshot(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("stable prompt")
    codebook = tmp_path / "codes.md"
    codebook.write_text("## A")
    frame = pd.DataFrame({"UserID": ["1", "2", "3", "4"], "Response": list("abcd")})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0, 1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s", "codebook": str(codebook)}]}))
    (tmp_path / "candidates.json").write_text('[{"llm":{"temperature":0.25}}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir", str(tmp_path / "out"),
            "--prompt-dir", str(prompts), "--train-rows", "2", "--comment-column", "Response"]
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        source = pd.read_csv(command[command.index("--input") + 1], dtype={"UserID": str})
        pd.DataFrame({"UserID": source["UserID"], "s_A": [1, 0]}).to_csv(
            command[command.index("--output") + 1], index=False)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.subprocess.run", side_effect=fake_run
    ):
        main()
    assert len(commands) == 2
    assert all(command[command.index("--temperature") + 1] == "0.25" for command in commands)
    assert commands[1][commands[1].index("--prompt-dir") + 1] == str(
        tmp_path / "out/candidate_001/prompts")
    outcome = json.loads((tmp_path / "out/final_result.json").read_text())
    assert outcome["success"] is True
    assert outcome["evaluated_cells"] == 2
    assert outcome["per_code"]["s_A"]["kappa"] == 1.0
    assert outcome["per_code"]["s_A"]["eligible_cells"] == 2


def test_run_candidate_rejects_non_markdown_codebook_before_model_call(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("safe")
    private = tmp_path / "private.csv"
    private.write_text("sensitive")
    args = SimpleNamespace(output_dir=str(tmp_path / "out"), prompt_dir=str(prompts),
                           input="unused.csv", id_column="UserID", batch_size=1,
                           max_output_tokens=None, provider=None, model=None, base_url=None,
                           temperature=None, comment_column="Comment", target_kappa=0.6)
    with patch("survey_coder.auto_research.subprocess.run") as run:
        with pytest.raises(ValueError, match="Codebook must be a Markdown file"):
            run_candidate(args, {}, 1, set(), pd.DataFrame(),
                          {"streams": [{"name": "s", "codebook": str(private)}]})
        run.assert_not_called()
    assert not (tmp_path / "out/candidate_001/codebooks/0_private.csv").exists()


def test_run_candidate_rejects_codebook_mapping_mismatch_before_model_call(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("safe")
    codebook = tmp_path / "codes.md"
    codebook.write_text("## Wrong")
    args = SimpleNamespace(output_dir=str(tmp_path / "out"), prompt_dir=str(prompts),
                           input="unused.csv", id_column="UserID", batch_size=1,
                           max_output_tokens=None, provider=None, model=None, base_url=None,
                           temperature=None, comment_column="Comment", target_kappa=0.6)
    reference = pd.DataFrame({"UserID": ["1"], "Comment": ["a"], "A": [1]})
    with patch("survey_coder.auto_research.subprocess.run") as run:
        with pytest.raises(ValueError, match="Codebook codes do not match reference columns"):
            run_candidate(args, {}, 1, {"1"}, reference,
                          {"streams": [{"name": "s", "codebook": str(codebook)}]})
        run.assert_not_called()


def test_run_candidate_rejects_prompt_path_traversal(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("safe")
    args = SimpleNamespace(output_dir=str(tmp_path / "out"), prompt_dir=str(prompts),
                           input="unused.csv", id_column="UserID", batch_size=1,
                           max_output_tokens=None, provider=None, model=None, base_url=None,
                           temperature=None, comment_column="Comment", target_kappa=0.6)
    with pytest.raises(ValueError, match="escapes snapshot directory"):
        run_candidate(args, {"prompts": {"../../escape.txt": "overwrite"}}, 1, set(),
                      pd.DataFrame(), {"streams": []})
    assert not (tmp_path / "escape.txt").exists()


def test_main_rejects_missing_comment_column_before_model_calls(tmp_path):
    pd.DataFrame({"UserID": ["1"]}).to_csv(tmp_path / "input.csv", index=False)
    pd.DataFrame({"UserID": ["1"], "Comment": ["a"], "A": [1]}).to_csv(
        tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out")]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="missing required column 'Comment'"):
            main()
        run_candidate.assert_not_called()


def test_main_rejects_invalid_reference_codes_before_model_calls(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[2, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="must contain only 0, 1, or blank"):
            main()
        run_candidate.assert_not_called()


def test_main_rejects_non_object_candidates_before_model_calls(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[null]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="Each candidate must be a JSON object"):
            main()
        run_candidate.assert_not_called()


def test_main_rejects_nonpositive_candidate_limit_before_model_calls(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1", "--limit", "0"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="--limit must be a positive integer"):
            main()
        run_candidate.assert_not_called()


def test_main_rejects_invalid_train_rows_before_model_calls(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "-1"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="--train-rows must be between"):
            main()
        run_candidate.assert_not_called()


def test_main_preserves_existing_output_artifacts(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    out = tmp_path / "out"
    out.mkdir()
    artifact = out / "summary.jsonl"
    artifact.write_text("preserve me")
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir", str(out),
            "--train-rows", "1"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(FileExistsError, match="Output directory is not empty"):
            main()
        run_candidate.assert_not_called()
    assert artifact.read_text() == "preserve me"


def test_train_only_rejects_holdout_labels_before_candidate_calls(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.assign(A=[1, 0]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1", "--train-only"]
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate"
    ) as run_candidate:
        with pytest.raises(ValueError, match="reference must contain development IDs only"):
            main()
        run_candidate.assert_not_called()


def test_candidate_setup_failure_does_not_abort_remaining_candidates(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.iloc[:1].assign(A=[1]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}, {}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1", "--train-only"]
    winner = {"candidate": 2, "success": False, "seconds": 1, "kappa": 0.5,
              "evaluated_cells": 1, "missing_predictions": 0, "prediction_coverage": 1.0}
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate", side_effect=[ValueError("bad candidate"), winner]
    ) as run_candidate:
        main()
    assert run_candidate.call_count == 2
    records = [json.loads(line) for line in (tmp_path / "out/summary.jsonl").read_text().splitlines()]
    assert records[0]["reason"] == "ValueError: bad candidate"
    failed_result = json.loads((tmp_path / "out/candidate_001/result.json").read_text())
    assert failed_result["reason"] == records[0]["reason"]
    assert records[1]["candidate"] == 2


def test_train_only_flag_skips_holdout_coding(tmp_path):
    frame = pd.DataFrame({"UserID": ["1", "2"], "Comment": ["a", "b"]})
    frame.to_csv(tmp_path / "input.csv", index=False)
    frame.iloc[:1].assign(A=[1]).to_csv(tmp_path / "reference.csv", index=False)
    (tmp_path / "config.json").write_text(json.dumps({"streams": [{"name": "s"}]}))
    (tmp_path / "candidates.json").write_text('[{}]')
    argv = ["auto-research", "--input", str(tmp_path / "input.csv"), "--reference",
            str(tmp_path / "reference.csv"), "--config", str(tmp_path / "config.json"),
            "--candidates", str(tmp_path / "candidates.json"), "--output-dir",
            str(tmp_path / "out"), "--train-rows", "1", "--train-only"]
    result = {"candidate": 1, "success": False, "seconds": 0, "kappa": 0.2,
              "evaluated_cells": 10, "missing_predictions": 0, "prediction_coverage": 1.0}
    with patch.object(sys, "argv", argv), patch(
        "survey_coder.auto_research.run_candidate", return_value=result
    ) as run_candidate:
        main()

    passed_reference = run_candidate.call_args.args[4]
    assert set(passed_reference["UserID"]) == {"1"}
    assert not (tmp_path / "out/final_validation").exists()
    assert not (tmp_path / "out/final_result.json").exists()
    assert json.loads((tmp_path / "out/candidates.json").read_text()) == [{}]
    summary = json.loads((tmp_path / "out/summary.jsonl").read_text().strip())
    assert summary["evaluated_cells"] == 10
    assert summary["missing_predictions"] == 0
    assert summary["prediction_coverage"] == 1.0
