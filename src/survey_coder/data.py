import pandas as pd


def load_data(path: str, id_column: str | None = None) -> pd.DataFrame:
    dtype = {id_column: str} if id_column else None
    if path.endswith(".csv"):
        return pd.read_csv(path, dtype=dtype)
    if path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=dtype)
    raise ValueError("Unsupported file format.")
