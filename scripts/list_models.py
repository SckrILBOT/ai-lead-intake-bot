"""Print Gemini models available for GEMINI_API_KEY from .env."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai  # noqa: E402

from config import get_settings  # noqa: E402

client = genai.Client(api_key=get_settings().gemini_api_key)
for model in client.models.list():
    if "generateContent" in (model.supported_actions or []):
        print(f"{model.name.removeprefix('models/'):<45} {model.display_name}")
