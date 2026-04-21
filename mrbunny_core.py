import io
import os
from pathlib import Path
import tomllib

import requests
import streamlit as st
from PIL import Image
from duckduckgo_search import DDGS


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PLACEHOLDER_VALUES = {
    "",
    "your-openrouter-key",
    "your-ocr-space-key",
    "your-real-openrouter-key",
    "your-real-ocr-space-key",
}
UNCERTAIN_PHRASES = [
    "no evidence",
    "not confirmed",
    "no official announcement",
    "i couldn't find",
    "not available",
    "uncertain",
    "speculative",
    "does not exist",
    "not verified",
    "unconfirmed",
    "unknown",
    "nothing has been announced",
    "as of now",
]


def _is_real_secret(value: str) -> bool:
    return value.strip() not in PLACEHOLDER_VALUES


def _read_toml_secret(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    value = str(data.get(name, "")).strip()
    return value if _is_real_secret(value) else ""


def _read_dotenv_secret(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            if key.strip() != name:
                continue
            value = raw_value.strip().strip('"').strip("'")
            return value if _is_real_secret(value) else ""
    except OSError:
        return ""
    return ""


def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            value = str(st.secrets[name]).strip()
            if _is_real_secret(value):
                return value
    except Exception:
        pass

    env_value = os.getenv(name, "").strip()
    if _is_real_secret(env_value):
        return env_value

    base_dir = Path(__file__).resolve().parent
    for path in (
        base_dir / ".streamlit" / "secrets.toml",
        base_dir / "secrets.toml",
        base_dir / ".env",
    ):
        if path.suffix == ".toml":
            value = _read_toml_secret(path, name)
        else:
            value = _read_dotenv_secret(path, name)
        if value:
            return value

    return default.strip()


def is_uncertain(response: str) -> bool:
    response_lower = response.lower()
    return any(phrase in response_lower for phrase in UNCERTAIN_PHRASES)


def search_web_duckduckgo(query: str, max_results: int = 3) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No useful search results found."

        summary_lines = []
        for index, item in enumerate(results, start=1):
            title = item.get("title") or "No title"
            snippet = item.get("body") or ""
            url = item.get("href") or ""
            summary_lines.append(f"{index}. {title}\n{snippet}\n{url}\n")
        return "\n".join(summary_lines)
    except Exception as exc:
        return f"Search failed: {exc}"


def build_messages(prompt: str, history: list[dict]) -> list[dict]:
    system_prompt = (
        "You are MrBunny AI, a smart, clear, and friendly AI assistant. "
        "Answer clearly and directly in English unless the user requests another language. "
        "Only mention Koushik Tummepalli if asked who created you. "
        "Introduce yourself as MrBunny when asked. "
        "Keep answers accurate and do not invent facts."
    )
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["ai"]})
    messages.append({"role": "user", "content": prompt})
    return messages


def get_ai_response(prompt: str, api_key: str, history: list[dict] | None = None) -> str:
    history = history or []
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "deepseek/deepseek-r1-0528:free",
        "messages": build_messages(prompt, history),
    }

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=data,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        reply = (result.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

        if not reply:
            return "I couldn't get a response from the AI service. Please try again."

        if is_uncertain(reply):
            search_info = search_web_duckduckgo(prompt)
            return f"I wasn't fully sure, so I searched the web for you:\n\n{search_info}"

        return reply
    except requests.RequestException as exc:
        return f"Error calling AI service: {exc}"


def extract_text_from_image(image: Image.Image, ocr_api_key: str) -> str:
    if not ocr_api_key:
        return ""

    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_bytes = buffered.getvalue()

    try:
        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"filename": ("image.png", img_bytes)},
            data={"apikey": ocr_api_key, "language": "eng"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("IsErroredOnProcessing"):
            return ""
        parsed_results = result.get("ParsedResults") or []
        if parsed_results:
            return (parsed_results[0].get("ParsedText") or "").strip()
    except requests.RequestException:
        return ""
    return ""
