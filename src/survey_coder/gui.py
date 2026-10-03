"""Guided Tk GUI for configuring and running survey coding."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import tkinter as tk

import pandas as pd

from .apply_codebooks import process_stream as apply_stream
from .config import load_prompt
from .generate_codebooks import (
    ensure_id_column,
    extract_stream,
    logger as generation_logger,
    process_stream as generate_stream,
    sample_comments,
    validate_columns,
)


def build_streams(columns: list[str], question: str) -> list[dict[str, str]]:
    """Build the legacy one-question-per-column stream configuration."""
    return [
        {"name": column, "column": column,
         "question": question or "What does this response say?", "prefix": column}
        for column in columns
    ]


def run_pipeline(settings: dict, operation: str) -> str:
    """Run one or both pipeline stages and return a concise completion message."""
    input_path = settings["input"]
    sheet = settings["sheet"]
    df = (pd.read_csv(input_path) if input_path.lower().endswith(".csv")
          else pd.read_excel(input_path, sheet_name=sheet))
    streams = settings["streams"]
    validate_columns(df, streams)
    output_dir = Path(settings["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    df, id_col = ensure_id_column(df, None)
    common = dict(
        model=settings["model"], provider=settings["provider"],
        openai_base_url=settings["base_url"], temperature=settings["temperature"],
        batch_size=settings["batch_size"], max_output_tokens=settings["max_output_tokens"],
    )

    if operation in ("generate", "both"):
        args = SimpleNamespace(output_dir=str(output_dir), **common,
                               max_themes_per_batch=20, max_final_codes=30)
        prompts = {
            "system": load_prompt(settings["prompt_dir"], "discover_system.txt"),
            "batch": load_prompt(settings["prompt_dir"], "discover_batch.txt"),
            "consolidate_system": load_prompt(settings["prompt_dir"], "consolidate_system.txt"),
            "consolidate": load_prompt(settings["prompt_dir"], "consolidate.txt"),
        }
        generated = []
        failed = []
        stream_details = []
        for stream in streams:
            warnings = []

            class StreamLogHandler(logging.Handler):
                def emit(self, record):
                    if record.levelno >= logging.WARNING:
                        warnings.append(record.getMessage())

            log_handler = StreamLogHandler()
            generation_logger.addHandler(log_handler)
            try:
                stream_df = extract_stream(df, stream["name"], stream["column"],
                                           id_col, settings["min_length"])
                result = generate_stream(stream, sample_comments(
                    stream_df, settings["sample_size"], 42), args, id_col,
                    prompts, settings["context"])
                if result is None:
                    failed.append(stream["name"])
                    reason = "; ".join(warnings) or "No usable themes were returned."
                    stream_details.append(f"{stream['name']}: no codebook was produced. {reason}")
                else:
                    generated.append(stream["name"])
                    if warnings:
                        stream_details.append(
                            f"{stream['name']} warnings: " + "; ".join(warnings))
            except Exception as exc:
                failed.append(stream["name"])
                stream_details.append(f"{stream['name']}: {exc}")
            finally:
                generation_logger.removeHandler(log_handler)

        if failed:
            summary = [f"Codebook generation: {len(generated)}/{len(streams)} succeeded.",
                       "Generated: " + (", ".join(generated) if generated else "none"),
                       "Failed: " + ", ".join(failed)]
            if stream_details:
                summary.append("Details:\n- " + "\n- ".join(stream_details))
            return "\n".join(summary)
        if stream_details:
            return f"Codebooks saved to {output_dir}\n" + "\n".join(stream_details)

    if operation in ("apply", "both"):
        args = SimpleNamespace(**common, max_rows=None)
        prompts = {"system": load_prompt(settings["prompt_dir"], "code_system.txt"),
                   "batch": load_prompt(settings["prompt_dir"], "code_batch.txt")}
        results = []
        for stream in streams:
            stream = dict(stream, codebook=str(output_dir /
                                                f"{stream['name'].lower()}_codebook.md"))
            result, codes = apply_stream(df, stream, id_col, args,
                                         settings["context"], prompts)
            results.append((stream, result, codes))
        for stream, result, codes in results:
            prefix = stream["prefix"]
            for code in codes:
                df[f"{prefix}_{code}"] = df[id_col].apply(
                    lambda value, code=code: int(code in result.get(str(value), [])))
            df[f"{prefix}_Codes_Applied"] = df[id_col].apply(
                lambda value: ", ".join(result.get(str(value), [])))
        output = output_dir / "final_coded_results.csv"
        df.to_csv(output, index=False)
        return f"Coded results saved to {output}"
    return f"Codebooks saved to {output_dir}"


class SurveyCoderApp:
    """Small, guided interface that keeps advanced settings out of the way."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Survey Coder")
        self.root.minsize(780, 660)
        self.root.geometry("900x760")
        self.root.option_add("*Font", "TkDefaultFont 11")

        self.input_var = tk.StringVar()
        self.sheet_var = tk.StringVar()
        self.context_var = tk.StringVar(value="student survey feedback")
        self.provider_var = tk.StringVar(value="openai")
        self.model_var = tk.StringVar(value="gpt-4o-mini")
        self.output_var = tk.StringVar(value="outputs")
        self.prompt_var = tk.StringVar(value="prompts")
        self.base_var = tk.StringVar()
        self.batch_var = tk.StringVar(value="5")
        self.sample_var = tk.StringVar(value="1000")
        self.min_var = tk.StringVar(value="5")
        self.temp_var = tk.StringVar(value="0.0")
        self.max_output_tokens_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Choose a data file to begin.")
        self.progress_var = tk.DoubleVar(value=0)
        self.df: pd.DataFrame | None = None
        self.stream_rows: dict[str, tuple[tk.BooleanVar, tk.StringVar, tk.StringVar]] = {}
        self.busy = False
        self.stage = "generate"
        self.preferences_path = self._preferences_path()
        self.preferences_after: str | None = None
        self._load_llm_preferences()
        self._last_provider = self.provider_var.get()

        self._build()
        for variable in (self.provider_var, self.model_var, self.base_var,
                         self.max_output_tokens_var):
            variable.trace_add("write", self._schedule_save_llm_preferences)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _preferences_path() -> Path:
        if sys.platform == "darwin":
            config_root = Path.home() / "Library" / "Application Support"
            return config_root / "Survey Coder" / "settings.json"
        if os.name == "nt":
            config_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            return config_root / "Survey Coder" / "settings.json"
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return config_root / "survey-coder" / "settings.json"

    def _load_llm_preferences(self) -> None:
        try:
            preferences = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            provider = preferences.get("provider", "openai")
            if provider not in ("openai", "google", "vllm"):
                return
            self.provider_var.set(provider)
            self.model_var.set(preferences.get("model", "gpt-4o-mini"))
            self.base_var.set(preferences.get("base_url") or
                              (os.environ.get("VLLM_BASE_URL", "") if provider == "vllm" else ""))
            max_output_tokens = preferences.get("max_output_tokens")
            if provider == "vllm" and "max_output_tokens" not in preferences:
                max_output_tokens = 32768
            self.max_output_tokens_var.set(
                "" if max_output_tokens is None else str(max_output_tokens))
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            pass

    def _schedule_save_llm_preferences(self, *_args) -> None:
        if self.preferences_after is not None:
            self.root.after_cancel(self.preferences_after)
        self.preferences_after = self.root.after(500, self._save_llm_preferences)

    def _save_llm_preferences(self) -> None:
        self.preferences_after = None
        preferences = {
            "provider": self.provider_var.get(),
            "model": self.model_var.get(),
            "base_url": self.base_var.get(),
            "max_output_tokens": self.max_output_tokens_var.get(),
        }
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.preferences_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(preferences, indent=2), encoding="utf-8")
            temporary_path.replace(self.preferences_path)
        except OSError as exc:
            self.status_var.set(f"Could not save local model preferences: {exc}")

    def _close(self) -> None:
        if self.preferences_after is not None:
            self.root.after_cancel(self.preferences_after)
            self.preferences_after = None
        self._save_llm_preferences()
        self.root.destroy()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=(24, 20, 24, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        ttk.Label(header, text="Survey Coder", font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
        ttk.Label(header, text="Build and apply codebooks to open-text survey responses.",
                  foreground="#555555").pack(anchor="w", pady=(3, 0))

        self.steps = ttk.Frame(outer)
        self.steps.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.step_labels: list[ttk.Label] = []
        for i, title in enumerate(("1  Data & questions", "2  Model & output", "3  Run & review")):
            label = ttk.Label(self.steps, text=title, padding=(10, 8), relief="groove")
            label.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            self.steps.columnconfigure(i, weight=1)
            self.step_labels.append(label)

        self.content = ttk.Frame(outer)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.pages: list[ttk.Frame] = []
        self._build_data_page()
        self._build_settings_page()
        self._build_run_page()

        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status_var,
                                      wraplength=540, foreground="#444444")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.back_button = ttk.Button(footer, text="Back", command=self.back)
        self.back_button.grid(row=0, column=1, padx=(8, 6))
        self.next_button = ttk.Button(footer, text="Continue", command=self.next)
        self.next_button.grid(row=0, column=2)
        self._show_page(0)

    def _section(self, parent: ttk.Frame, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, padding=14)

    def _build_data_page(self) -> None:
        page = ttk.Frame(self.content)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self.pages.append(page)

        source = self._section(page, "1. Choose your data")
        ttk.Label(source, text="Select a CSV or Excel file, then choose a worksheet if needed.",
                  foreground="#555555").pack(anchor="w", pady=(0, 10))
        source.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        source.columnconfigure(0, weight=1)
        input_row = ttk.Frame(source)
        input_row.pack(fill="x")
        self.input_entry = ttk.Entry(input_row, textvariable=self.input_var)
        self.input_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(input_row, text="Browse…", command=self.browse_input).pack(side="left", padx=(8, 0))
        sheet_row = ttk.Frame(source)
        sheet_row.pack(fill="x", pady=(10, 0))
        ttk.Label(sheet_row, text="Worksheet", width=14).pack(side="left", anchor="w")
        self.sheet_box = ttk.Combobox(sheet_row, textvariable=self.sheet_var, state="readonly", width=32)
        self.sheet_box.pack(side="left", fill="x", expand=True)
        self.sheet_box.bind("<<ComboboxSelected>>", self.refresh_columns)
        self.data_summary = ttk.Label(source, text="No file loaded", foreground="#555555")
        self.data_summary.pack(anchor="w", pady=(8, 0))

        mapping = self._section(page, "2. Choose columns and describe each question")
        ttk.Label(mapping, text="Each response column becomes its own codebook. Add the question wording for each one.",
                  foreground="#555555", wraplength=680).grid(row=0, column=0, sticky="w", pady=(0, 10))
        mapping.grid(row=1, column=0, sticky="nsew")
        mapping.columnconfigure(0, weight=1)
        mapping.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(mapping)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.selected_count = ttk.Label(toolbar, text="0 columns selected")
        self.selected_count.pack(side="left")
        ttk.Button(toolbar, text="Select all", command=lambda: self.select_all(True)).pack(side="right")
        ttk.Button(toolbar, text="Clear", command=lambda: self.select_all(False)).pack(side="right", padx=(0, 6))
        self.mapping_canvas = tk.Canvas(mapping, highlightthickness=0, height=250)
        self.mapping_scroll = ttk.Scrollbar(mapping, orient="vertical", command=self.mapping_canvas.yview)
        self.mapping_canvas.configure(yscrollcommand=self.mapping_scroll.set)
        self.mapping_canvas.grid(row=2, column=0, sticky="nsew")
        self.mapping_scroll.grid(row=2, column=1, sticky="ns")
        self.mapping_inner = ttk.Frame(self.mapping_canvas)
        self.mapping_window = self.mapping_canvas.create_window((0, 0), window=self.mapping_inner, anchor="nw")
        self.mapping_inner.bind("<Configure>", lambda _e: self.mapping_canvas.configure(
            scrollregion=self.mapping_canvas.bbox("all")))
        self.mapping_canvas.bind("<Configure>", lambda e: self.mapping_canvas.itemconfigure(
            self.mapping_window, width=e.width))
        self._render_mapping([])

    def _build_settings_page(self) -> None:
        page = ttk.Frame(self.content)
        page.columnconfigure(0, weight=1)
        self.pages.append(page)
        title = ttk.Label(page, text="Model and output", font=("TkDefaultFont", 16, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 12))
        model = self._section(page, "Model")
        model.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(model, text="Choose the service and model that will analyse your responses.",
                  foreground="#555555").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        for col in (1, 3):
            model.columnconfigure(col, weight=1)
        ttk.Label(model, text="Provider").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        provider = ttk.Combobox(model, textvariable=self.provider_var,
                                values=("openai", "google", "vllm"), state="readonly", width=22)
        provider.grid(row=1, column=1, sticky="ew", pady=5)
        provider.bind("<<ComboboxSelected>>", self.provider_changed)
        ttk.Label(model, text="Model name").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.model_box = ttk.Combobox(model, textvariable=self.model_var, state="normal")
        self.model_box.grid(row=2, column=1, sticky="ew", pady=5)
        self.refresh_models_button = ttk.Button(model, text="Refresh models",
                                                command=self.refresh_vllm_models)
        self.refresh_models_button.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=5)
        self.base_label = ttk.Label(model, text="Compatible API URL")
        self.base_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        self.base_entry = ttk.Entry(model, textvariable=self.base_var)
        self.base_entry.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(model, text="Context").grid(row=1, column=2, sticky="w", padx=(18, 8), pady=5)
        ttk.Entry(model, textvariable=self.context_var).grid(row=1, column=3, sticky="ew", pady=5)
        self.model_help = ttk.Label(model, text="Used to frame what the comments are about.",
                                    foreground="#555555", wraplength=360)
        self.model_help.grid(row=3, column=2, columnspan=2, sticky="w", padx=(18, 0))
        ttk.Label(model, text="Max output tokens").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 3))
        ttk.Entry(model, textvariable=self.max_output_tokens_var, width=14).grid(
            row=4, column=1, sticky="w", pady=(8, 3))
        ttk.Label(model, text="vLLM default: 32,768. Adjust to your server’s limit.",
                  foreground="#555555", wraplength=360).grid(
            row=4, column=2, columnspan=2, sticky="w", padx=(18, 0), pady=(8, 3))

        output = self._section(page, "Output")
        output.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        output.columnconfigure(0, weight=1)
        ttk.Label(output, text="Generated codebooks and coded results are written here.",
                  foreground="#555555").pack(anchor="w", pady=(0, 8))
        self._path_row(output, "Output folder", self.output_var, self.choose_output_dir)

        advanced = ttk.LabelFrame(page, text="Advanced settings", padding=10)
        advanced.grid(row=3, column=0, sticky="ew")
        self.advanced_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(advanced, text="Show processing and prompt settings",
                        variable=self.advanced_visible,
                        command=self.toggle_advanced).grid(row=0, column=0, columnspan=4, sticky="w")
        self.advanced_fields = ttk.Frame(advanced)
        self.advanced_fields.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for col in (1, 3):
            self.advanced_fields.columnconfigure(col, weight=1)
        self._setting_entry(self.advanced_fields, "Batch size", self.batch_var, 0, 0,
                            "Number of comments sent to the model in each request.")
        self._setting_entry(self.advanced_fields, "Sample size", self.sample_var, 0, 2,
                            "Maximum comments used to generate each codebook.")
        self._setting_entry(self.advanced_fields, "Minimum text length", self.min_var, 1, 0,
                            "Shorter responses are ignored during codebook generation.")
        self._setting_entry(self.advanced_fields, "Temperature", self.temp_var, 1, 2,
                            "Lower values make model output more consistent.")
        self._path_row(self.advanced_fields, "Prompt folder", self.prompt_var,
                       self.choose_prompt_dir, row=2)
        self.advanced_fields.grid_remove()
        self.provider_changed()

    def _build_run_page(self) -> None:
        page = ttk.Frame(self.content)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        self.pages.append(page)
        ttk.Label(page, text="Run your analysis", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 12))
        self.summary = ttk.LabelFrame(page, text="Run summary", padding=14)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.summary_text = ttk.Label(self.summary, text="", justify="left", wraplength=680)
        self.summary_text.pack(anchor="w")
        self.work = ttk.LabelFrame(page, text="Progress", padding=14)
        self.work.grid(row=2, column=0, sticky="nsew")
        self.work.columnconfigure(0, weight=1)
        self.work.rowconfigure(2, weight=1)
        self.run_status = ttk.Label(self.work, text="Ready when you are.")
        self.run_status.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(self.work, variable=self.progress_var,
                                        maximum=100, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(9, 12))
        log_frame = ttk.Frame(self.work)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled",
                                relief="solid", borderwidth=1, padx=8, pady=8)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        actions = ttk.Frame(page)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="Load configuration…", command=self.load_config).pack(side="left")
        ttk.Button(actions, text="Save configuration…", command=self.save_config).pack(side="left", padx=(8, 0))
        self.open_output_button = ttk.Button(actions, text="Open output folder", command=self.open_output_dir)
        self.open_output_button.pack(side="left", padx=(8, 0))
        self.action_button = ttk.Button(actions, text="Generate codebooks", command=self.run_action)
        self.action_button.pack(side="right")
        self.review_button = ttk.Button(actions, text="Apply reviewed codebooks",
                                        command=self.apply_reviewed)
        self.review_button.pack(side="right", padx=(0, 8))
        self.review_button.state(["disabled"])

    def _setting_entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar,
                       row: int, col: int, note: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=12)
        entry.grid(row=row, column=col + 1, sticky="ew", pady=4)
        ttk.Label(parent, text=note, foreground="#666666", wraplength=205).grid(
            row=row + 3, column=col, columnspan=2, sticky="w")

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar,
                  command, row: int = 0) -> None:
        line = ttk.Frame(parent)
        if row:
            line.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        else:
            line.pack(fill="x", pady=(0, 5))
        ttk.Label(line, text=label, width=15).pack(side="left", anchor="w")
        ttk.Entry(line, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="Browse…", command=command).pack(side="left", padx=(8, 0))

    def _show_page(self, index: int) -> None:
        self.page_index = index
        for page in self.pages:
            page.grid_remove()
        self.pages[index].grid(row=0, column=0, sticky="nsew")
        for i, label in enumerate(self.step_labels):
            label.configure(relief="solid" if i == index else "groove")
        self.back_button.state(["disabled"] if index == 0 or self.busy else ["!disabled"])
        self.next_button.configure(text="Review run" if index == 1 else "Continue")
        self.next_button.grid()
        if index == 2:
            self.next_button.grid_remove()
            self.back_button.configure(text="Edit settings")
            self.back_button.grid()
            self.refresh_summary()
        else:
            self.back_button.configure(text="Back")
            self.status_var.set(("Choose a data file to begin." if index == 0 else
                                 "Settings are ready. Review before starting a run."))

    def next(self) -> None:
        try:
            if self.page_index == 0:
                self.settings_from_ui(validate_data=True)
                self._show_page(1)
            elif self.page_index == 1:
                self.settings_from_ui(validate_data=True)
                self._show_page(2)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check your settings", str(exc), parent=self.root)

    def back(self) -> None:
        if self.page_index > 0 and not self.busy:
            self._show_page(self.page_index - 1)

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("Survey files", "*.xlsx *.xls *.csv"), ("All files", "*.*")])
        if not path:
            return
        self.input_var.set(path)
        self.status_var.set("Reading file columns…")
        try:
            if path.lower().endswith(".csv"):
                self.df = pd.read_csv(path, nrows=100)
                sheets = ["CSV"]
            else:
                book = pd.ExcelFile(path)
                sheets = book.sheet_names
                self.df = pd.read_excel(path, sheet_name=sheets[0], nrows=100)
            self.sheet_box.configure(values=sheets)
            self.sheet_var.set(sheets[0])
            self._render_mapping(list(self.df.columns))
            self._update_data_summary()
            self.status_var.set("File loaded. Choose columns and add their question wording.")
        except Exception as exc:
            self.df = None
            self._render_mapping([])
            messagebox.showerror("Could not open file", str(exc), parent=self.root)
            self.status_var.set("The selected file could not be opened.")

    def refresh_columns(self, *_args) -> None:
        if not self.input_var.get() or self.input_var.get().lower().endswith(".csv"):
            return
        try:
            self.df = pd.read_excel(self.input_var.get(), sheet_name=self.sheet_var.get(), nrows=100)
            self._render_mapping(list(self.df.columns))
            self._update_data_summary()
        except Exception as exc:
            messagebox.showerror("Could not read worksheet", str(exc), parent=self.root)

    def _update_data_summary(self) -> None:
        if self.df is None:
            self.data_summary.configure(text="No file loaded")
            return
        self.data_summary.configure(
            text=f"{len(self.df):,} preview rows · {len(self.df.columns)} columns")

    def _render_mapping(self, columns: list[str]) -> None:
        for child in self.mapping_inner.winfo_children():
            child.destroy()
        self.stream_rows.clear()
        headers = ("Analyse", "Response column", "Question wording", "Output prefix")
        widths = (9, 23, 38, 16)
        for col, (text, width) in enumerate(zip(headers, widths)):
            label = ttk.Label(self.mapping_inner, text=text, width=width, font=("TkDefaultFont", 10, "bold"))
            label.grid(row=0, column=col, sticky="w", padx=(0, 6), pady=(0, 7))
        for row, column in enumerate(columns, start=1):
            include = tk.BooleanVar(value=False)
            question = tk.StringVar()
            prefix = tk.StringVar(value=str(column))
            cb = ttk.Checkbutton(self.mapping_inner, variable=include, command=self.update_selected_count)
            cb.grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
            ttk.Label(self.mapping_inner, text=str(column), width=23,
                      wraplength=180).grid(row=row, column=1, sticky="w", padx=(0, 6), pady=3)
            question_entry = ttk.Entry(self.mapping_inner, textvariable=question)
            question_entry.grid(row=row, column=2, sticky="ew", padx=(0, 6), pady=3)
            ttk.Entry(self.mapping_inner, textvariable=prefix, width=16).grid(
                row=row, column=3, sticky="ew", pady=3)
            include.trace_add("write", lambda *_: self.update_selected_count())
            self.stream_rows[str(column)] = (include, question, prefix)
        self.mapping_inner.columnconfigure(2, weight=1)
        self.update_selected_count()

    def select_all(self, selected: bool) -> None:
        for include, _question, _prefix in self.stream_rows.values():
            include.set(selected)

    def update_selected_count(self) -> None:
        if hasattr(self, "selected_count"):
            count = sum(include.get() for include, _question, _prefix in self.stream_rows.values())
            self.selected_count.configure(text=f"{count} column{'s' if count != 1 else ''} selected")

    def settings_from_ui(self, validate_data: bool = False) -> dict:
        streams = []
        for column, (include, question, prefix) in self.stream_rows.items():
            if include.get():
                if not question.get().strip():
                    raise ValueError(f"Add the survey question for column “{column}”.")
                streams.append({"name": column, "column": column,
                                "question": question.get().strip(),
                                "prefix": prefix.get().strip() or column})
        if not self.input_var.get() or not streams:
            raise ValueError("Choose a data file and select at least one response column.")
        if validate_data and not Path(self.input_var.get()).is_file():
            raise ValueError("The selected data file does not exist. Browse to the file again.")
        settings = {
            "input": self.input_var.get(), "sheet": self.sheet_var.get(), "streams": streams,
            "context": self.context_var.get().strip(), "provider": self.provider_var.get(),
            "model": self.model_var.get().strip(), "base_url": self.base_var.get().strip(),
            "output_dir": self.output_var.get().strip(), "prompt_dir": self.prompt_var.get().strip(),
            "batch_size": int(self.batch_var.get()), "sample_size": int(self.sample_var.get()),
            "min_length": int(self.min_var.get()), "temperature": float(self.temp_var.get()),
            "max_output_tokens": (int(self.max_output_tokens_var.get())
                                  if self.max_output_tokens_var.get().strip() else None),
        }
        if not settings["model"] or not settings["output_dir"]:
            raise ValueError("Enter a model name and output folder.")
        if settings["provider"] == "vllm" and not settings["base_url"]:
            raise ValueError("Enter a vLLM API URL or set VLLM_BASE_URL.")
        if settings["batch_size"] < 1 or settings["sample_size"] < 1 or settings["min_length"] < 0:
            raise ValueError("Batch size and sample size must be positive; minimum text length cannot be negative.")
        if settings["max_output_tokens"] is not None and settings["max_output_tokens"] < 1:
            raise ValueError("Maximum output tokens must be a positive integer, or left blank for the server default.")
        if not 0 <= settings["temperature"] <= 2:
            raise ValueError("Temperature must be between 0 and 2.")
        return settings

    def refresh_summary(self) -> None:
        try:
            settings = self.settings_from_ui()
            lines = [f"File: {Path(settings['input']).name}",
                     f"Columns: {', '.join(s['column'] for s in settings['streams'])}",
                     f"Model: {settings['provider']} / {settings['model']}",
                     "Output token limit: " + (str(settings["max_output_tokens"])
                                                 if settings["max_output_tokens"] else "provider/server default"),
                     f"Output: {settings['output_dir']}",
                     "First step: generate codebooks, review the Markdown files, then apply them."]
            self.summary_text.configure(text="\n".join(lines))
            self.action_button.state(["!disabled"])
        except Exception as exc:
            self.summary_text.configure(text=str(exc))
            self.action_button.state(["disabled"])

    def run_action(self) -> None:
        self._run("generate")

    def apply_reviewed(self) -> None:
        try:
            settings = self.settings_from_ui(validate_data=True)
            missing = [str(Path(settings["output_dir"]) /
                           f"{s['name'].lower()}_codebook.md") for s in settings["streams"]
                       if not (Path(settings["output_dir"]) /
                               f"{s['name'].lower()}_codebook.md").is_file()]
            if missing:
                raise ValueError("Generate the codebooks first. Missing:\n" + "\n".join(missing))
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Cannot apply codebooks", str(exc), parent=self.root)
            return
        self._run("apply", settings=settings)

    def _run(self, operation: str, settings: dict | None = None) -> None:
        try:
            settings = settings or self.settings_from_ui(validate_data=True)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check your settings", str(exc), parent=self.root)
            return
        if operation == "both":
            missing = [s["name"] for s in settings["streams"] if not (
                Path(settings["output_dir"]) / f"{s['name'].lower()}_codebook.md").is_file()]
            if missing:
                messagebox.showerror("Cannot run both", "Run both needs an existing codebook for every selected column. Missing: " + ", ".join(missing), parent=self.root)
                return
        self.busy = True
        self.back_button.state(["disabled"])
        self.next_button.state(["disabled"])
        self.action_button.state(["disabled"])
        self.review_button.state(["disabled"])
        self.run_status.configure(text="Preparing the analysis…")
        self.status_var.set("The analysis is running. This may take a while for large surveys.")
        self.progress.start(12)
        self._append_log(f"Starting {operation} for {len(settings['streams'])} response column(s).")
        if operation in ("generate", "both"):
            self.run_status.configure(text="Generating codebooks from the selected responses…")
        elif operation == "apply":
            self.run_status.configure(text="Applying the reviewed codebooks to the dataset…")
        worker = threading.Thread(target=self._worker, args=(settings, operation), daemon=True)
        worker.start()

    def _worker(self, settings: dict, operation: str) -> None:
        try:
            result = run_pipeline(settings, operation)
            self.root.after(0, lambda: self._finished(operation, result, None))
        except Exception as exc:
            detail = f"{exc}\n\n{traceback.format_exc()}"
            error_message = str(exc)
            self.root.after(0, lambda: self._finished(operation, error_message, detail))

    def _finished(self, operation: str, result: str, detail: str | None) -> None:
        self.progress.stop()
        self.busy = False
        self.back_button.state(["!disabled"] if self.page_index > 0 else ["disabled"])
        self.next_button.state(["!disabled"])
        self.action_button.state(["!disabled"])
        self.review_button.state(["!disabled"] if not detail and operation == "generate" else ["disabled"])
        if detail:
            self.run_status.configure(text="The run stopped with an error.")
            self.status_var.set("Run failed. Expand the details below or copy the error to share for support.")
            self._append_log("ERROR: " + result)
            self._append_log(detail)
        else:
            partial_generation = (operation in ("generate", "both") and
                                  result.startswith("Codebook generation:") and
                                  "Failed:" in result)
            self.run_status.configure(text=("Generation finished with failures."
                                            if partial_generation else "Run complete."))
            self.status_var.set("Some codebooks could not be generated. See run details."
                                if partial_generation else result)
            self._append_log(result)
            if operation == "generate":
                self.action_button.configure(text="Generate again")
                self.action_button.state(["!disabled"])
                has_generated = not result.startswith("Codebook generation: 0/")
                self.review_button.state(["!disabled"] if has_generated else ["disabled"])
                if has_generated:
                    self._append_log("Review the codebooks in the output folder before applying them.")
            elif operation == "apply":
                self.open_output_button.focus_set()
        self.refresh_summary()

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def toggle_advanced(self) -> None:
        if self.advanced_visible.get():
            self.advanced_fields.grid()
        else:
            self.advanced_fields.grid_remove()

    def provider_changed(self, *_args) -> None:
        provider = self.provider_var.get()
        if provider == "vllm" and getattr(self, "_last_provider", None) != "vllm":
            if not self.max_output_tokens_var.get().strip():
                self.max_output_tokens_var.set("32768")
        self._last_provider = provider
        needs_endpoint = provider in ("openai", "vllm")
        self.base_label.configure(text="vLLM API URL" if provider == "vllm" else "Compatible API URL")
        if provider == "vllm" and not self.base_var.get().strip():
            self.base_var.set(os.environ.get("VLLM_BASE_URL", ""))
        state = "normal" if needs_endpoint else "disabled"
        self.base_entry.configure(state=state)
        self.base_label.configure(foreground="#222222" if needs_endpoint else "#888888")
        if provider == "vllm":
            if self.model_var.get() == "gpt-4o-mini":
                self.model_var.set("")
            self.refresh_models_button.state(["!disabled"])
            self.model_box.configure(state="normal")
            self.model_help.configure(text="Choose Refresh models to query the server. If discovery fails, enter a model ID manually.")
        else:
            self.refresh_models_button.state(["disabled"])
            self.model_box.configure(state="normal")
            self.model_help.configure(text="Used to frame what the comments are about.")

    def refresh_vllm_models(self) -> None:
        if self.provider_var.get() != "vllm":
            return
        endpoint = self.base_var.get().strip().rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            messagebox.showerror(
                "Invalid vLLM URL",
                "Enter a full server URL, for example http://localhost:8000/v1.",
                parent=self.root,
            )
            return
        models_url = endpoint if parsed.path.endswith("/models") else f"{endpoint}/models"
        self.refresh_models_button.state(["disabled"])
        self.model_help.configure(text="Querying the vLLM server…")
        self.status_var.set("Retrieving available models from vLLM…")
        api_key = os.environ.get("VLLM_API_KEY")

        def fetch() -> None:
            try:
                headers = {"Accept": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                request = Request(models_url, headers=headers)
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                models = sorted({item["id"] for item in payload.get("data", [])
                                 if isinstance(item, dict) and item.get("id")})
                if not models:
                    raise ValueError("The server returned no model IDs.")
                self.root.after(0, lambda: self._models_loaded(models))
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                message = str(exc)
                self.root.after(0, lambda: self._models_failed(message))

        threading.Thread(target=fetch, daemon=True).start()

    def _models_loaded(self, models: list[str]) -> None:
        current = self.model_var.get().strip()
        self.model_box.configure(values=models, state="readonly")
        self.model_var.set(current if current in models else models[0])
        self.refresh_models_button.state(["!disabled"])
        self.model_help.configure(text=f"Found {len(models)} model(s). Choose one from the list.")
        self.status_var.set(f"Retrieved {len(models)} model(s) from vLLM.")

    def _models_failed(self, message: str) -> None:
        self.model_box.configure(state="normal")
        self.refresh_models_button.state(["!disabled"])
        self.model_help.configure(text="Could not retrieve models. Check the URL/key or enter the model ID manually.")
        self.status_var.set("Model discovery failed.")
        messagebox.showerror("Could not retrieve vLLM models", message, parent=self.root)

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, initialdir=self.output_var.get() or ".")
        if path:
            self.output_var.set(path)

    def choose_prompt_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, initialdir=self.prompt_var.get() or ".")
        if path:
            self.prompt_var.set(path)

    def open_output_dir(self) -> None:
        path = Path(self.output_var.get()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])

    def save_config(self) -> None:
        try:
            settings = self.settings_from_ui()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Check your settings", str(exc), parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            config = {
                "context": settings["context"],
                "streams": settings["streams"],
                "llm": {"provider": settings["provider"], "model": settings["model"],
                        "base_url": settings["base_url"] or None,
                        "max_output_tokens": settings["max_output_tokens"]},
            }
            Path(path).write_text(json.dumps(config, indent=2), encoding="utf-8")
            self._append_log(f"Saved configuration to {path}")

    def load_config(self) -> None:
        path = filedialog.askopenfilename(parent=self.root,
                                          filetypes=[("JSON configuration", "*.json")])
        if not path:
            return
        try:
            with Path(path).open(encoding="utf-8") as config_file:
                config = json.load(config_file)
            streams = config.get("streams")
            if not isinstance(streams, list) or not streams:
                raise ValueError("Configuration needs a non-empty streams list.")
            self.context_var.set(config.get("context", "qualitative survey feedback"))
            llm = config.get("llm", {})
            provider = llm.get("provider", "openai")
            if provider not in ("openai", "google", "vllm"):
                raise ValueError("Provider must be openai, google, or vllm.")
            endpoint = llm.get("base_url", "")
            if provider == "vllm" and not endpoint:
                endpoint = os.environ.get("VLLM_BASE_URL", "")
            if provider == "vllm" and not endpoint:
                raise ValueError("A vLLM configuration needs llm.base_url or VLLM_BASE_URL.")
            self.provider_var.set(provider)
            self.model_var.set(llm.get("model", "gpt-4o-mini"))
            self.base_var.set(endpoint)
            max_output_tokens = llm.get("max_output_tokens")
            if provider == "vllm" and "max_output_tokens" not in llm:
                max_output_tokens = 32768
            self.max_output_tokens_var.set(
                "" if max_output_tokens is None else str(max_output_tokens))
            self._last_provider = provider
            self.provider_changed()
            rows = {str(stream.get("column")): stream for stream in streams}
            for column, (include, question, prefix) in self.stream_rows.items():
                stream = rows.get(column)
                include.set(stream is not None)
                if stream is not None:
                    question.set(stream.get("question", ""))
                    prefix.set(stream.get("prefix", stream.get("name", column)))
            self.update_selected_count()
            self._append_log(f"Loaded configuration from {path}")
        except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            messagebox.showerror("Could not load configuration", str(exc), parent=self.root)


def main() -> None:
    # Some relocatable Python builds retain a build-time Tcl path (notably uv
    # on macOS). Prefer the Tcl/Tk libraries shipped with that interpreter.
    tcl_roots = [Path(sys.base_prefix) / "lib", Path(sys.base_prefix) / "tcl"]
    for root in tcl_roots:
        tcl = next(root.glob("tcl*/init.tcl"), None) if root.exists() else None
        tk_file = next(root.glob("tk*/tk.tcl"), None) if root.exists() else None
        if tcl and "TCL_LIBRARY" not in os.environ:
            os.environ["TCL_LIBRARY"] = str(tcl.parent)
        if tk_file and "TK_LIBRARY" not in os.environ:
            os.environ["TK_LIBRARY"] = str(tk_file.parent)
        if tcl and tk_file:
            break

    root = tk.Tk()
    SurveyCoderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
