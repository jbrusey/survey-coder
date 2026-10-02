import pandas as pd
import numpy as np
from sklearn.metrics import confusion_matrix, accuracy_score, precision_score, recall_score, f1_score, cohen_kappa_score
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate LLM coding against hand-coded labels.")
    parser.add_argument("--input", required=True, help="Path to the coded CSV file")
    parser.add_argument("--output", help="Path to save the evaluation report (text)")
    return parser.parse_args()

def main():
    args = parse_args()
    
    try:
        df = pd.read_csv(args.input)
    except Exception as e:
        print(f"Error loading CSV: {e}")
        sys.exit(1)
    
    # Preprocessing: Fill NaN with 0 in hand-coded columns
    df['Hand coded - A'] = df['Hand coded - A'].fillna(0).astype(int)
    df['Hand coded - B'] = df['Hand coded - B'].fillna(0).astype(int)
    
    # Map LLM results to Concept A (Underloaded) and B (Overloaded)
    # Note: We combine Positive and Improvement streams as the hand-coder likely looked at both.
    if 'Pos_Module Underloaded' in df.columns and 'Imp_Module Underloaded' in df.columns:
        df['LLM_A'] = ((df['Pos_Module Underloaded'] == 1) | (df['Imp_Module Underloaded'] == 1)).astype(int)
    else:
        # Fallback if names are slightly different or missing
        print("Warning: Expected LLM columns for 'Underloaded' not found. Check column names.")
        df['LLM_A'] = 0

    if 'Pos_Module Overloaded' in df.columns and 'Imp_Module Overloaded' in df.columns:
        df['LLM_B'] = ((df['Pos_Module Overloaded'] == 1) | (df['Imp_Module Overloaded'] == 1)).astype(int)
    else:
        print("Warning: Expected LLM columns for 'Overloaded' not found. Check column names.")
        df['LLM_B'] = 0

    results = []
    
    for concept, true_col, pred_col in [("Concept A (Underloaded)", "Hand coded - A", "LLM_A"), 
                                        ("Concept B (Overloaded)", "Hand coded - B", "LLM_B")]:
        y_true = df[true_col]
        y_pred = df[pred_col]
        
        cm = confusion_matrix(y_true, y_pred)
        # Ensure cm is 2x2 even if some labels are missing
        if cm.shape == (1, 1):
            # If all are 0 or all are 1
            label = y_true.iloc[0]
            new_cm = np.zeros((2,2), dtype=int)
            new_cm[label, label] = cm[0,0]
            cm = new_cm
            
        tn, fp, fn, tp = cm.ravel()
        
        acc = accuracy_score(y_true, y_pred)
        prec = precision_score(y_true, y_pred, zero_division=0)
        rec = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        kappa = cohen_kappa_score(y_true, y_pred)
        
        res_str = f"""
=== Evaluation for {concept} ===
Confusion Matrix:
          Predicted 0    Predicted 1
Actual 0      {tn:<10}     {fp:<10}
Actual 1      {fn:<10}     {tp:<10}

Metrics:
Accuracy:  {acc:.4f}
Precision: {prec:.4f}
Recall:    {rec:.4f}
F1-Score:  {f1:.4f}
Cohen's Kappa (Agreement): {kappa:.4f}
"""
        results.append(res_str)
        print(res_str)

    if args.output:
        with open(args.output, 'w') as f:
            f.writelines(results)
        print(f"Report saved to {args.output}")

if __name__ == "__main__":
    main()
