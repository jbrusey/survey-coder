"""Small cross-platform Tk GUI for running the survey coder in-process."""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from .apply_codebooks import process_stream as apply_stream
from .config import load_prompt
from .generate_codebooks import (
    ensure_id_column,
    extract_stream,
    process_stream as generate_stream,
    sample_comments,
    validate_columns,
)


def build_streams(columns: list[str], question: str) -> list[dict[str, str]]:
    return [
        {"name": column, "column": column, "question": question or f"What does this response say?", "prefix": column}
        for column in columns
    ]


def run_pipeline(settings: dict, operation: str) -> str:
    input_path = settings["input"]
    sheet = settings["sheet"]
    df = pd.read_csv(input_path) if input_path.lower().endswith(".csv") else pd.read_excel(input_path, sheet_name=sheet)
    streams = build_streams(settings["columns"], settings["question"])
    validate_columns(df, streams)
    output_dir = Path(settings["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    df, id_col = ensure_id_column(df, None)
    common = dict(model=settings["model"], provider=settings["provider"], openai_base_url=settings["base_url"],
                  temperature=settings["temperature"], batch_size=settings["batch_size"])

    if operation in ("generate", "both"):
        args = SimpleNamespace(output_dir=str(output_dir), **common, max_themes_per_batch=20, max_final_codes=30)
        prompts = {name: load_prompt(settings["prompt_dir"], name) for name in
                   ("discover_system.txt", "discover_batch.txt", "consolidate_system.txt", "consolidate.txt")}
        prompts = {"system": prompts["discover_system.txt"], "batch": prompts["discover_batch.txt"], **prompts}
        for stream in streams:
            stream_df = extract_stream(df, stream["name"], stream["column"], id_col, settings["min_length"])
            generate_stream(stream, sample_comments(stream_df, settings["sample_size"], 42), args, id_col, prompts, settings["context"])

    if operation in ("apply", "both"):
        args = SimpleNamespace(**common, max_rows=None)
        prompts = {"system": load_prompt(settings["prompt_dir"], "code_system.txt"),
                   "batch": load_prompt(settings["prompt_dir"], "code_batch.txt")}
        results = []
        for stream in streams:
            stream = dict(stream, codebook=str(output_dir / f"{stream['name'].lower()}_codebook.md"))
            result, codes = apply_stream(df, stream, id_col, args, settings["context"], prompts)
            results.append((stream, result, codes))
        for stream, result, codes in results:
            prefix = stream["prefix"]
            for code in codes:
                df[f"{prefix}_{code}"] = df[id_col].apply(lambda value, code=code: int(code in result.get(str(value), [])))
            df[f"{prefix}_Codes_Applied"] = df[id_col].apply(lambda value: ", ".join(result.get(str(value), [])))
        output = output_dir / "final_coded_results.csv"
        df.to_csv(output, index=False)
        return f"Saved {output}"
    return f"Saved codebooks in {output_dir}"


def main() -> None:
    # Some relocatable Python builds retain a build-time Tcl path (notably uv
    # on macOS). Prefer the Tcl/Tk libraries shipped with that interpreter.
    tcl_roots = [Path(sys.base_prefix) / "lib", Path(sys.base_prefix) / "tcl"]
    for root in tcl_roots:
        tcl = next(root.glob("tcl*/init.tcl"), None) if root.exists() else None
        tk = next(root.glob("tk*/tk.tcl"), None) if root.exists() else None
        if tcl and "TCL_LIBRARY" not in os.environ:
            os.environ["TCL_LIBRARY"] = str(tcl.parent)
        if tk and "TK_LIBRARY" not in os.environ:
            os.environ["TK_LIBRARY"] = str(tk.parent)
        if tcl and tk:
            break

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Survey Coder")
    root.minsize(720, 560)
    values = {"input": "", "sheet": "", "columns": [], "question": "", "context": "student survey feedback",
              "model": "gpt-4o-mini", "provider": "openai", "base_url": "", "output_dir": "outputs",
              "prompt_dir": "prompts", "batch_size": 5, "sample_size": 1000, "min_length": 5, "temperature": 0.0}

    def browse_input():
        path = filedialog.askopenfilename(filetypes=[("Survey files", "*.xlsx *.xls *.csv"), ("All files", "*.*")])
        if not path:
            return
        values["input"] = path
        input_var.set(path)
        try:
            if path.lower().endswith(".csv"):
                sheets = ["CSV"]
                columns = list(pd.read_csv(path, nrows=0).columns)
            else:
                book = pd.ExcelFile(path)
                sheets = book.sheet_names
                columns = list(pd.read_excel(path, sheet_name=sheets[0], nrows=0).columns)
            sheet_box["values"] = sheets
            sheet_var.set(sheets[0])
            column_box.delete(0, tk.END)
            for column in columns:
                column_box.insert(tk.END, column)
        except Exception as exc:
            messagebox.showerror("Could not open file", str(exc))

    def refresh_columns(*_):
        if not values["input"] or values["input"].lower().endswith(".csv"):
            return
        try:
            columns = list(pd.read_excel(values["input"], sheet_name=sheet_var.get(), nrows=0).columns)
            column_box.delete(0, tk.END)
            for column in columns:
                column_box.insert(tk.END, column)
        except Exception as exc:
            messagebox.showerror("Could not read worksheet", str(exc))

    def settings_from_ui():
        selected = [column_box.get(i) for i in column_box.curselection()]
        if not input_var.get() or not selected:
            raise ValueError("Choose an input file and at least one column.")
        values.update(input=input_var.get(), sheet=sheet_var.get(), columns=selected, question=question_var.get(),
                      context=context_var.get(), model=model_var.get(), provider=provider_var.get(), base_url=base_var.get(),
                      output_dir=output_var.get(), prompt_dir=prompt_var.get(), batch_size=int(batch_var.get()),
                      sample_size=int(sample_var.get()), min_length=int(min_var.get()), temperature=float(temp_var.get()))
        return values.copy()

    def start(operation):
        try:
            settings = settings_from_ui()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid settings", str(exc)); return
        status.set(f"Running {operation}...")
        for button in buttons: button.configure(state="disabled")
        def work():
            try: result = run_pipeline(settings, operation)
            except Exception as exc: result = f"Error: {exc}\n{traceback.format_exc()}"
            root.after(0, lambda: (status.set(result), [b.configure(state="normal") for b in buttons]))
        threading.Thread(target=work, daemon=True).start()

    def save_config():
        try: settings = settings_from_ui()
        except (ValueError, TypeError) as exc: messagebox.showerror("Invalid settings", str(exc)); return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            config = {"context": settings["context"], "streams": build_streams(settings["columns"], settings["question"])}
            Path(path).write_text(json.dumps(config, indent=2))
            status.set(f"Saved configuration to {path}")

    frame = ttk.Frame(root, padding=12); frame.pack(fill="both", expand=True)
    input_var, sheet_var = tk.StringVar(), tk.StringVar(); question_var = tk.StringVar(); context_var = tk.StringVar(value=values["context"])
    model_var = tk.StringVar(value=values["model"]); provider_var = tk.StringVar(value=values["provider"]); base_var = tk.StringVar()
    output_var = tk.StringVar(value=values["output_dir"]); prompt_var = tk.StringVar(value=values["prompt_dir"])
    batch_var = tk.StringVar(value="5"); sample_var = tk.StringVar(value="1000"); min_var = tk.StringVar(value="5"); temp_var = tk.StringVar(value="0.0"); status = tk.StringVar(value="Ready")
    def row(label, variable, r): ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", pady=3); ttk.Entry(frame, textvariable=variable, width=68).grid(row=r, column=1, columnspan=2, sticky="ew")
    row("Input file", input_var, 0); ttk.Button(frame, text="Browse", command=browse_input).grid(row=0, column=3)
    ttk.Label(frame, text="Worksheet").grid(row=1, column=0, sticky="w"); sheet_box = ttk.Combobox(frame, textvariable=sheet_var, state="readonly"); sheet_box.grid(row=1, column=1, sticky="ew"); sheet_box.bind("<<ComboboxSelected>>", refresh_columns)
    ttk.Label(frame, text="Columns to analyse").grid(row=2, column=0, sticky="nw"); column_box = tk.Listbox(frame, selectmode="extended", height=7, exportselection=False); column_box.grid(row=2, column=1, columnspan=2, sticky="nsew")
    row("Question", question_var, 3); row("Context", context_var, 4); row("Model", model_var, 5)
    ttk.Label(frame, text="Provider").grid(row=6, column=0, sticky="w"); ttk.Combobox(frame, textvariable=provider_var, values=("openai", "google"), state="readonly").grid(row=6, column=1, sticky="ew"); row("OpenAI-compatible URL", base_var, 7)
    row("Output directory", output_var, 8); row("Prompt directory", prompt_var, 9)
    row("Batch size", batch_var, 10); row("Sample size", sample_var, 11); row("Minimum text length", min_var, 12); row("Temperature", temp_var, 13)
    buttons = [ttk.Button(frame, text="Generate codebooks", command=lambda: start("generate")), ttk.Button(frame, text="Apply codebooks", command=lambda: start("apply")), ttk.Button(frame, text="Run both", command=lambda: start("both")), ttk.Button(frame, text="Save config", command=save_config)]
    for i, button in enumerate(buttons): button.grid(row=14, column=i if i < 3 else 3, pady=12, padx=3)
    ttk.Label(frame, textvariable=status, wraplength=680).grid(row=15, column=0, columnspan=4, sticky="w"); frame.columnconfigure(1, weight=1); frame.rowconfigure(2, weight=1)
    root.mainloop()


if __name__ == "__main__":
    main()
