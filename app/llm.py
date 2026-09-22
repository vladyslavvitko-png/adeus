from __future__ import annotations

from . import deepseek, grok, settings


class LlmError(RuntimeError):
    pass


def chat(
    system: str,
    user: str,
    temperature: float = 0.3,
    timeout: float = 90.0,
    json_mode: bool = False,
) -> str:
    provider = settings.preferred_llm()
    try:
        if provider == "deepseek":
            return deepseek.chat(
                system,
                user,
                temperature=temperature,
                timeout=timeout,
                json_mode=json_mode,
            )
        return grok.chat(
            system,
            user,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
        )
    except (deepseek.DeepSeekError, grok.GrokError) as exc:
        raise LlmError(str(exc)) from exc
