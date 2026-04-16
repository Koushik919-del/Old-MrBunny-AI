import os
import requests
import json
from PIL import Image
import io
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup

# ---------- uncertainty detection ----------
UNCERTAIN_PHRASES = [
    "no evidence", "not confirmed", "no official announcement",
    "i couldn't find", "not available", "uncertain", "speculative",
    "does not exist", "not verified", "unconfirmed", "unknown",
    "nothing has been announced", "as of now"
]

def is_uncertain(response: str) -> bool:
    response_lower = response.lower()
    return any(phrase in response_lower for phrase in UNCERTAIN_PHRASES)

# ---------- conversation history ----------
ai_conversation = []

# ---------- fallback web search ----------
def search_web_duckduckgo(query: str, max_results: int = 3) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No useful search results found."

        summary_lines = []
        for i, item in enumerate(results, start=1):
            title = item.get("title") or "No title"
            snippet = item.get("body") or ""
            url = item.get("href") or ""
            summary_lines.append(f"{i}. {title}\n{snippet}\n{url}\n")
        return "\n".join(summary_lines)
    except Exception as e:
        return f"Search failed: {e}"

# ---------- AI/chat response ----------
def get_ai_response(prompt: str, api_key: str) -> str:
    global ai_conversation

    ai_conversation.append({"role": "user", "content": prompt})

    system_prompt = (
        "You are MrBunny AI, a smart, clear, and friendly AI assistant. "
        "Answer clearly, directly, and only in English unless requested otherwise. "
        "Only if asked about your creation, mention Koushik Tummepalli creating you. "
        "Introduce yourself as MrBunny when asked. "
        "Tell the answer first and then explain it. "
        "Always use emojis to make the conversation more engaging. "
        "It is not Avengers: Kang Dynasty anymore, it is Avengers: Doomsday."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek/deepseek-r1-0528:free",
        "messages": [{"role": "system", "content": system_prompt}] + ai_conversation
    }

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=15
        )
        result = response.json()
        reply = (result.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

        if not reply or is_uncertain(reply):
            search_info = search_web_duckduckgo(prompt)
            fallback_msg = f"🔍 I wasn't sure about the answer, so I searched for you:\n\n{search_info}"
            ai_conversation.append({"role": "assistant", "content": fallback_msg})
            return fallback_msg

        ai_conversation.append({"role": "assistant", "content": reply})
        return reply

    except Exception as e:
        return f"❌ Error calling AI: {e}"

# ---------- OCR helper ----------
def extract_text_from_image(image: Image.Image, ocr_api_key: str) -> str:
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_bytes = buffered.getvalue()

    try:
        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"filename": ("image.png", img_bytes)},
            data={"apikey": ocr_api_key, "language": "eng"},
            timeout=30
        )
        result = response.json()
        if result.get("IsErroredOnProcessing"):
            return ""
        parsed_results = result.get("ParsedResults")
        if parsed_results and len(parsed_results) > 0:
            return parsed_results[0].get("ParsedText", "").strip()
    except Exception:
        pass
    return ""
