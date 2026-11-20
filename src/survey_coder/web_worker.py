"""Durable single-question coding worker; no browser or HTTP request dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import secrets
import signal
import threading
import time

import pandas as pd

from . import apply_codebooks as engine
from .web_coding import ERROR, ROW_ID, STATUS, coding_response_schema
from .web_store import Store
from .web_failures import failure_report, provider_failure

logger = logging.getLogger(__name__)


def validate_batch(payload: object, indices: list[int], valid_codes: list[str]) -> list[tuple]:
    expected = {f"R{index + 1:06d}": index for index in indices}
    if isinstance(payload, dict) and payload.get("error") == "parse_failure":
        return [(index, "failed", None, "Model returned invalid JSON.") for index in indices]
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return [(index, "failed", None, "Model response did not contain a results list.") for index in indices]
    found: dict[str, list] = {}
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in expected:
            return [(index, "failed", None, "Model returned an unexpected response ID.") for index in indices]
        found.setdefault(item["id"], []).append(item)
    outcomes = []
    for identifier, index in expected.items():
        items = found.get(identifier, [])
        labels = items[0].get("codes") if len(items) == 1 else None
        if not items:
            error = "Model omitted this response ID."
        elif len(items) != 1:
            error = "Model returned this response ID more than once."
        elif not isinstance(labels, list) or any(not isinstance(code, str) for code in labels):
            error = "Model returned an invalid codes list."
        elif any(code not in valid_codes for code in labels):
            error = "Model returned unknown code names."
        elif len(set(labels)) != len(labels):
            error = "Model returned duplicate code names."
        else:
            error = None
        outcomes.append((index, "failed" if error else "coded", labels if not error else None, error))
    return outcomes


def execute_job(store: Store, job: dict, worker: str, call=None):
    call = call or engine.call_llm
    spec, run_id = job["spec"], job["run_id"]
    config = spec["config"]
    frame = pd.DataFrame(spec["data"], columns=spec["columns"])
    pending = store.pending(run_id)
    batch_size = 1 if spec.get("retry_count", 0) else config["batch_size"]
    # Older saved runs predate the capability setting. Apply the vLLM default
    # on retry as well, without changing their codebook, model or source rows.
    structured_outputs = config.get("structured_outputs", config["provider"] == "vllm" and config["json_mode"])
    stream = {"name": spec["column"], "column": spec["column"], "prefix": "AI"}
    for offset in range(0, len(pending), batch_size):
        if not store.renew(run_id, worker):
            return
        indices = pending[offset:offset + batch_size]
        records = [{ROW_ID: f"R{index + 1:06d}", spec["column"]: frame.iloc[index][spec["column"]]} for index in indices]
        system, prompt = engine.build_coding_prompt(
            stream, config["context"] + "\nSurvey question: " + spec["question"], spec["codebook"], records, ROW_ID, config["prompts"])
        system += ("\nAllowed code names (copy these strings exactly; do not include heading numbers): "
                   + json.dumps(spec["codes"], ensure_ascii=False)
                   + ". Return an empty codes list if none applies. Never invent an Other or Unclear code.")
        try:
            schema_args = [coding_response_schema(indices, spec["codes"])] if structured_outputs else []
            text = call(prompt, system, config["model"], config["temperature"], config["base_url"],
                        config["provider"], config["max_output_tokens"], config["think"], config["json_mode"], *schema_args)
            results = validate_batch(engine.parse_json_response(text), indices, spec["codes"])
        except Exception as exc:
            # Provider messages may contain credentials or comment text. Store a safe explanation.
            logger.warning("Coding request failed (%s) for run %s", type(exc).__name__, run_id)
            results = [(index, "failed", None, provider_failure(exc, schema_constrained=structured_outputs)) for index in indices]
        if not store.checkpoint(run_id, worker, results):
            return
    rows = store.row_results(run_id)
    frame[ROW_ID] = [f"R{index + 1:06d}" for index in range(len(frame))]
    labels = {frame.iloc[row["row_index"]][ROW_ID]: json.loads(row["labels"]) for row in rows if row["state"] == "coded"}
    output = engine.add_code_columns(frame, ROW_ID, [(stream, labels, spec["codes"])])
    output[STATUS] = [row["state"] for row in rows]
    output[ERROR] = [row["error"] or "" for row in rows]
    csv = output.to_csv(index=False).encode("utf-8-sig")
    manifest = {"run_id": run_id, "operator_id": job["owner_id"], "name": job["name"],
                "created_unix": job["created"], "finished_unix": time.time(),
                "sheet": spec["sheet"], "response_column": spec["column"], "id_column": spec["id_column"],
                "question": spec["question"], "codes": spec["codes"], "codebook_markdown": spec["codebook"],
                "non_response_value": spec.get("non_response_value"),
                "non_response_matching": spec.get("non_response_matching"),
                "configuration": config, "source_sha256": spec["source_sha256"],
                "retry_count": spec.get("retry_count", 0), "request_batch_size": batch_size,
                "structured_outputs": structured_outputs,
                "failures": failure_report(rows),
                "codebook_sha256": spec["codebook_sha256"], "result_sha256": hashlib.sha256(csv).hexdigest(),
                "counts": {state: sum(row["state"] == state for row in rows) for state in ("coded", "blank", "non_response", "failed")},
                "model_revision": "Requested model ID recorded; exact provider revision unavailable.",
                "missing_values": "Blank, configured non-response and failed rows have missing code cells; coded empty sets have zeros."}
    store.finish(run_id, worker, csv, json.dumps(manifest, indent=2).encode())


def work_once(store: Store, call=None) -> bool:
    worker = secrets.token_hex(16)
    job = store.claim(worker)
    if job is None:
        return False
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(15):
            if not store.renew(job["run_id"], worker):
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        execute_job(store, job, worker, call=call)
    except Exception as exc:
        logger.warning("Coding job failed (%s) for run %s", type(exc).__name__, job["run_id"])
        store.fail(job["run_id"], worker, "The coding job could not finish. Ask the service maintainer to check the worker.")
    finally:
        stopped.set()
        thread.join()
    return True


def main():
    parser = argparse.ArgumentParser(description="Process persisted Survey Coder jobs")
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.database)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        if not work_once(store):
            stop.wait(1)


if __name__ == "__main__":
    main()
