"""Safe failure explanations shared by the worker, browser and API.

Never copy provider exception messages or generated labels into these records.
"""
from __future__ import annotations

from collections import Counter


class OutputTruncatedError(ValueError):
    """The provider stopped generation at its output limit."""


# The persisted explanation also works with databases created before diagnostics.
GUIDANCE = {
    "Model returned invalid or unknown code names.": "Retry may help. The saved result does not distinguish an invalid list, unknown names or duplicate names. Retrying sends each failed response separately; repeated failures need a codebook or model review.",
    "Model returned unknown code names.": "Retry may help. The model used names outside the codebook. Repeated failures need a codebook or model review.",
    "Model returned duplicate code names.": "Retry may help. Each code must appear at most once per response.",
    "Model returned an invalid codes list.": "Retry may help. The model must return a list of code names, including an empty list when no code applies.",
    "Model returned invalid JSON.": "Retry may help. The model did not return readable JSON.",
    "Model response did not contain a results list.": "Retry may help. The model did not follow the required response structure.",
    "Model returned an unexpected response ID.": "Retry may help. The batch could not be safely matched to source rows.",
    "Model omitted this response ID.": "Retry may help. The model skipped this response.",
    "Model returned this response ID more than once.": "Retry may help. The model repeated a response ID.",
    "Model output was truncated.": "Retry uses one response per request, which may help. If it repeats, ask the maintainer to increase the output-token limit or review model thinking settings.",
    "Provider request timed out.": "Retry may help after the provider recovers. A timed-out request may still have incurred a charge.",
    "Could not connect to the provider.": "Retry after checking that the provider and network are available.",
    "Provider rate limit exceeded (HTTP 429).": "Wait before retrying. Ask the maintainer to check provider rate limits and quota if it continues.",
    "Provider temporarily unavailable.": "Retry may help after the provider recovers.",
    "Provider rejected credentials or access (HTTP 401/403).": "Ask the maintainer to fix provider credentials or model access before retrying.",
    "Provider rejected the request or model settings.": "Ask the maintainer to check the model ID, endpoint, context length and request settings before retrying.",
    "Provider rejected the schema-constrained request.": "Ask the maintainer to check JSON-schema structured-output support, the model ID, context length and request settings before retrying. Repeating the same request may not help.",
    "Provider request failed. Ask the service maintainer to check connectivity, credentials and model settings.": "Retry benefit is unknown. Older or unclassified failures lack a precise cause; ask the maintainer to check the service if it repeats.",
}
UNKNOWN = "Failure details are unavailable. Ask the service maintainer to check the worker before retrying."


def provider_failure(exc: Exception, *, schema_constrained: bool = False) -> str:
    """Classify SDK errors using types and status codes, never their messages."""
    from google.genai.errors import APIError
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(exc, OutputTruncatedError):
        return "Model output was truncated."
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return "Provider request timed out."
    if isinstance(exc, (APIConnectionError, ConnectionError)):
        return "Could not connect to the provider."
    status = exc.status_code if isinstance(exc, APIStatusError) else exc.code if isinstance(exc, APIError) else None
    if status in (401, 403):
        return "Provider rejected credentials or access (HTTP 401/403)."
    if status == 429:
        return "Provider rate limit exceeded (HTTP 429)."
    if status in (408, 504):
        return "Provider request timed out."
    if isinstance(status, int) and 500 <= status <= 599:
        return "Provider temporarily unavailable."
    if schema_constrained and status in (400, 422):
        return "Provider rejected the schema-constrained request."
    if isinstance(status, int) and 400 <= status <= 499:
        return "Provider rejected the request or model settings."
    return "Provider request failed. Ask the service maintainer to check connectivity, credentials and model settings."


def failure_report(rows: list[dict]) -> dict:
    failures = []
    for row in rows:
        if row["state"] != "failed":
            continue
        # Allowlist explanations even for old records; never render arbitrary errors.
        reason = row["error"] if row["error"] in GUIDANCE else "Failure details unavailable."
        failures.append({"row_id": f"R{row['row_index'] + 1:06d}", "reason": reason,
                         "guidance": GUIDANCE.get(reason, UNKNOWN)})
    counts = Counter(item["reason"] for item in failures)
    return {"summary": [{"reason": reason, "count": count, "guidance": GUIDANCE.get(reason, UNKNOWN)}
                        for reason, count in counts.items()], "rows": failures}
