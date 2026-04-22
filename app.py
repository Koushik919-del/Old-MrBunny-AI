import re
import json
from io import BytesIO
from uuid import uuid4

import streamlit as st
from gtts import gTTS
from PIL import Image
from streamlit_local_storage import LocalStorage

from mrbunny_core import (
    extract_text_from_image,
    generate_image,
    get_ai_response,
    get_secret,
)


st.set_page_config(page_title="MrBunny AI", page_icon="🐰", layout="wide")

BROWSER_CONVERSATIONS_KEY = "mrbunny_conversations_v1"
BROWSER_CURRENT_CONVO_KEY = "mrbunny_current_convo_v1"


def init_session_state() -> None:
    st.session_state.setdefault("conversations", {})
    st.session_state.setdefault("current_convo", None)
    st.session_state.setdefault("show_image_uploader", False)
    st.session_state.setdefault("rename_mode", set())
    st.session_state.setdefault("feedback", {})
    st.session_state.setdefault("pending_audio", "")
    st.session_state.setdefault("browser_storage_loaded", False)


def get_local_storage() -> LocalStorage:
    return LocalStorage()


def _parse_storage_value(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def load_browser_chats() -> None:
    if st.session_state.browser_storage_loaded:
        return

    local_storage = get_local_storage()
    stored_conversations = local_storage.getItem(
        BROWSER_CONVERSATIONS_KEY,
        key="browser_conversations_loader",
    )
    stored_current = local_storage.getItem(
        BROWSER_CURRENT_CONVO_KEY,
        key="browser_current_loader",
    )

    st.session_state.conversations = _parse_storage_value(stored_conversations, {})
    st.session_state.current_convo = _parse_storage_value(stored_current, None)
    if st.session_state.current_convo not in st.session_state.conversations:
        st.session_state.current_convo = next(iter(st.session_state.conversations), None)
    st.session_state.browser_storage_loaded = True


def save_browser_chats() -> None:
    local_storage = get_local_storage()
    local_storage.setItem(
        BROWSER_CONVERSATIONS_KEY,
        json.dumps(st.session_state.conversations),
    )
    local_storage.setItem(
        BROWSER_CURRENT_CONVO_KEY,
        json.dumps(st.session_state.current_convo),
    )


def clear_browser_chats() -> None:
    st.session_state.conversations = {}
    st.session_state.current_convo = None
    st.session_state.rename_mode = set()
    st.session_state.feedback = {}
    st.session_state.pending_audio = ""
    save_browser_chats()
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


def add_convo(name: str) -> None:
    clean_name = name.strip()
    if not clean_name:
        return
    convo_id = str(uuid4())
    st.session_state.conversations[convo_id] = {"name": clean_name, "messages": []}
    st.session_state.current_convo = convo_id
    save_browser_chats()


def delete_convo(convo_id: str) -> None:
    if convo_id not in st.session_state.conversations:
        return

    del st.session_state.conversations[convo_id]
    st.session_state.rename_mode.discard(convo_id)

    if st.session_state.current_convo == convo_id:
        remaining = list(st.session_state.conversations.keys())
        st.session_state.current_convo = remaining[0] if remaining else None
    save_browser_chats()


def rename_convo(convo_id: str, new_name: str) -> None:
    clean_name = new_name.strip()
    if convo_id in st.session_state.conversations and clean_name:
        st.session_state.conversations[convo_id]["name"] = clean_name
        save_browser_chats()


def render_sidebar() -> None:
    with st.sidebar:
        st.title("💬 Conversations")
        st.caption("Chats are saved in this browser on this device.")
        if st.button("Clear saved chats", use_container_width=True):
            clear_browser_chats()
        st.markdown("---")

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
                save_browser_chats()
                st.rerun()

            if cols[1].button("Rename", key=f"rename_btn_{convo_id}", use_container_width=True):
                if convo_id in st.session_state.rename_mode:
                    st.session_state.rename_mode.remove(convo_id)
                else:
                    st.session_state.rename_mode.add(convo_id)
                st.rerun()

            if cols[2].button("Delete", key=f"del_{convo_id}", use_container_width=True):
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

    for idx, msg in enumerate(convo["messages"]):
        with st.chat_message("user"):
            st.write(msg["user"])
        with st.chat_message("assistant"):
            if msg["ai"]:
                st.write(msg["ai"])
            render_generated_image(msg.get("image_bytes"))
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
        input_col, send_col, upload_col, image_col = st.columns([6, 1, 1, 1])
        user_text = input_col.text_input("Type your message:")
        send_clicked = send_col.form_submit_button("Chat")
        upload_clicked = upload_col.form_submit_button("Upload")
        image_clicked = image_col.form_submit_button("Generate Image")

        st.caption("Use `Chat` for normal replies and `Generate Image` for pictures.")

        if upload_clicked:
            st.session_state.show_image_uploader = not st.session_state.show_image_uploader
            st.rerun()

        if send_clicked or image_clicked:
            clean_text = user_text.strip()
            if not clean_text:
                st.warning("Type a message before sending.")
                return

            should_generate_image = image_clicked or wants_image_generation(clean_text)

            if should_generate_image:
                with st.spinner("MrBunny is drawing..."):
                    reply, image_bytes = generate_image(clean_text, api_key)
                convo["messages"].append(
                    {"user": clean_text, "ai": reply, "image_bytes": image_bytes}
                )
                save_browser_chats()
                st.rerun()

            combined_prompt = clean_text
            if uploaded_file is not None:
                try:
                    image = Image.open(uploaded_file).convert("RGB")
                    ocr_text = extract_text_from_image(image, ocr_api_key)
                    if ocr_text:
                        combined_prompt = f"[Image text extracted: {ocr_text}]\n\n{clean_text}"
                except Exception as exc:
                    st.warning(f"Failed to process uploaded image: {exc}")

            with st.spinner("MrBunny is thinking..."):
                reply = get_ai_response(combined_prompt, api_key, convo["messages"])

            convo["messages"].append({"user": clean_text, "ai": reply, "image_bytes": None})
            save_browser_chats()
            st.rerun()


def main() -> None:
    init_session_state()
    load_browser_chats()
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()
