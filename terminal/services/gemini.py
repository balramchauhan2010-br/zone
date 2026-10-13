"""Optional Gemini explanation adapter; never supplies market numbers or signals."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from terminal.config import GEMINI_API_KEY, GEMINI_MODEL
from terminal.providers.base import ProviderError


class GeminiExplainer:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = (api_key if api_key is not None else GEMINI_API_KEY).strip()
        self.model = (model or GEMINI_MODEL).strip()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def status(self) -> dict[str, Any]:
        return {
            "provider": "Google Gemini",
            "status": "CONFIGURED_NOT_CHECKED" if self.configured else "NOT_CONFIGURED",
            "credentials_configured": self.configured,
            "model": self.model,
            "note": "Gemini may explain only the supplied verified facts; deterministic risk filters remain authoritative.",
        }

    def explain(self, evidence: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise ProviderError("GEMINI_API_KEY is not configured", "GEMINI_KEY_MISSING")
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be a JSON object")
        text_payload = str(evidence)
        if len(text_payload) > 12000:
            raise ValueError("Evidence payload is too large (max 12,000 characters)")
        prompt = (
            "You are an explanation-only market research assistant. Explain the supplied data for an Indian trading dashboard. "
            "Use only facts and numbers explicitly present in the JSON. Never invent prices, timestamps, OI, news, confidence, or probabilities. "
            "Do not give a standalone buy/sell recommendation, do not override a NO TRADE/risk filter, and state missing/stale fields clearly. "
            "Return concise plain text with: observed evidence, disagreement/missing data, and main risk.\n\n"
            f"Verified input JSON:\n{text_payload}"
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model, safe='')}:generateContent"
        try:
            response = httpx.post(
                url,
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 350},
                },
                timeout=httpx.Timeout(35.0, connect=8.0),
            )
        except httpx.HTTPError as exc:
            raise ProviderError("Gemini service is not reachable", "GEMINI_NETWORK_ERROR", True) from exc
        if response.status_code >= 400:
            raise ProviderError(f"Gemini request failed with HTTP {response.status_code}", f"GEMINI_HTTP_{response.status_code}", response.status_code in (429, 500, 503))
        try:
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            summary = "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini response did not contain explanation text", "GEMINI_SCHEMA_ERROR") from exc
        if not summary.strip():
            raise ProviderError("Gemini returned an empty explanation", "GEMINI_EMPTY_RESPONSE")
        return {"summary": summary.strip(), "model": self.model,
                "role": "explanation_only", "numbers_source": "input evidence only"}
