from __future__ import annotations

from typing import Optional

import httpx

from . import gmail_client, settings

XAI_URL = "https://api.x.ai/v1/chat/completions"
FALLBACK_MODELS = ("grok-4", "grok-4-latest", "grok-3")


class GrokError(RuntimeError):
    pass


def _require_key() -> str:
    key = settings.grok_api_key()
    if not key:
        raise GrokError("Add a Grok API key in the AI desk first.")
    return key


def _letter_context(message_id: Optional[str]) -> str:
    if not message_id:
        return ""
    message = gmail_client.get_message(message_id)
    chunks = []
    for item in message.get("thread") or [message]:
        sender = item.get("from") or {}
        body = (item.get("body") or {}).get("text") or item.get("snippet") or ""
        chunks.append(
            f"Subject: {item.get('subject')}\n"
            f"From: {sender.get('name')} <{sender.get('email')}>\n"
            f"Date: {(item.get('date') or {}).get('label')}\n\n"
            f"{body.strip()}"
        )
    return "\n\n----------\n\n".join(chunks).strip()[:16000]


def chat(
    system: str,
    user: str,
    temperature: float = 0.3,
    timeout: float = 90.0,
    json_mode: bool = False,
) -> str:
    key = _require_key()
    models = [settings.grok_model()]
    for name in FALLBACK_MODELS:
        if name not in models:
            models.append(name)

    last_error = "Grok request failed."
    for model in models:
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        attempts = [True, False] if json_mode else [False]
        for use_json in attempts:
            body = dict(payload)
            if use_json:
                body["response_format"] = {"type": "json_object"}
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        XAI_URL,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                if response.status_code == 401:
                    raise GrokError("Grok rejected the API key. Check it in the AI desk.")
                if response.status_code in (400, 404) and "model" in response.text.lower():
                    last_error = f"Model {model} is not available."
                    break
                if use_json and response.status_code == 400:
                    last_error = "Grok JSON mode was rejected."
                    continue
                if response.status_code >= 400:
                    last_error = f"Grok returned {response.status_code}."
                    break
                data = response.json()
                text = (
                    (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                    or ""
                ).strip()
                if text:
                    if model != settings.grok_model():
                        settings.save_settings({"grok_model": model})
                    return text
                last_error = "Grok returned an empty reply."
            except GrokError:
                raise
            except httpx.HTTPError as exc:
                last_error = f"Could not reach xAI: {exc}"
                break
    raise GrokError(last_error)


def complete(question: str, message_id: Optional[str] = None) -> str:
    context = _letter_context(message_id)
    user = question.strip()
    if context:
        user = (
            "Here is an email. Answer using only this letter.\n\n"
            f"{context}\n\n---\n{question.strip()}"
        )
    system = (
        "You are Grok, reading mail in Adeus. Be concise, clear, and a little dry. "
        "Do not invent senders, dates, or promises that are not in the letter."
    )
    return chat(system, user, temperature=0.3)


def summarize(message_id: str) -> str:
    return complete(
        "Summarize this letter in 4 short bullets: who it is from, what they want, "
        "any dates or deadlines, and what action is needed.",
        message_id=message_id,
    )
