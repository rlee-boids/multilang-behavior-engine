from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Optional, List

import re
import json
import httpx
from google import genai
from openai import OpenAI

from app.core.config import settings
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract


class AIConversionError(Exception):
    pass


@dataclass
class TargetArtifacts:
    """
    Bundle of AI conversion outputs:

    - code: final target-language source.
    - python_requirements: list of pip requirement strings needed by that code.
    """
    code: str
    python_requirements: List[str]

def _clean_ai_code_string(text: str) -> str:
    """
    Final-pass sanitizer for AI-generated code.

    Designed to work on the *code string* that was already extracted
    from Gemini's pseudo-JSON by _best_effort_extract_code_from_pseudo_json.

    We:
    - Strip outer markdown fences if Gemini added ```...```.
    - Fix escaped quotes (\" -> ", \\\" -> " etc.).
    - Remove trailing backslashes at end-of-line (which caused \"\"\"\\ and
      similar issues in docstrings).

    IMPORTANT: we do NOT touch '\\n' / '\\t' escape sequences, because they
    are often inside Python string literals. Replacing them with actual
    newlines is exactly what broke code like:

        print("Server stopped.\\n")

    into:

        print("
        Server stopped.")
    """
    if not text:
        return text

    cleaned = text.strip()
    cleaned = _strip_markdown_fences(cleaned).strip()

    # Case 1: if someone upstream wrapped the whole thing in single or
    # double quotes, try to unescape once as a string literal.
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in ("'", '"')
    ):
        inner = cleaned[1:-1]
        try:
            # Interpret common escapes once (\" -> ", \\n -> \n, etc.)
            # NOTE: this path is mainly for truly "string-literal wrapped"
            # code, which is rarer with your best-effort extractor.
            cleaned = bytes(inner, "utf-8").decode("unicode_escape")
        except Exception:
            cleaned = inner
    else:
        # General case: the string already looks like source code.
        # Only fix problematic escaped quotes; DO NOT touch \n / \t.
        cleaned = cleaned.replace('\\"', '"').replace("\\'", "'")
        # Sometimes we get double-escaped quotes.
        cleaned = cleaned.replace("\\\\\"", '"').replace("\\\\'", "'")

    # Drop trailing backslashes that show up at EOL (e.g. in
    # docstrings like \"\"\"...\"\"\"\\)
    lines = cleaned.splitlines()
    trimmed_lines = []
    for line in lines:
        if line.endswith("\\"):
            trimmed_lines.append(line[:-1])
        else:
            trimmed_lines.append(line)
    cleaned = "\n".join(trimmed_lines)

    return cleaned


def _best_effort_extract_code_from_pseudo_json(text: str) -> str:
    """
    If the raw Gemini response *looks* like JSON with a "code" field
    but isn't valid JSON, try to pull out that string and decode escapes.

    If that fails, return the original text.
    """
    # Look for "code": "...."
    m = re.search(r'"code"\s*:\s*"(?P<code>(?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if not m:
        return text

    raw_code = m.group("code")

    # Safest way to decode JSON-style escapes is to wrap in quotes and json.loads
    try:
        return json.loads(f'"{raw_code}"')
    except json.JSONDecodeError:
        # Fallback: manual common escape replacements
        return (
            raw_code
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )


def _best_effort_extract_requirements_from_pseudo_json(text: str) -> List[str]:
    """
    Try to salvage "python_requirements": [ ... ] from a not-quite-valid JSON blob.
    """
    m = re.search(
        r'"python_requirements"\s*:\s*\[(?P<body>.*?)\]',
        text,
        re.DOTALL,
    )
    if not m:
        return []

    body = "[" + m.group("body") + "]"
    try:
        parsed = json.loads(body)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except json.JSONDecodeError:
        pass

    return []


def _fetch_source_code_from_github(
    repo_url: str,
    revision: str,
    file_path: str,
) -> str:
    """
    Fetch raw source code from GitHub using the standard
    raw.githubusercontent.com URL pattern.

    repo_url is expected to be something like:
      https://github.com/owner/repo or
      https://github.com/owner/repo.git
    """
    if "github.com" not in repo_url:
        raise AIConversionError(
            f"Unsupported repo host in repo_url={repo_url!r} (only GitHub is supported for now)"
        )

    # Normalize: strip ".git" and trailing slash
    clean = repo_url.rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]

    # Extract owner/repo
    parts = clean.split("/")
    if len(parts) < 2:
        raise AIConversionError(f"Unexpected repo_url format: {repo_url!r}")
    owner = parts[-2]
    repo_name = parts[-1]

    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{revision}/{file_path}"

    try:
        resp = httpx.get(raw_url, timeout=20.0)
    except Exception as exc:
        raise AIConversionError(f"HTTP error fetching source: {exc}") from exc

    if resp.status_code != 200:
        raise AIConversionError(
            f"Failed to fetch source from {raw_url} (status={resp.status_code})"
        )

    return resp.text


