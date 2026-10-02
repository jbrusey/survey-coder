import pandas as pd

from .evaluate_results import evaluate_predictions, parse_args


def main():
    args = parse_args()
    df = pd.read_csv(args.input)

    mappings = {
        "LLM_A": "Imp_CONTENT_DEPTH_FOUNDATION",
    }
    predictions = []
    for concept, true_col, llm_col in (
        ("Concept A (Underloaded)", "Hand coded - A", "LLM_A"),
    ):
        source = mappings[llm_col]
        if source not in df:
            print(f"Warning: Expected LLM column '{source}' not found.")
        prediction = df.get(source, pd.Series(0, index=df.index)).fillna(0).astype(int)
        predictions.append((concept, true_col, prediction))

    if "Imp_MODULE_PACING_TIMELINE" in df or "Imp_TEACHING_MODEL_STRUCTURE" in df:
        prediction = (
            df.get("Imp_MODULE_PACING_TIMELINE", pd.Series(0, index=df.index)).fillna(0).eq(1)
            | df.get("Imp_TEACHING_MODEL_STRUCTURE", pd.Series(0, index=df.index)).fillna(0).eq(1)
        ).astype(int)
    else:
        print("Warning: Expected pacing or teaching-structure LLM column not found.")
        prediction = pd.Series(0, index=df.index)
    predictions.append(("Concept B (Overloaded)", "Hand coded - B", prediction))

    evaluate_predictions(df, predictions, args.output)


if __name__ == "__main__":
    main()
