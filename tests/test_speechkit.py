import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from services.speechkit_service import SpeechKitError, SpeechKitService


async def run_service(handler, audio: bytes = b"OggS..."):
    app = web.Application()
    app.router.add_post("/stt", handler)
    async with TestServer(app) as server:
        service = SpeechKitService("secret-key", url=str(server.make_url("/stt")))
        return await service.transcribe(audio)


async def test_transcribe_sends_audio_and_auth():
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["query"] = dict(request.query)
        seen["body"] = await request.read()
        return web.json_response({"result": " Нужен бот для салона "})

    text = await run_service(handler)

    assert text == "Нужен бот для салона"
    assert seen["auth"] == "Api-Key secret-key"
    assert seen["query"] == {"lang": "ru-RU", "format": "oggopus"}
    assert seen["body"] == b"OggS..."


async def test_empty_speech_gives_empty_string():
    async def handler(request):
        return web.json_response({"result": ""})

    assert await run_service(handler) == ""


async def test_http_error_raises():
    async def handler(request):
        return web.Response(status=403, text="no access")

    with pytest.raises(SpeechKitError, match="403"):
        await run_service(handler)