def _build_conversion_prompt(
    source_code: str,
    behavior: Behavior,
    contract: Optional[BehaviorContract],
    source_language: str,
    target_language: str,
    file_path: str,
) -> str:
    """
    Build a prompt instructing the model to do a direct-ish translation
    from the source language to the target language, and RETURN JSON
    ARTIFACTS:

      {
        "code": "<full target-language source>",
        "python_requirements": ["matplotlib", ...]
      }
    """
    contract_snippet = ""
    if contract:
        contract_snippet = dedent(
            f"""
            Contract information:
            - Contract ID: {contract.id}
            - Version: {contract.version}
            - Name: {contract.name}
            - Description: {contract.description}

            Test cases (JSON):
            {contract.test_cases}
            """
        )

    behavior_desc = behavior.description or behavior.name

    file_context = f"This source file's path in its Git repo is: {file_path!r}.\n"

    # Extra guidance for Perl -> Python so the AI doesn't feel
    # forced into a literal cgi-bin layout.
    extra_guidance = ""
    if source_language.lower() == "perl" and target_language.lower() == "python":
        extra_guidance = dedent(
            """
            Additional guidance for Perl -> Python:

            - If this file is a library under `lib/`, convert it into a normal
              importable Python module, keeping function and class names where reasonable.

            - If this file is an executable script under `bin/`, convert it into a
              Python CLI script with a clear `main()` entrypoint.

            - If this file lives under `cgi-bin/` or otherwise serves a web UI
              (for example `cgi-bin/plot_ui.cgi`), convert it into a small,
              idiomatic Python *WSGI* entrypoint that can be run inside a container.

              Concretely, for CGI-style code:
                * Expose a WSGI callable named `application(environ, start_response)`.
                * Handle both GET query parameters and POSTed form data, similar to CGI.
                * Render HTML directly as a bytes iterable from `application`.
                * Do NOT depend on heavyweight frameworks (no Flask/Django/etc); use
                  only the standard library plus whatever minimal third-party libs
                  you list in `python_requirements` (e.g., matplotlib).

              For local/container demo:
                * Add a standard `if __name__ == "__main__":` block that uses
                  `wsgiref.simple_server.make_server("0.0.0.0", 8000, application)`
                  and calls `serve_forever()`.

            - Keep the overall behaviour close to the original CGI script, but with
              clean, testable Python functions and classes where appropriate.
            """
        ).strip()

    prompt = f"""
    You are a senior software engineer who is an expert in both {source_language} and {target_language}.

    Your task is to convert the following {source_language} code into an idiomatic {target_language} module
    that preserves the behaviour as closely as possible.

    Behavior description:
    {behavior_desc}

    {file_context}
    {extra_guidance}

    {contract_snippet}

    Requirements:
    - Maintain the same high-level functionality and external behaviour.
    - Preserve function names and signatures where it makes sense.
    - If the original defines a module/class/namespace, reflect that appropriately in {target_language}.
    - If the original is an executable or CGI-style script, make sure the converted file
      still has a single obvious entrypoint that drives the same flow.
    - Do NOT include placeholders like "TODO" or "NotImplementedError".
    - Do NOT include any explanation or commentary outside of comments in the code.

    OUTPUT FORMAT (IMPORTANT):

    - Return a SINGLE JSON object with exactly these keys:
        1) "code": a string with the FULL {target_language} source file.
        2) "python_requirements": a JSON array of strings for any third-party
           Python packages that must be installed (for example ["matplotlib"]).

    - Do NOT wrap the JSON in markdown or triple backticks.
    - The response must be valid JSON by itself.

    Example of the *shape* (not actual content):

      {{
        "code": "print('hello')\\n",
        "python_requirements": ["matplotlib"]
      }}

    Here is the full {source_language} source to convert:

    {source_code}
    """

    return dedent(prompt).strip()


