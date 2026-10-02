import argparse
import sys

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate LLM coding against hand-coded labels.")
    parser.add_argument("--input", required=True, help="Path to the coded CSV file")
    parser.add_argument("--output", help="Path to save the evaluation report (text)")
    return parser.parse_args()


def evaluate_predictions(df: pd.DataFrame, predictions, output=None):
    for column in ("Hand coded - A", "Hand coded - B"):
        df[column] = df[column].fillna(0).astype(int)

    reports = []
    for concept, true_col, y_pred in predictions:
        y_true = df[true_col]
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
        report = f"""
=== Evaluation for {concept} ===
Confusion Matrix:
          Predicted 0    Predicted 1
Actual 0      {tn:<10}     {fp:<10}
Actual 1      {fn:<10}     {tp:<10}

Metrics:
Accuracy:  {accuracy_score(y_true, y_pred):.4f}
Precision: {precision_score(y_true, y_pred, zero_division=0):.4f}
Recall:    {recall_score(y_true, y_pred, zero_division=0):.4f}
F1-Score:  {f1_score(y_true, y_pred, zero_division=0):.4f}
Cohen's Kappa (Agreement): {cohen_kappa_score(y_true, y_pred):.4f}
"""
        reports.append(report)
        print(report)

    if output:
        with open(output, "w") as file:
            file.writelines(reports)
        print(f"Report saved to {output}")


def main():
    args = parse_args()
    try:
        df = pd.read_csv(args.input)
    except Exception as exc:
        print(f"Error loading CSV: {exc}")
        sys.exit(1)

    predictions = []
    for concept, true_col, llm_col in (
        ("Concept A (Underloaded)", "Hand coded - A", "LLM_A"),
        ("Concept B (Overloaded)", "Hand coded - B", "LLM_B"),
    ):
        predictions.append((concept, true_col, df.get(llm_col, pd.Series(0, index=df.index))))
    evaluate_predictions(df, predictions, args.output)


if __name__ == "__main__":
    main()
