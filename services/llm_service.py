from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from schemas.lead import LeadSubmission

logger = logging.getLogger(__name__)

SUBMIT_LEAD = "submit_lead"

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

TRANSCRIBE_PROMPT = (
    "Дословно транскрибируй это голосовое сообщение на языке оригинала. "
    "Верни только текст сообщения, без пояснений и кавычек. "
    "Если речи нет или она неразборчива — верни пустую строку."
)

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


def _submit_lead_tool() -> types.Tool:
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=SUBMIT_LEAD,
                description=(
                    "Оформить заявку клиента и передать менеджеру. Вызывай только "
                    "когда собраны все 4 обязательных поля."
                ),
                parameters_json_schema=LeadSubmission.model_json_schema(),
            )
        ]
    )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, errors.ClientError):
        return exc.code == 429
    if isinstance(exc, errors.APIError):
        return True  # 5xx
    # transport-level failures (SSL, disconnects, timeouts)
    return isinstance(exc, (OSError, asyncio.TimeoutError)) or "disconnect" in str(
        exc
    ).lower()


class LLMService:
    def __init__(
        self, client: genai.Client, model: str, max_retries: int = 3
    ) -> None:
        self._client = client
        self._model = model
        self._max_retries = max_retries

    @classmethod
    def from_api_key(cls, api_key: str, model: str, max_retries: int = 3) -> "LLMService":
        return cls(genai.Client(api_key=api_key), model, max_retries)

    async def _generate(
        self, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        delay = 1.5
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._client.aio.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001
                if attempt == self._max_retries or not _is_retryable(exc):
                    raise
                logger.warning(
                    "Gemini call failed (attempt %s/%s): %s",
                    attempt,
                    self._max_retries,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    async def transcribe(self, audio: bytes, mime_type: str = "audio/ogg") -> str:
        response = await self._generate(
            [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=audio, mime_type=mime_type),
                        types.Part.from_text(text=TRANSCRIBE_PROMPT),
                    ],
                )
            ],
            types.GenerateContentConfig(temperature=0.0),
        )
        return (response.text or "").strip()

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
        contents = build_contents(history)
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt(meta, completed=not allow_submit),
            temperature=0.6,
            tools=[_submit_lead_tool()] if allow_submit else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        # At most one correction round if the model sends invalid tool args.
        for _ in range(2):
            response = await self._generate(contents, config)
            calls = [c for c in (response.function_calls or []) if c.name == SUBMIT_LEAD]
            if not calls or not allow_submit:
                return LLMResult(text=_text_of(response) or FALLBACK_REPLY)

            call = calls[0]
            try:
                lead = LeadSubmission.model_validate(call.args or {})
            except ValidationError as exc:
                logger.info("submit_lead rejected: %s", exc.errors(include_url=False))
                contents.append(response.candidates[0].content)
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=SUBMIT_LEAD,
                                response={
                                    "status": "error",
                                    "detail": "Не все поля заполнены корректно: "
                                    + "; ".join(
                                        f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                        for e in exc.errors()
                                    )
                                    + ". Уточни недостающее у клиента.",
                                },
                            )
                        ],
                    )
                )
                continue
            return LLMResult(text=_text_of(response), lead=lead)

        return LLMResult(text=FALLBACK_REPLY)


def build_contents(history: list[tuple[str, str]]) -> list[types.Content]:
    """Turn (role, text) pairs into Gemini contents, merging consecutive same-role
    turns (e.g. a user message left unanswered after an API error)."""
    merged: list[tuple[str, str]] = []
    for role, text in history:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))
    return [
        types.Content(role=role, parts=[types.Part.from_text(text=text)])
        for role, text in merged
    ]


def _text_of(response: types.GenerateContentResponse) -> str:
    """Concatenate text parts without triggering SDK warnings on tool calls."""
    if not response.candidates or not response.candidates[0].content:
        return ""
    parts = response.candidates[0].content.parts or []
    return "".join(p.text for p in parts if p.text and not p.thought).strip()