def _call_google_conversion(prompt: str) -> str:
    """
    Call Google Gemini and return the raw text response.

    The caller is responsible for parsing it as JSON artifacts.
    """
    if not settings.GOOGLE_API_KEY:
        raise AIConversionError("GOOGLE_API_KEY is not set")

    client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    try:
        response = client.models.generate_content(
            model=settings.GOOGLE_MODEL_NAME,
            contents=prompt,
        )
    except Exception as exc:
        raise AIConversionError(f"Google Gemini API call failed: {exc}") from exc

    # google-genai exposes .text for convenience
    text = getattr(response, "text", None)
    if not text:
        raise AIConversionError("Empty response from Google Gemini")

    return text.strip()


def _call_openai_conversion(prompt: str) -> str:
    """
    Call OpenAI and return raw text.

    For OpenAI we currently expect plain code (no JSON envelope), so later
    we will just wrap it in TargetArtifacts with empty python_requirements.
    """
    if not settings.OPENAI_API_KEY:
        raise AIConversionError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    try:
        # Using the Responses API style for newer models; adjust if needed
        response = client.responses.create(
            model=settings.OPENAI_MODEL_NAME,
            input=prompt,
        )
    except Exception as exc:
        raise AIConversionError(f"OpenAI API call failed: {exc}") from exc

    # Minimal extraction: first output chunk's first content item
    try:
        text = response.output[0].content[0].text
    except Exception as exc:
        raise AIConversionError(f"Unexpected OpenAI response structure: {exc}") from exc

    if not text:
        raise AIConversionError("Empty response from OpenAI")

    return text.strip()


def _strip_markdown_fences(raw: str) -> str:
    """
    Gemini sometimes returns ```json ... ``` even when we ask it not to.
    This helper strips the outer ```...``` fences if present.
    """
    text = raw.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        # Drop the first line (``` or ```json)
        first = lines[0].strip()
        if first.startswith("```"):
            lines = lines[1:]

        # Drop trailing fence if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


def _parse_gemini_artifacts(raw: str) -> TargetArtifacts:
    """
    Parse Gemini's JSON artifacts, being tolerant of markdown fences.
    """
    stripped = _strip_markdown_fences(raw)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        # We let the caller decide whether to fall back to code-only.
        raise AIConversionError(
            f"Failed to parse Gemini JSON response: {exc}; raw={raw!r}"
        ) from exc

    code = data.get("code")
    if not isinstance(code, str):
        raise AIConversionError("Gemini JSON is missing 'code' string field")

    py_reqs_field = data.get("python_requirements", [])
    if py_reqs_field is None:
        py_reqs_field = []
    if not isinstance(py_reqs_field, list):
        # Be defensive; ignore if not a list
        py_reqs: List[str] = []
    else:
        py_reqs = [str(x).strip() for x in py_reqs_field if str(x).strip()]
    cleaned_code = _clean_ai_code_string(code)
    return TargetArtifacts(code=cleaned_code, python_requirements=py_reqs)

