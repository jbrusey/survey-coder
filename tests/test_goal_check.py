import json
import subprocess
from pathlib import Path


CHECK = Path(__file__).resolve().parents[1] / "check.sh"


def write_final_result(root, question, evaluated, missing):
    out = root / "outputs/staff_pulse/auto_research" / question
    out.mkdir(parents=True)
    (out / "final_result.json").write_text(json.dumps({
        "success": True,
        "kappa": 0.7,
        "evaluated_cells": evaluated,
        "missing_predictions": missing,
    }))


def test_goal_check_accepts_exact_kappa_and_coverage_thresholds(tmp_path):
    for question in ("question_1", "question_2"):
        write_final_result(tmp_path, question, 80, 20)
        path = tmp_path / "outputs/staff_pulse/auto_research" / question / "final_result.json"
        result = json.loads(path.read_text())
        result["kappa"] = 0.60
        path.write_text(json.dumps(result))
    checked = subprocess.run([str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 0
    assert "SCORE: 60" in checked.stdout


def test_goal_check_rejects_malformed_counts(tmp_path):
    write_final_result(tmp_path, "question_1", 100, 0)
    write_final_result(tmp_path, "question_2", 100, 0)
    path = tmp_path / "outputs/staff_pulse/auto_research/question_1/final_result.json"
    result = json.loads(path.read_text())
    result["missing_predictions"] = -1
    path.write_text(json.dumps(result))
    checked = subprocess.run([str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 1


def test_goal_check_rejects_out_of_range_kappa(tmp_path):
    write_final_result(tmp_path, "question_1", 100, 0)
    write_final_result(tmp_path, "question_2", 100, 0)
    path = tmp_path / "outputs/staff_pulse/auto_research/question_1/final_result.json"
    result = json.loads(path.read_text())
    result["kappa"] = 1.1
    path.write_text(json.dumps(result))
    checked = subprocess.run([str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 1


def test_goal_check_requires_threshold_and_coverage(tmp_path):
    write_final_result(tmp_path, "question_1", 79, 21)
    write_final_result(tmp_path, "question_2", 100, 0)
    result = subprocess.run([str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert "SCORE: 70" in result.stdout

    (tmp_path / "outputs/staff_pulse/auto_research/question_1/final_result.json").write_text(
        json.dumps({"success": True, "kappa": 0.7, "evaluated_cells": 80,
                    "missing_predictions": 20})
    )
    result = subprocess.run([str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    assert "SCORE: 70" in result.stdout
