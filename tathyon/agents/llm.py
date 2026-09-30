"""Gemini planner/composer for the bounded agents (Google GenAI SDK).

Activated only when GEMINI_API_KEY is set on the SERVER. The key is never sent
to the browser, logged, or written to the ledger. Automatic function calling is
disabled: Gemini may only *name* a tool; the harness in base.py validates and
executes it. What Gemini receives is the agent's tool results for this
workspace (facility ids, quantities, event ids) — the same data the UI shows.
No patient data exists in TATHYON.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-flash-lite-latest"
REQUEST_TIMEOUT_MS = 20_000
_JSON_RE = re.compile(r"\{.*\}", re.S)
MAX_OUTPUT_TOKENS = 4096
THINKING_BUDGET = 512
REPAIR_PROMPT = ("Respond now with ONLY the JSON object {\"answer\": str, \"statements\": "
                 "[{\"text\": str, \"event_ids\": [\"evt_...\"]}]} using event ids from the tool results above.")


def _parse_json(parts: list) -> Optional[dict]:
    text = "".join(p.text or "" for p in parts if not getattr(p, "thought", False))
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def gemini_status() -> dict:
    key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    return {"configured": key, "model": os.environ.get("GEMINI_MODEL", DEFAULT_MODEL) if key else None,
            "fallback_model": os.environ.get("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL) if key else None,
            "state": "CONFIGURED" if key else "NOT_CONFIGURED",
            "fallback": "Deterministic tool planner and template composer (labelled DETERMINISTIC)."}


class GeminiClient:
    def __init__(self, api_key: str, model: Optional[str] = None):
        from google import genai
        from google.genai import types
        self._types = types
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = genai.Client(api_key=api_key,
                                    http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

    def _declarations(self, tools: list) -> list:
        t = self._types
        return [t.Tool(function_declarations=[
            t.FunctionDeclaration(name=tool.name, description=tool.description,
                                  parameters_json_schema=tool.args.model_json_schema())
            for tool in tools])]

    def _generate(self, contents, config):
        resp, self.model = _generate_with_fallback(self._client, self.model, contents, config)
        return resp

    def next_step(self, system: str, request: str, history: list[dict], tools: list) -> dict:
        t = self._types
        contents: list[Any] = [t.Content(role="user", parts=[t.Part(text=request)])]
        for h in history:
            if h.get("raw") is not None:
                contents.append(h["raw"])  # the model's own turn, incl. thought signatures
            else:
                contents.append(t.Content(role="model", parts=[t.Part(function_call=t.FunctionCall(
                    name=h["name"], args=h["args"]))]))
            contents.append(t.Content(role="user", parts=[t.Part(function_response=t.FunctionResponse(
                name=h["name"], response={"result": json.loads(json.dumps(h["result"], default=str))}))]))
        config = t.GenerateContentConfig(
            system_instruction=system, temperature=0.0, tools=self._declarations(tools),
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=MAX_OUTPUT_TOKENS, thinking_config=self._thinking())
        resp = self._generate(contents, config)
        cand = (resp.candidates or [None])[0]
        parts = (cand.content.parts if cand and cand.content else None) or []
        for part in parts:
            if part.function_call:
                return {"type": "tool_call", "name": part.function_call.name,
                        "args": dict(part.function_call.args or {}), "raw": cand.content}
        parsed = _parse_json(parts)
        if parsed is not None:
            return {"type": "final", "output": parsed}
        # One repair turn: ask for the JSON object only, with no tools offered.
        if cand and cand.content:
            contents.append(cand.content)
        contents.append(t.Content(role="user", parts=[t.Part(text=REPAIR_PROMPT)]))
        repair = self._generate(contents, t.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json",
            max_output_tokens=MAX_OUTPUT_TOKENS, thinking_config=self._thinking()))
        rc = (repair.candidates or [None])[0]
        parsed = _parse_json((rc.content.parts if rc and rc.content else None) or [])
        return {"type": "final", "output": parsed} if parsed is not None else {"type": "invalid"}

    def _thinking(self):
        if "2.5" not in self.model:
            return None
        return self._types.ThinkingConfig(thinking_budget=THINKING_BUDGET)


def _generate_with_fallback(client, model: str, contents, config) -> tuple[Any, str]:
    """Try the configured model; on 404 (retired/unavailable for this key) use the fallback once."""
    try:
        return client.models.generate_content(model=model, contents=contents, config=config), model
    except Exception as exc:
        fallback = os.environ.get("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
        if "404" not in str(exc) or model == fallback:
            raise
        return client.models.generate_content(model=fallback, contents=contents, config=config), fallback


def default_llm() -> Optional[GeminiClient]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        return GeminiClient(key)
    except Exception:
        return None


TRANSCRIBE_PROMPT = """Transcribe a photographed stock count sheet from a public health facility.
Rules: transcribe ONLY values a human wrote. Do NOT count objects in the photo. If a value is
unclear or absent, return null with confidence 0. Return JSON:
{"fields": {"facility_id": str|null, "sku": str|null, "present_qty": number|null,
 "usable_qty": number|null, "expired_qty": number|null, "counted_by": str|null},
 "confidences": {<same keys>: number between 0 and 1}}"""


def default_vision():
    """Callable(image_bytes, mime) -> transcription dict, or None when Gemini is not configured."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None

    def transcribe(image: bytes, mime: str) -> dict:
        from google import genai
        from google.genai import types
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        resp, model = _generate_with_fallback(
            client, model, [types.Part.from_bytes(data=image, mime_type=mime), TRANSCRIBE_PROMPT],
            types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0))
        data = json.loads(resp.text or "{}")
        return {"fields": data.get("fields", {}), "confidences": data.get("confidences", {}), "model": model}

    return transcribe
