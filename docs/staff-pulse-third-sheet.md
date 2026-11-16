# Run Survey Coder on the staff-pulse source sheet

The source workbook’s third worksheet is **`Staff Pulse 2025 -sample data`**. It contains `UserID` plus 12 comment columns. The existing staff-pulse config should map those columns to their corresponding codebooks.

## Export that worksheet

`apply-codebooks` currently reads Excel files with `pandas.read_excel()` and has no worksheet selector; it therefore reads the **first** worksheet by default. Export the third sheet to CSV first:

```bash
uv run python -c 'import pandas as pd; pd.read_excel("Staff Pulse 2025 - sample for comment analysis SR copy.xlsx", sheet_name="Staff Pulse 2025 -sample data").to_csv("outputs/staff_pulse/source_data.csv", index=False)'
```

## Apply the codebooks

Check that `outputs/staff_pulse/config.json` points each of the 12 comment columns to its intended codebook, then run:

```bash
uv run apply-codebooks \
  --input outputs/staff_pulse/source_data.csv \
  --config outputs/staff_pulse/config.json \
  --output outputs/staff_pulse/source_coded.csv \
  --id-column UserID \
  --batch-size 25
```

The provider and model are read from the config’s `llm` section. The output CSV retains the input columns and adds one-hot code columns and a `*_Codes_Applied` column per stream. This processes all rows; use `--max-rows 400` to limit it to the first 400 rows for a trial.

## Is additional code required?

**Not to run this workflow:** exporting the desired worksheet to CSV is enough, and existing codebooks/config can be used. **To pass the workbook directly to the CLI and select a worksheet, yes:** `load_data()` and the CLI would need a worksheet-name option. That change is unnecessary for the CSV workflow above.
