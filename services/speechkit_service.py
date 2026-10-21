from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"


class SpeechKitError(RuntimeError):
    pass


class SpeechKitService:
    """Speech-to-text via Yandex SpeechKit (synchronous API v1: up to 30 s / 1 MB).

    Telegram voice messages are OGG/Opus, which the API accepts as-is (`oggopus`).
    """

    def __init__(self, api_key: str, lang: str = "ru-RU", url: str = STT_URL) -> None:
        self._api_key = api_key
        self._lang = lang
        self._url = url

    async def transcribe(self, audio: bytes) -> str:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self._url,
                params={"lang": self._lang, "format": "oggopus"},
                headers={"Authorization": f"Api-Key {self._api_key}"},
                data=audio,
            ) as response:
                body = await response.text()
                if response.status != 200:
                    raise SpeechKitError(f"SpeechKit HTTP {response.status}: {body[:300]}")
                payload = await response.json(content_type=None)
        return str(payload.get("result", "")).strip()
