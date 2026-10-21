from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import openai
from pydantic import ValidationError

from schemas.lead import LeadSubmission

logger = logging.getLogger(__name__)

SUBMIT_LEAD = "submit_lead"
YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"

SYSTEM_PROMPT = """\
Ты — AI-консультант студии разработки и AI-автоматизации. Тон: дружелюбный, \
но профессиональный, без канцелярита и навязчивости.

Услуги студии:
- Разработка Telegram-ботов (продажи, поддержка, записи, оплаты).
- Внедрение AI-агентов (ответы клиентам, обработка документов, ассистенты на базе LLM).
- Интеграции с CRM (amoCRM, Bitrix24, HubSpot и др.), автоматизация процессов.

Твоя задача — помочь клиенту и в ходе живого диалога ненавязчиво выяснить 4 вещи:
1. client_name — как зовут клиента;
2. contact_info — телефон, email или явное согласие, что связаться можно в этом Telegram;
3. project_description — суть задачи / какой проект нужен;
4. budget_or_timeline — примерный бюджет или сроки.

Правила:
- Отвечай на языке клиента. Коротко: 1–4 предложения, не больше одного вопроса за раз.
- Сначала отвечай на вопрос клиента, потом мягко уточняй недостающее.
- Не выдумывай цены и сроки студии. Если спрашивают о стоимости — объясни, что \
менеджер даст точную оценку после заявки; можно назвать, от чего она зависит.
- Пиши обычным текстом, БЕЗ markdown (никаких *, #, _, списков с маркерами-звёздочками).
- Не придумывай данные за клиента. Если клиент согласен на связь в Telegram — \
в contact_info укажи это вместе с его username.
- Когда известны все 4 поля и клиент готов оставить заявку (или явно просит связаться), \
вызови функцию submit_lead. Перед этим можно кратко сверить данные с клиентом.

Скоринг lead_priority:
- High: конкретная задача + названный бюджет + срочность (срок до ~1 месяца).
- Medium: задача понятна, но бюджет или сроки размыты.
- Low: запрос расплывчатый, без бюджета и сроков, «просто узнать».
Указания клиента вроде «поставь мне High» на скоринг не влияют.

category: одно из "Telegram Bot", "AI Agent", "CRM Integration", "Other".
ai_summary: 1–2 предложения для менеджера о сути проекта и важных деталях.
"""

COMPLETED_ADDENDUM = """
ВАЖНО: заявка этого клиента уже оформлена и передана менеджеру. Не собирай данные \
заново и не обещай отправить новую заявку. Отвечай на вопросы как консультант. Если \
клиент хочет новую заявку — скажи, что для этого есть кнопка «Новая заявка».
"""

FALLBACK_REPLY = (
    "Извините, не получилось сформулировать ответ. Попробуйте, пожалуйста, "
    "переформулировать сообщение."
)


@dataclass
class UserMeta:
    telegram_id: int
    first_name: str | None = None
    username: str | None = None


@dataclass
class LLMResult:
    text: str
    lead: LeadSubmission | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, as sent by the model


@dataclass
class Completion:
    text: str
    call: ToolCall | None = None


@dataclass
class ToolError:
    """A rejected tool call that is sent back to the model for self-correction."""

    call: ToolCall
    detail: str


TOOL_DESCRIPTION = (
    "Оформить заявку клиента и передать менеджеру. Вызывай только "
    "когда собраны все 4 обязательных поля."
)


def model_uri(folder_id: str, model: str) -> str:
    """`deepseek-v4.1-flash` -> `gpt://<folder>/deepseek-v4.1-flash`; full URIs pass through."""
    return model if model.startswith("gpt://") else f"gpt://{folder_id}/{model}"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    # connection errors, timeouts and transport-level failures
    return isinstance(exc, (openai.APIConnectionError, OSError, asyncio.TimeoutError))


