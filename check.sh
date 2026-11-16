#!/bin/sh
set -eu
python3 - <<'PY'
import json
import math
from pathlib import Path

scores = []
passed = True
for question in ("question_1", "question_2"):
    root = Path("outputs/staff_pulse/auto_research") / question
    files = list(root.rglob("final_result.json")) if root.exists() else []
    valid = []
    for path in files:
        try:
            result = json.loads(path.read_text())
            raw_value = result["kappa"]
            evaluated = result["evaluated_cells"]
            missing = result["missing_predictions"]
            if (type(raw_value) not in (int, float)
                    or type(evaluated) is not int or type(missing) is not int
                    or evaluated < 0 or missing < 0 or evaluated + missing == 0):
                continue
            value = float(raw_value)
            coverage = evaluated / (evaluated + missing)
            if (result.get("success") is True and math.isfinite(value)
                    and -1 <= value <= 1 and coverage >= 0.8):
                valid.append(value)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    if not valid or max(valid) < 0.60:
        passed = False
    scores.extend(valid)
score = max(0, min(100, round(max(scores, default=0.0) * 100)))
print(f"SCORE: {score}")
sys_exit = 0 if passed else 1
raise SystemExit(sys_exit)
PY
