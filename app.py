import re
from io import BytesIO
from uuid import uuid4

import requests
import streamlit as st
from gtts import gTTS
from PIL import Image
from streamlit_local_storage import LocalStorage

from mrbunny_core import (
    extract_text_from_image,
    generate_image,
    get_ai_response,
    get_secret,
    load_user_conversations,
    save_user_conversations,
)


st.set_page_config(page_title="MrBunny AI", page_icon="🐰", layout="wide")

BROWSER_DEVICE_KEY = "mrbunny_device_id_v1"
PIXVERSE_TEXT_TO_VIDEO_URL = "https://app-api.pixverse.ai/openapi/v2/video/text/generate"
PIXVERSE_STATUS_URL = "https://app-api.pixverse.ai/openapi/v2/video/result/{video_id}"
PIXVERSE_API_KEY_FALLBACK = "sk-d60b94d64385c5108af8d111c80063c6"


def init_session_state() -> None:
    st.session_state.setdefault("conversations", {})
    st.session_state.setdefault("current_convo", None)
    st.session_state.setdefault("show_image_uploader", False)
    st.session_state.setdefault("rename_mode", set())
    st.session_state.setdefault("feedback", {})
    st.session_state.setdefault("pending_audio", "")
    st.session_state.setdefault("device_id", None)
    st.session_state.setdefault("device_storage_loaded", False)
    st.session_state.setdefault("device_storage_attempts", 0)
    st.session_state.setdefault("ghost_conversations", set())


def get_local_storage() -> LocalStorage:
    return LocalStorage()


def load_device_state() -> None:
    if st.session_state.device_storage_loaded:
        return

    local_storage = get_local_storage()
    device_id = local_storage.getItem(BROWSER_DEVICE_KEY)
    if device_id in (None, "") and st.session_state.device_storage_attempts < 1:
        st.session_state.device_storage_attempts += 1
        st.rerun()

    if not device_id:
        device_id = uuid4().hex
        local_storage.setItem(BROWSER_DEVICE_KEY, device_id, key="browser_device_id_saver")

    st.session_state.device_id = device_id
    conversations, current_convo = load_user_conversations(device_id)
    st.session_state.conversations = conversations
    st.session_state.current_convo = current_convo
    st.session_state.ghost_conversations = set()
    st.session_state.device_storage_attempts = 0
    st.session_state.device_storage_loaded = True


def save_device_chats() -> None:
    device_id = st.session_state.device_id
    if not device_id:
        return

    persisted_conversations = {
        convo_id: convo
        for convo_id, convo in st.session_state.conversations.items()
        if convo_id not in st.session_state.ghost_conversations
    }
    persisted_current = st.session_state.current_convo
    if persisted_current not in persisted_conversations:
        persisted_current = next(iter(persisted_conversations), None)
    save_user_conversations(device_id, persisted_conversations, persisted_current)


def clear_saved_chats() -> None:
    st.session_state.conversations = {}
    st.session_state.current_convo = None
    st.session_state.rename_mode = set()
    st.session_state.feedback = {}
    st.session_state.pending_audio = ""
    st.session_state.ghost_conversations = set()
    save_device_chats()
    st.rerun()


def remove_emojis(text: str) -> str:
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub("", text)


def wants_image_generation(text: str) -> bool:
    lowered = text.lower().strip()
    image_phrases = (
        "draw ",
        "draw me",
        "make an image",
        "generate an image",
        "create an image",
        "make a picture",
        "generate a picture",
        "create a picture",
        "make art",
        "generate art",
        "create art",
        "illustrate",
        "image of",
        "picture of",
    )
    return any(phrase in lowered for phrase in image_phrases)


