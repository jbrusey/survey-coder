import json
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_STREAMS = [
    {"name": "Positive", "column": "Comment1Positive", "question": "What was positive about the lecture/session?", "prefix": "Pos"},
    {"name": "Improvement", "column": "Comment1Improvement", "question": "What could be improved about the lecture/session?", "prefix": "Imp"},
]


def load_config(path: str | None) -> Dict[str, Any]:
    if not path:
        return {"context": "student lecture feedback", "streams": DEFAULT_STREAMS}
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
    return config


def load_prompt(prompt_dir: str, name: str) -> str:
    with Path(prompt_dir, name).open() as f:
        return f.read()
