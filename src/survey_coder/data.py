import pandas as pd


def load_data(path: str) -> pd.DataFrame:
    if path.endswith(".csv"):
        return pd.read_csv(path)
    if path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    raise ValueError("Unsupported file format.")