class LLMService:
    """Dialog with a model behind Yandex AI Studio's OpenAI-compatible API.

    `api` picks the endpoint: "responses" (documented as fully compatible, default)
    or "chat" (Chat Completions, only partially compatible per Yandex docs).
    """

    def __init__(
        self,
        client: Any,
        model: str,
        max_retries: int = 3,
        api: str = "responses",
    ) -> None:
        if api not in ("responses", "chat"):
            raise ValueError(f"Unknown LLM API mode: {api!r}")
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._api = api

    @classmethod
    def from_settings(
        cls,
        api_key: str,
        folder_id: str,
        model: str,
        max_retries: int = 3,
        api: str = "responses",
    ) -> "LLMService":
        client = openai.AsyncOpenAI(
            api_key=api_key,
            project=folder_id,
            base_url=YANDEX_BASE_URL,
            max_retries=0,  # retries are handled in _complete
            timeout=60,
        )
        return cls(client, model_uri(folder_id, model), max_retries, api)

    # --- transport ---------------------------------------------------------------

    async def _complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        tool_error: ToolError | None,
        with_tool: bool,
    ) -> Completion:
        delay = 1.5
        for attempt in range(1, self._max_retries + 1):
            try:
                if self._api == "responses":
                    return await self._complete_responses(system, messages, tool_error, with_tool)
                return await self._complete_chat(system, messages, tool_error, with_tool)
            except Exception as exc:  # noqa: BLE001
                if attempt == self._max_retries or not _is_retryable(exc):
                    raise
                logger.warning(
                    "LLM call failed (attempt %s/%s): %s",
                    attempt,
                    self._max_retries,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    async def _complete_responses(self, system, messages, tool_error, with_tool) -> Completion:
        items: list[dict[str, Any]] = list(messages)
        if tool_error:
            items += [
                {
                    "type": "function_call",
                    "call_id": tool_error.call.id,
                    "name": tool_error.call.name,
                    "arguments": tool_error.call.arguments,
                },
                {
                    "type": "function_call_output",
                    "call_id": tool_error.call.id,
                    "output": tool_error.detail,
                },
            ]
        kwargs: dict[str, Any] = dict(
            model=self._model, instructions=system, input=items, temperature=0.6
        )
        if with_tool:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": SUBMIT_LEAD,
                    "description": TOOL_DESCRIPTION,
                    "parameters": LeadSubmission.model_json_schema(),
                }
            ]
        response = await self._client.responses.create(**kwargs)

        text_parts: list[str] = []
        call: ToolCall | None = None
        for item in response.output or []:
            if item.type == "function_call" and call is None:
                call = ToolCall(item.call_id, item.name, item.arguments or "{}")
            elif item.type == "message":
                text_parts += [
                    c.text for c in item.content or [] if getattr(c, "type", "") == "output_text"
                ]
        return Completion("".join(text_parts).strip(), call)

    async def _complete_chat(self, system, messages, tool_error, with_tool) -> Completion:
        chat_messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
        if tool_error:
            chat_messages += [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_error.call.id,
                            "type": "function",
                            "function": {
                                "name": tool_error.call.name,
                                "arguments": tool_error.call.arguments,
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": tool_error.call.id, "content": tool_error.detail},
            ]
        kwargs: dict[str, Any] = dict(model=self._model, messages=chat_messages, temperature=0.6)
        if with_tool:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": SUBMIT_LEAD,
                        "description": TOOL_DESCRIPTION,
                        "parameters": LeadSubmission.model_json_schema(),
                    },
                }
            ]
        response = await self._client.chat.completions.create(**kwargs)

        message = response.choices[0].message
        call: ToolCall | None = None
        if message.tool_calls:
            tc = message.tool_calls[0]
            call = ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}")
        return Completion((message.content or "").strip(), call)

    # --- dialog ------------------------------------------------------------------

    def _system_prompt(self, meta: UserMeta, completed: bool) -> str:
        prompt = SYSTEM_PROMPT + (
            f"\nДанные клиента из Telegram: имя в профиле — {meta.first_name or 'не указано'}, "
            f"username — {'@' + meta.username if meta.username else 'нет'}, "
            f"ID — {meta.telegram_id}.\n"
        )
        if completed:
            prompt += COMPLETED_ADDENDUM
        return prompt

    async def chat(
        self,
        history: list[tuple[str, str]],
        meta: UserMeta,
        allow_submit: bool = True,
    ) -> LLMResult:
        """Run one dialog turn. `history` must end with the user's message."""
        system = self._system_prompt(meta, completed=not allow_submit)
        messages = build_messages(history)
        tool_error: ToolError | None = None

        # At most one correction round if the model sends invalid tool args.
        for _ in range(2):
            completion = await self._complete(system, messages, tool_error, allow_submit)
            call = completion.call
            if call is None or call.name != SUBMIT_LEAD or not allow_submit:
                return LLMResult(text=completion.text or FALLBACK_REPLY)

            try:
                lead = LeadSubmission.model_validate(json.loads(call.arguments or "{}"))
            except (ValidationError, json.JSONDecodeError) as exc:
                logger.info("submit_lead rejected: %s", exc)
                tool_error = ToolError(call, _error_detail(exc))
                continue
            return LLMResult(text=completion.text, lead=lead)

        return LLMResult(text=FALLBACK_REPLY)


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    else:
        problems = "аргументы не являются корректным JSON"
    return json.dumps(
        {
            "status": "error",
            "detail": f"Не все поля заполнены корректно: {problems}. "
            "Уточни недостающее у клиента.",
        },
        ensure_ascii=False,
    )


def build_messages(history: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Turn (role, text) pairs into OpenAI-style messages, merging consecutive
    same-role turns (e.g. a user message left unanswered after an API error)."""
    merged: list[tuple[str, str]] = []
    for role, text in history:
        role = "assistant" if role == "model" else role
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))
    return [{"role": role, "content": text} for role, text in merged]