def generate_video(prompt: str, pixverse_api_key: str, duration: int = 5, aspect_ratio: str = "16:9") -> tuple[str, str | None]:
    """
    Generate a video using the PixVerse API (text-to-video).
    Returns (reply_text, video_url) — stores URL, not bytes, to avoid black screen.
    """
    import time

    headers = {
        "API-KEY": pixverse_api_key,
        "Ai-trace-id": uuid4().hex,
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "model": "v5",
        "quality": "540p",
    }

    try:
        resp = requests.post(PIXVERSE_TEXT_TO_VIDEO_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ErrCode", -1) != 0:
            return f"PixVerse error: {data.get('ErrMsg', 'Unknown error')}", None

        video_id = data["Resp"]["video_id"]
        status_url = PIXVERSE_STATUS_URL.format(video_id=video_id)
        status_headers = {"API-KEY": pixverse_api_key, "Ai-trace-id": uuid4().hex}

        for _ in range(60):
            time.sleep(3)
            status_resp = requests.get(status_url, headers=status_headers, timeout=15)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            status = status_data.get("Resp", {}).get("status")

            if status == 1:
                video_url = status_data["Resp"]["url"]
                return "Here is your generated video! 🎬", video_url
            elif status in (7, 8):
                msg = "Content moderation failed — try a different prompt." if status == 7 else "Generation failed."
                return f"PixVerse: {msg}", None

        return "Video generation timed out. Try again shortly.", None

    except requests.HTTPError as exc:
        return f"Video generation failed (HTTP {exc.response.status_code}): {exc}", None
    except Exception as exc:
        return f"Video generation failed: {exc}", None


def speak(text: str) -> None:
    clean_text = remove_emojis(text).strip()
    if not clean_text:
        st.warning("There is no readable text to play.")
        return

    try:
        audio_buffer = BytesIO()
        gTTS(clean_text).write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        st.audio(audio_buffer.read(), format="audio/mp3")
    except Exception as exc:
        st.warning(f"Audio generation failed: {exc}")


def render_generated_image(image_bytes: bytes | None) -> None:
    if not image_bytes:
        return

    try:
        generated_image = Image.open(BytesIO(image_bytes))
        st.image(generated_image, use_container_width=True)
    except Exception as exc:
        st.warning(f"Generated image could not be displayed: {exc}")


def render_generated_video(video_url: str | None) -> None:
    if not video_url:
        return
    # Use HTML video tag for reliable playback — st.video() can show black screen with remote URLs
    st.markdown(
        f'''<video controls autoplay style="width:100%;border-radius:8px">
  <source src="{video_url}" type="video/mp4">
  <a href="{video_url}" target="_blank">Download video</a>
</video>''',
        unsafe_allow_html=True,
    )


def add_convo(name: str) -> None:
    clean_name = name.strip()
    if not clean_name:
        return
    convo_id = str(uuid4())
    st.session_state.conversations[convo_id] = {"name": clean_name, "messages": []}
    st.session_state.current_convo = convo_id
    st.session_state.ghost_conversations.discard(convo_id)
    save_device_chats()


def delete_convo(convo_id: str) -> None:
    if convo_id not in st.session_state.conversations:
        return

    del st.session_state.conversations[convo_id]
    st.session_state.rename_mode.discard(convo_id)
    st.session_state.ghost_conversations.discard(convo_id)

    if st.session_state.current_convo == convo_id:
        remaining = list(st.session_state.conversations.keys())
        st.session_state.current_convo = remaining[0] if remaining else None
    save_device_chats()


def rename_convo(convo_id: str, new_name: str) -> None:
    clean_name = new_name.strip()
    if convo_id in st.session_state.conversations and clean_name:
        st.session_state.conversations[convo_id]["name"] = clean_name
        save_device_chats()


def toggle_ghost_mode(convo_id: str) -> None:
    if convo_id in st.session_state.ghost_conversations:
        st.session_state.ghost_conversations.remove(convo_id)
    else:
        st.session_state.ghost_conversations.add(convo_id)
    save_device_chats()


def render_sidebar() -> None:
    with st.sidebar:
        st.title("💬 Conversations")
        st.caption("Chats are saved for this device without sign-in.")
        if st.button("Clear saved chats", use_container_width=True):
            clear_saved_chats()
        st.markdown("---")

        current_convo = st.session_state.current_convo
        ghost_enabled = current_convo in st.session_state.ghost_conversations if current_convo else False
        ghost_label = "👻 Ghost On" if ghost_enabled else "👻 Ghost Off"
        if st.button(ghost_label, use_container_width=True, help="Toggle whether the current chat is saved"):
            if current_convo:
                toggle_ghost_mode(current_convo)
                st.rerun()

        if ghost_enabled:
            st.caption("This conversation will not be saved to browser storage.")

        with st.form("new_convo_form", clear_on_submit=True):
            new_convo_name = st.text_input("Create New Conversation")
            create_clicked = st.form_submit_button("Create")
            if create_clicked and new_convo_name.strip():
                add_convo(new_convo_name)
                st.rerun()

        for convo_id, convo in list(st.session_state.conversations.items()):
            is_current = convo_id == st.session_state.current_convo
            row = st.container()
            cols = row.columns([0.72, 0.14, 0.14])
            label = f"👉 {convo['name']}" if is_current else convo["name"]

            if cols[0].button(label, key=f"select_{convo_id}", use_container_width=True):
                st.session_state.current_convo = convo_id
                save_device_chats()
                st.rerun()

            if cols[1].button("✍️", key=f"rename_btn_{convo_id}", use_container_width=True, help="Rename"):
                if convo_id in st.session_state.rename_mode:
                    st.session_state.rename_mode.remove(convo_id)
                else:
                    st.session_state.rename_mode.add(convo_id)
                st.rerun()

            if cols[2].button("🗑️", key=f"del_{convo_id}", use_container_width=True, help="Delete"):
                delete_convo(convo_id)
                st.rerun()

            if convo_id in st.session_state.rename_mode:
                new_name = st.text_input(
                    "Rename to",
                    value=convo["name"],
                    key=f"rename_input_{convo_id}",
                )
                if st.button("Save name", key=f"save_rename_{convo_id}"):
                    rename_convo(convo_id, new_name)
                    st.session_state.rename_mode.discard(convo_id)
                    st.rerun()


def render_feedback(idx: int) -> None:
    feedback = st.session_state.feedback
    current = feedback.get(idx)
    col1, col2, col3 = st.columns([0.14, 0.14, 0.72])

    if col1.button("Play", key=f"speak_{idx}", use_container_width=True):
        st.session_state.pending_audio = str(idx)

    if col2.button("Like", key=f"like_{idx}", use_container_width=True):
        feedback[idx] = "liked"

    if col3.button("Dislike", key=f"dislike_{idx}", use_container_width=True):
        feedback[idx] = "disliked"

    if current == "liked":
        st.caption("Liked")
    elif current == "disliked":
        st.caption("Disliked")


def render_main() -> None:
    st.title("🐰 MrBunny AI")
    st.caption("Your friendly AI assistant")

    api_key = get_secret("OPENROUTER_API_KEY")
    ocr_api_key = get_secret("OCR_API_KEY")
    pixverse_api_key = get_secret("PIXVERSE_API_KEY") or PIXVERSE_API_KEY_FALLBACK

    if not api_key:
        st.error(
            "Missing `OPENROUTER_API_KEY`. Add a real key in Streamlit Cloud app secrets, "
            "`.streamlit/secrets.toml`, `secrets.toml`, or `.env`."
        )
        st.stop()


    if st.session_state.current_convo is None:
        st.info("Create or select a conversation to begin chatting with MrBunny.")
        return

    convo = st.session_state.conversations[st.session_state.current_convo]
    ghost_enabled = st.session_state.current_convo in st.session_state.ghost_conversations

    if ghost_enabled:
        st.info("Ghost mode is on for this chat. Messages here will not be saved.")

    for idx, msg in enumerate(convo["messages"]):
        with st.chat_message("user"):
            st.write(msg["user"])
        with st.chat_message("assistant"):
            if msg["ai"]:
                st.write(msg["ai"])
            render_generated_image(msg.get("image_bytes"))
            render_generated_video(msg.get("video_url"))
            render_feedback(idx)

        if st.session_state.pending_audio == str(idx):
            speak(msg["ai"])
            st.session_state.pending_audio = ""

    uploaded_file = None
    if st.session_state.show_image_uploader:
        uploaded_file = st.file_uploader(
            "Upload an image",
            type=["png", "jpg", "jpeg"],
            key="chat_image_upload",
        )

    with st.form("chat_form", clear_on_submit=True):
        input_col, send_col, upload_col, image_col, video_col = st.columns([5, 1, 1, 1, 1])
        user_text = input_col.text_input("Type your message:")
        send_clicked = send_col.form_submit_button("Chat")
        upload_clicked = upload_col.form_submit_button("📥 Upload")
        image_clicked = image_col.form_submit_button("🎨 Image")
        video_clicked = video_col.form_submit_button("🎬 Video")

        st.caption("Use `Chat` for replies, `🎨 Image` for pictures, and `🎬 Video` to generate a video.")

        if upload_clicked:
            st.session_state.show_image_uploader = not st.session_state.show_image_uploader
            st.rerun()

        if video_clicked:
            clean_text = user_text.strip()
            if not clean_text:
                st.warning("Describe the video you want to generate.")
                return

            with st.spinner("MrBunny is filming... 🎬 (this can take 1–3 minutes)"):
                reply, video_url = generate_video(clean_text, pixverse_api_key)

            convo["messages"].append(
                {"user": clean_text, "ai": reply, "image_bytes": None, "video_url": video_url}
            )
            if not ghost_enabled:
                save_device_chats()
            st.rerun()
