import pandas as pd

from .evaluate_results import evaluate_predictions, parse_args


def main():
    args = parse_args()
    df = pd.read_csv(args.input)

    mappings = {
        "LLM_A": "Imp_prior_knowledge_differentiation",
        "LLM_B": "Imp_pacing_and_time_constraints",
    }
    predictions = []
    for concept, true_col, llm_col in (
        ("Concept A (Underloaded)", "Hand coded - A", "LLM_A"),
        ("Concept B (Overloaded)", "Hand coded - B", "LLM_B"),
    ):
        source = mappings[llm_col]
        if source not in df:
            print(f"Warning: Expected LLM column '{source}' not found.")
        prediction = df.get(source, pd.Series(0, index=df.index)).fillna(0).astype(int)
        predictions.append((concept, true_col, prediction))

    evaluate_predictions(df, predictions, args.output)


if __name__ == "__main__":
    main()
