import json
import os
from pathlib import Path
from typing import Any, Dict

PROVIDERS = ("openai", "google", "vllm", "openwebui")


def load_model_options(path: str | Path | None = None) -> dict[str, list[str]]:
    """Load provider model choices from JSON; an environment path can override the bundled file."""
    config_path = Path(path or os.environ.get("SURVEY_CODER_MODELS_CONFIG", "models.json"))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(config, dict):
        raise ValueError("Model configuration must be a JSON object keyed by provider.")
    options = {}
    for provider, models in config.items():
        if provider not in PROVIDERS or not isinstance(models, list) or not all(
                isinstance(model, str) and model.strip() for model in models):
            raise ValueError(f"Model configuration for {provider!r} must be a list of model names.")
        options[provider] = models
    return options


def api_key_environment_name(provider: str) -> str:
    return {
        "google": "GOOGLE_API_KEY",
        "vllm": "VLLM_API_KEY",
        "openwebui": "OPENWEBUI_API_KEY",
    }.get(provider, "OPENAI_API_KEY")


def base_url_environment_name(provider: str) -> str | None:
    return {"vllm": "VLLM_BASE_URL", "openwebui": "OPENWEBUI_BASE_URL"}.get(provider)


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
    if llm.get("provider") not in (None, *PROVIDERS):
        raise ValueError("Config llm.provider must be 'openai', 'google', 'vllm', or 'openwebui'")
    max_output_tokens = llm.get("max_output_tokens")
    if max_output_tokens is not None and (
            not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
            or max_output_tokens < 1):
        raise ValueError("Config llm.max_output_tokens must be a positive integer")
    return config


def load_prompt(prompt_dir: str, name: str) -> str:
    with Path(prompt_dir, name).open() as f:
        return f.read()
