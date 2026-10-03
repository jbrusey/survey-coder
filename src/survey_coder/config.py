import json
from pathlib import Path
from typing import Any, Dict

DEFAULT_STREAMS = [
    {"name": "Positive", "column": "Comment1Positive", "question": "What was positive about the lecture/session?", "prefix": "Pos"},
    {"name": "Improvement", "column": "Comment1Improvement", "question": "What could be improved about the lecture/session?", "prefix": "Imp"},
]


def load_config(path: str | None) -> Dict[str, Any]:
    if not path:
        return {"context": "student lecture feedback", "streams": DEFAULT_STREAMS, "llm": {}}
    with Path(path).open() as f:
        config = json.load(f)
    if not config.get("streams"):
        raise ValueError("Config must contain a non-empty 'streams' list")
    for stream in config["streams"]:
        for key in ("name", "column", "question"):
            if not stream.get(key):
                raise ValueError(f"Each stream requires '{key}'")
        stream.setdefault("prefix", stream["name"])
    config.setdefault("context", "qualitative survey feedback")
    llm = config.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ValueError("Config 'llm' must be an object")
    if llm.get("provider") not in (None, "openai", "google", "vllm"):
        raise ValueError("Config llm.provider must be 'openai', 'google', or 'vllm'")
    max_output_tokens = llm.get("max_output_tokens")
    if max_output_tokens is not None and (
            not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
            or max_output_tokens < 1):
        raise ValueError("Config llm.max_output_tokens must be a positive integer")
    return config


def load_prompt(prompt_dir: str, name: str) -> str:
    with Path(prompt_dir, name).open() as f:
        return f.read()
