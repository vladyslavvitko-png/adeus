from __future__ import annotations

from typing import Optional

import httpx

from . import gmail_client, settings

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
FALLBACK_MODELS = ("deepseek-chat", "deepseek-reasoner")


class DeepSeekError(RuntimeError):
    pass


def _require_key() -> str:
    key = settings.deepseek_api_key()
    if not key:
        raise DeepSeekError("Add a DeepSeek API key in the AI desk first.")
    return key


def chat(
    system: str,
    user: str,
    temperature: float = 0.3,
    timeout: float = 90.0,
    json_mode: bool = False,
) -> str:
    key = _require_key()
    models = [settings.deepseek_model()]
    for name in FALLBACK_MODELS:
        if name not in models:
            models.append(name)

    last_error = "DeepSeek request failed."
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
                        DEEPSEEK_URL,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                if response.status_code == 401:
                    raise DeepSeekError("DeepSeek rejected the API key. Check it in the AI desk.")
                if response.status_code in (400, 404) and "model" in response.text.lower():
                    last_error = f"Model {model} is not available."
                    break
                if use_json and response.status_code == 400:
                    last_error = "DeepSeek JSON mode was rejected."
                    continue
                if response.status_code >= 400:
                    last_error = f"DeepSeek returned {response.status_code}."
                    break
                data = response.json()
                text = (
                    (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                    or ""
                ).strip()
                if text:
                    if model != settings.deepseek_model():
                        settings.save_settings({"deepseek_model": model})
                    return text
                last_error = "DeepSeek returned an empty reply."
            except DeepSeekError:
                raise
            except httpx.HTTPError as exc:
                last_error = f"Could not reach DeepSeek: {exc}"
                break
    raise DeepSeekError(last_error)


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


def complete(question: str, message_id: Optional[str] = None) -> str:
    context = _letter_context(message_id)
    user = question.strip()
    if context:
        user = (
            "Here is an email. Answer using only this letter.\n\n"
            f"{context}\n\n---\n{question.strip()}"
        )
    system = (
        "You are DeepSeek, reading mail in Adeus. Be concise and exact. "
        "Do not invent senders, dates, or promises that are not in the letter."
    )
    return chat(system, user, temperature=0.3)


def summarize(message_id: str) -> str:
    return complete(
        "Summarize this letter in 4 short bullets: who it is from, what they want, "
        "any dates or deadlines, and what action is needed.",
        message_id=message_id,
    )
