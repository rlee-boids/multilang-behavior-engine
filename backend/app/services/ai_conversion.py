from __future__ import annotations

from textwrap import dedent
from typing import Optional

import httpx
from google import genai
from openai import OpenAI

from app.core.config import settings
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract


class AIConversionError(Exception):
    pass


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
    *,
    file_path: Optional[str] = None,
) -> str:
    """
    Build a prompt instructing the model to do a direct-ish translation
    from the source language to the target language.

    Now slightly UI-aware:
    - If this looks like a CGI / UI entrypoint and target is Python,
      we give extra guidance to produce idiomatic Python web code.
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
        ).strip()

    behavior_desc = behavior.description or behavior.name

    # --- UI-aware hinting ----------------------------------------------------
    ui_hint = ""
    is_ui_like = False

    if file_path:
        lower_path = file_path.lower()
        # Heuristics for "this is probably a UI/CGI entrypoint"
        if (
            "cgi-bin" in lower_path
            or lower_path.endswith(".cgi")
            or "plot_ui" in lower_path
        ):
            is_ui_like = True

    # If Behavior has a domain field like "ui", "web", "cgi", we can also use it.
    behavior_domain = getattr(behavior, "domain", None)
    if behavior_domain and isinstance(behavior_domain, str):
        if behavior_domain.lower() in {"ui", "web", "cgi", "frontend"}:
            is_ui_like = True

    if target_language.lower() == "python" and is_ui_like:
        ui_hint = dedent(
            """
            Additional requirements for Python UI / web conversion:

            - The source is a CGI-style or UI entrypoint. Convert it into idiomatic Python web code
              instead of doing a literal CGI translation.
            - Prefer a lightweight web style (for example, FastAPI or a simple WSGI/ASGI app)
              over raw CGI.
            - Separate concerns:
                * Keep core business / plotting logic in reusable functions or modules.
                * The web layer should:
                    - parse incoming HTTP parameters or JSON
                    - call the reusable logic
                    - return an HTTP response (HTML, JSON, or binary image) with correct headers.
            - Expose a clear application entrypoint that a server can use:
                * For ASGI: provide `app = FastAPI()` (or similar) as the main application object.
                * For WSGI: provide `application = ...`.
            - Preserve the incoming parameters and behavior of the original CGI endpoint
              (names and semantics), but DO NOT be constrained to CGI environment variables.
            - Assume the converted file will live in a Python project and be run inside a container.
            """
        ).strip()

    # --- Base prompt ---------------------------------------------------------
    prompt = f"""
    You are a senior software engineer who is an expert in both {source_language} and {target_language}.

    Your task is to convert the following {source_language} code into an idiomatic {target_language} module
    that preserves the behavior as closely as possible.

    Behavior description:
    {behavior_desc}

    {contract_snippet}

    {ui_hint}

    Requirements:
    - Maintain the same high-level functionality and behavior.
    - Preserve function names and signatures where it makes sense.
    - If the original defines a module/class/namespace, reflect that appropriately in {target_language}.
    - Do NOT include placeholders like "TODO" or "NotImplementedError".
    - Do NOT include any explanation or commentary outside of comments in the code.
    - Return ONLY the final {target_language} code, no markdown, no prose, no triple backticks.

    Here is the full {source_language} source to convert:

    {source_code}
    """

    return dedent(prompt).strip()


def _call_google_conversion(prompt: str) -> str:
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
    High-level helper:

    - Fetch source code from GitHub
    - Build a conversion prompt (UI-aware when appropriate)
    - Call selected AI provider
    - Return generated target-language code
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
        file_path=file_path,  # <-- lets the prompt detect CGI / UI entrypoints
    )

    if settings.AI_PROVIDER == "google":
        return _call_google_conversion(prompt)
    elif settings.AI_PROVIDER == "openai":
        return _call_openai_conversion(prompt)
    else:
        raise AIConversionError(f"Unsupported AI_PROVIDER: {settings.AI_PROVIDER}")