def _extract_google_artifacts(raw: str) -> TargetArtifacts:
    """
    Robustly extract (code, python_requirements) from a Gemini response.

    Strategy:
    - Strip markdown fences (```...```).
    - Find the JSON object between the first '{' and last '}'.
    - json.loads() that substring.
    - Pull out "code" and "python_requirements".
    - Run the code through _clean_ai_code_string.
    - If anything goes wrong, fall back to treating the whole response as code.
    """
    if not raw:
        raise AIConversionError("Empty response from Google Gemini")

    # 1) Remove ``` and ```json fences if present
    stripped = _strip_markdown_fences(raw).strip()

    # 2) Try to isolate the JSON object between the first '{' and last '}'
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = stripped[start : end + 1]
    else:
        candidate = stripped

    # 3) Try to parse JSON
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Fallback: Gemini did not give us parseable JSON;
        # treat the entire stripped text as code.
        code_fallback = _clean_ai_code_string(stripped)
        return TargetArtifacts(code=code_fallback, python_requirements=[])

    # 4) Extract code
    code_field = data.get("code")
    if not isinstance(code_field, str):
        # If "code" is missing or wrong type, treat candidate as code
        code_fallback = _clean_ai_code_string(stripped)
        return TargetArtifacts(code=code_fallback, python_requirements=[])

    code = _clean_ai_code_string(code_field)

    # 5) Extract python_requirements
    py_field = data.get("python_requirements", [])
    if py_field is None:
        py_field = []
    if not isinstance(py_field, list):
        py_reqs: List[str] = []
    else:
        py_reqs = [str(x).strip() for x in py_field if str(x).strip()]

    return TargetArtifacts(code=code, python_requirements=py_reqs)


def generate_target_artifacts_from_ai(
    *,
    repo_url: str,
    revision: str,
    file_path: str,
    behavior: Behavior,
    contract: Optional[BehaviorContract],
    source_language: str,
    target_language: str,
) -> TargetArtifacts:
    """
    High-level helper:

    - Fetch source code from GitHub
    - Build a conversion prompt (with file-path context)
    - Call selected AI provider
    - Parse provider response into TargetArtifacts

    For Gemini (google), we expect JSON:

        {
          "code": "<full target-language source>",
          "python_requirements": ["matplotlib", ...]
        }

    If parsing fails, we fall back to treating the full response text as code.
    For OpenAI, we currently treat the entire response as plain code.
    """
    source_code = _fetch_source_code_from_github(
        repo_url=repo_url,
        revision=revision,
        file_path=file_path,
    )

    prompt = _build_conversion_prompt(
        source_code=source_code,
        behavior=behavior,
        contract=contract,
        source_language=source_language,
        target_language=target_language,
        file_path=file_path,
    )

    if settings.AI_PROVIDER == "google":
        raw = _call_google_conversion(prompt)
        # Let the helper handle JSON vs fallback
        return _extract_google_artifacts(raw)

    elif settings.AI_PROVIDER == "openai":
        # For now we treat OpenAI as "code-only".
        code = _call_openai_conversion(prompt)
        code = _clean_ai_code_string(code)
        return TargetArtifacts(code=code, python_requirements=[])

    else:
        raise AIConversionError(f"Unsupported AI_PROVIDER: {settings.AI_PROVIDER}")


def generate_target_code_from_ai(
    *,
    repo_url: str,
    revision: str,
    file_path: str,
    behavior: Behavior,
    contract: Optional[BehaviorContract],
    source_language: str,
    target_language: str,
) -> str:
    """
    Backwards-compatible wrapper that returns ONLY the generated code.

    Newer callers should use generate_target_artifacts_from_ai instead.
    """
    artifacts = generate_target_artifacts_from_ai(
        repo_url=repo_url,
        revision=revision,
        file_path=file_path,
        behavior=behavior,
        contract=contract,
        source_language=source_language,
        target_language=target_language,
    )
    return artifacts.code
