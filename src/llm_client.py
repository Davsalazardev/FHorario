"""Cliente unificado de LLM para R5 y R6.

Soporta:
- Modelo local con Ollama (http://localhost:11434)
- API externa Gemini (GEMINI_API_KEY)
- API externa OpenAI-compatible (OPENAI_API_KEY)
- Deteccion limpia de disponibilidad sin bloquear cuando se corre offline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from src.r1_data.anonymizer import sanitize_text_for_llm


@dataclass
class LLMConfig:
    provider: str = "auto"  # "auto", "ollama", "gemini", "openai", "offline"
    model: str = ""
    ollama_url: str = "http://localhost:11434"
    timeout_sec: float = 45.0
    temperature: float = 0.1
    max_tokens: int | None = None


class LLMUnavailableError(RuntimeError):
    """Se lanza cuando ningun backend LLM (Ollama o API) esta accesible."""


def check_ollama_available(ollama_url: str = "http://localhost:11434", timeout: float = 1.5) -> tuple[bool, list[str]]:
    """Comprueba si el servidor local de Ollama responde y lista modelos instalados."""
    try:
        resp = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return True, models
    except Exception:
        pass
    return False, []


def detect_backend(cfg: LLMConfig | None = None) -> tuple[str, str]:
    """Detecta el mejor backend LLM disponible: (provider, model_name)."""
    c = cfg or LLMConfig()
    if c.provider == "offline":
        return "offline", "deterministic_baseline"

    if c.provider in ("auto", "ollama"):
        ok, models = check_ollama_available(c.ollama_url)
        if ok and models:
            return "ollama", c.model or models[0]
        if c.provider == "ollama":
            return "offline", "ollama_not_running"

    if c.provider in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY"):
        return "gemini", c.model or "gemini-2.5-flash"

    if c.provider in ("auto", "openai") and os.environ.get("OPENAI_API_KEY"):
        return "openai", c.model or "gpt-4o-mini"

    return "offline", "no_api_key_or_ollama"


def call_llm_text(
    system_prompt: str,
    user_prompt: str,
    cfg: LLMConfig | None = None,
    expect_json: bool = False,
) -> tuple[str, str]:
    """Invoca al LLM configurado (Ollama o API). Retorna (respuesta_texto, backend_label).

    Sanitiza el texto antes de enviarlo a una API externa (regla de etica y privacidad).
    """
    c = cfg or LLMConfig()
    provider, model_name = detect_backend(c)

    if provider == "offline":
        raise LLMUnavailableError(f"LLM no disponible ({model_name}).")

    if provider == "ollama":
        opts: dict[str, Any] = {"temperature": c.temperature}
        if c.max_tokens is not None:
            opts["num_predict"] = c.max_tokens
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": opts,
        }
        if expect_json:
            payload["format"] = "json"
        resp = requests.post(
            f"{c.ollama_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=c.timeout_sec,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        return text, f"ollama:{model_name}"

    # Para APIs externas, sanitizar posibles datos personales (correos/telefonos)
    safe_user = sanitize_text_for_llm(user_prompt)

    if provider == "gemini":
        api_key = os.environ["GEMINI_API_KEY"]
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        gen_cfg: dict[str, Any] = {"temperature": c.temperature}
        if expect_json:
            gen_cfg["responseMimeType"] = "application/json"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": safe_user}]}],
            "generationConfig": gen_cfg,
        }
        resp = requests.post(url, json=payload, timeout=c.timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text, f"gemini:{model_name}"

    if provider == "openai":
        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        payload = {
            "model": model_name,
            "temperature": c.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": safe_user},
            ],
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=c.timeout_sec,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return text, f"openai:{model_name}"

    raise LLMUnavailableError(f"Proveedor desconocido: {provider}")


def parse_json_from_llm(raw_text: str) -> Any:
    """Extrae y parsea JSON incluso si el LLM lo envuelve en bloques ```json ... ```."""
    txt = raw_text.strip()
    if txt.startswith("```"):
        lines = txt.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        txt = "\n".join(lines).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # Intentar recortar del primer '{' o '[' al ultimo '}' o ']'
        start_obj = txt.find("{")
        start_arr = txt.find("[")
        starts = [i for i in (start_obj, start_arr) if i != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(txt.rfind("}"), txt.rfind("]"))
        return json.loads(txt[start : end + 1])
