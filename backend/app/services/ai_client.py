from __future__ import annotations

import asyncio
from enum import Enum

from openai import AsyncOpenAI
from google import genai

from app.core.config import settings


class AIProvider(str, Enum):
    GOOGLE = "google"
    OPENAI = "openai"


class AIClientError(Exception):
    pass


class AIClient:
    """
    Unified AI client:
      - Google GenAI via `google-genai` (from google import genai)
      - OpenAI via `openai`

    Public async API:
      - summarize_code(code_text, language)
      - suggest_contract(code_text, language)
    """

    def __init__(self) -> None:
        provider_str = (settings.AI_PROVIDER or "google").lower()
        if provider_str not in ("google", "openai"):
            raise AIClientError(f"Unsupported AI_PROVIDER: {provider_str}")

        self.provider: AIProvider = (
            AIProvider.GOOGLE if provider_str == "google" else AIProvider.OPENAI
        )

        self._google_client = None
        self._google_model_name: str | None = None
        self._openai_client: AsyncOpenAI | None = None

        if self.provider is AIProvider.GOOGLE:
            self._init_google()
        else:
            self._init_openai()

    # ------------------------------------------------------------------
    # Provider init
    # ------------------------------------------------------------------
    def _init_google(self) -> None:
        api_key = settings.GOOGLE_API_KEY
        if not api_key:
            raise AIClientError("GOOGLE_API_KEY is not set")

        # New google-genai client
        self._google_client = genai.Client(api_key=api_key)
        # Allow override from env, default to the model you showed
        self._google_model_name = settings.GOOGLE_MODEL_NAME or "gemini-3-pro-preview"

    def _init_openai(self) -> None:
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise AIClientError("OPENAI_API_KEY is not set")

        self._openai_client = AsyncOpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # Public async methods
    # ------------------------------------------------------------------
    async def summarize_code(self, code_text: str, language: str) -> str:
        if self.provider is AIProvider.GOOGLE:
            # google-genai client is sync; wrap in to_thread
            return await asyncio.to_thread(
                self._summarize_code_google, code_text, language
            )
        else:
            return await self._summarize_code_openai(code_text, language)

    async def suggest_contract(self, code_text: str, language: str) -> str:
        if self.provider is AIProvider.GOOGLE:
            return await asyncio.to_thread(
                self._suggest_contract_google, code_text, language
            )
        else:
            return await self._suggest_contract_openai(code_text, language)

    # ------------------------------------------------------------------
    # Google (new google-genai client, sync)
    # ------------------------------------------------------------------
    def _summarize_code_google(self, code_text: str, language: str) -> str:
        if not self._google_client or not self._google_model_name:
            raise AIClientError("Google client/model not initialized")

        prompt = (
            f"You are analyzing legacy {language} source code.\n\n"
            f"Provide a concise, high-level summary (3–6 sentences) of what this file does, "
            f"including its main responsibilities and any important side effects.\n\n"
            f"Return plain text without markdown headings.\n\n"
            f"--- CODE START ---\n{code_text}\n--- CODE END ---"
        )

        resp = self._google_client.models.generate_content(
            model=self._google_model_name,
            contents=prompt,
        )
        # new client returns .text
        return (getattr(resp, "text", "") or "").strip()

    def _suggest_contract_google(self, code_text: str, language: str) -> str:
        if not self._google_client or not self._google_model_name:
            raise AIClientError("Google client/model not initialized")

        prompt = (
            f"You are designing a language-agnostic behavior contract for {language} source code.\n\n"
            f"Based on the code below, describe its behavior in terms of:\n"
            f"- Inputs (parameters, expected types, constraints)\n"
            f"- Outputs (return values, side effects, files produced)\n"
            f"- Error conditions\n"
            f"- 1–3 example test cases\n\n"
            f"Return the contract as structured Markdown, and do NOT invent behavior "
            f"that is not clearly implied by the code.\n\n"
            f"--- CODE START ---\n{code_text}\n--- CODE END ---"
        )

        resp = self._google_client.models.generate_content(
            model=self._google_model_name,
            contents=prompt,
        )
        return (getattr(resp, "text", "") or "").strip()

    # ------------------------------------------------------------------
    # OpenAI (true async)
    # ------------------------------------------------------------------
    async def _summarize_code_openai(self, code_text: str, language: str) -> str:
        if not self._openai_client:
            raise AIClientError("OpenAI client not initialized")

        model_name = settings.OPENAI_MODEL_NAME or "gpt-4.1-mini"
        resp = await self._openai_client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior software engineer summarizing source code.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Language: {language}\n\n"
                        f"Provide a concise, high-level summary (3–6 sentences) of what this file does, "
                        f"including its main responsibilities and important side effects. "
                        f"Return plain text only.\n\n"
                        f"--- CODE START ---\n{code_text}\n--- CODE END ---"
                    ),
                },
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()

    async def _suggest_contract_openai(self, code_text: str, language: str) -> str:
        if not self._openai_client:
            raise AIClientError("OpenAI client not initialized")

        model_name = settings.OPENAI_MODEL_NAME or "gpt-4.1-mini"
        resp = await self._openai_client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are designing language-agnostic behavior contracts for legacy code. "
                        "You never hallucinate new behavior."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Language: {language}\n\n"
                        f"Read the code below and describe its behavior in terms of:\n"
                        f"- Inputs (parameters, expected types, constraints)\n"
                        f"- Outputs (return values, side effects, files produced)\n"
                        f"- Error conditions\n"
                        f"- 1–3 example test cases\n\n"
                        f"Return the contract as structured Markdown.\n\n"
                        f"--- CODE START ---\n{code_text}\n--- CODE END ---"
                    ),
                },
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()


_ai_client: AIClient | None = None


def get_ai_client() -> AIClient:
    global _ai_client
    if _ai_client is None:
        _ai_client = AIClient()
    return _ai_client
