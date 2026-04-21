import re
from io import BytesIO
from uuid import uuid4

import requests
import streamlit as st
from gtts import gTTS
from PIL import Image

from mrbunny_core import (
    build_google_auth_url,
    create_oauth_state,
    exchange_google_code,
    extract_text_from_image,
    fetch_google_user,
    generate_image,
    get_ai_response,
    get_secret,
    load_user_conversations,
    save_user_conversations,
)


st.set_page_config(page_title="MrBunny AI", page_icon="🐰", layout="wide")


def init_session_state() -> None:
    st.session_state.setdefault("conversations", {})
    st.session_state.setdefault("current_convo", None)
    st.session_state.setdefault("show_image_uploader", False)
    st.session_state.setdefault("rename_mode", set())
    st.session_state.setdefault("feedback", {})
    st.session_state.setdefault("pending_audio", "")
    st.session_state.setdefault("user_info", None)
    st.session_state.setdefault("google_oauth_state", "")


def save_current_user_chats() -> None:
    user_info = st.session_state.get("user_info")
    if not user_info:
        return
    user_id = user_info.get("sub")
    if not user_id:
        return
    save_user_conversations(
        user_id,
        st.session_state.conversations,
        st.session_state.current_convo,
    )


def load_current_user_chats() -> None:
    user_info = st.session_state.get("user_info")
    if not user_info:
        st.session_state.conversations = {}
        st.session_state.current_convo = None
        return

    user_id = user_info.get("sub")
    if not user_id:
        st.session_state.conversations = {}
        st.session_state.current_convo = None
        return

    conversations, current_convo = load_user_conversations(user_id)
    st.session_state.conversations = conversations
    st.session_state.current_convo = current_convo


def logout_user() -> None:
    save_current_user_chats()
    st.session_state.user_info = None
    st.session_state.conversations = {}
    st.session_state.current_convo = None
    st.session_state.rename_mode = set()
    st.session_state.feedback = {}
    st.session_state.pending_audio = ""
    st.session_state.show_image_uploader = False
    st.session_state.google_oauth_state = ""
    st.query_params.clear()
    st.rerun()


def render_login_screen() -> None:
    st.title("🐰 MrBunny AI")
    st.caption("Sign in with Google to see your chats.")

    if not st.session_state.google_oauth_state:
        st.session_state.google_oauth_state = create_oauth_state()

    auth_url = build_google_auth_url(st.session_state.google_oauth_state)
    if not auth_url:
        st.error(
            "Missing Google sign-in settings. Add `GOOGLE_CLIENT_ID`, "
            "`GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`."
        )
        st.stop()

    st.markdown(
        (
            f'<a href="{auth_url}" target="_self">'
            '<button style="width:100%;padding:0.9rem;border:none;border-radius:12px;'
            'background:linear-gradient(90deg,#4285F4,#34A853);color:white;'
            'font-weight:700;cursor:pointer;">Sign in with Google</button></a>'
        ),
        unsafe_allow_html=True,
    )
    st.stop()


def authenticate_user() -> None:
    if st.session_state.user_info:
        return

    error = st.query_params.get("error")
    code = st.query_params.get("code")
    returned_state = st.query_params.get("state")

    if error:
        st.error(f"Google sign-in failed: {error}")
        st.query_params.clear()
        render_login_screen()

    if code:
        if returned_state != st.session_state.google_oauth_state:
            st.error("Google sign-in state mismatch. Please try again.")
            st.query_params.clear()
            st.session_state.google_oauth_state = create_oauth_state()
            render_login_screen()

        try:
            with st.spinner("Signing you in..."):
                token_data = exchange_google_code(code)
                user_info = fetch_google_user(token_data["access_token"])
        except (requests.RequestException, ValueError, KeyError) as exc:
            st.error(f"Google sign-in failed: {exc}")
            st.query_params.clear()
            st.session_state.google_oauth_state = create_oauth_state()
            render_login_screen()

        st.session_state.user_info = user_info
        st.query_params.clear()
        load_current_user_chats()
        st.rerun()

    render_login_screen()


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
    save_current_user_chats()


def delete_convo(convo_id: str) -> None:
    if convo_id not in st.session_state.conversations:
        return

    del st.session_state.conversations[convo_id]
    st.session_state.rename_mode.discard(convo_id)

    if st.session_state.current_convo == convo_id:
        remaining = list(st.session_state.conversations.keys())
        st.session_state.current_convo = remaining[0] if remaining else None
    save_current_user_chats()


def rename_convo(convo_id: str, new_name: str) -> None:
    clean_name = new_name.strip()
    if convo_id in st.session_state.conversations and clean_name:
        st.session_state.conversations[convo_id]["name"] = clean_name
        save_current_user_chats()


def render_sidebar() -> None:
    with st.sidebar:
        st.title("💬 Conversations")
        user_info = st.session_state.user_info or {}
        if user_info:
            st.caption(f"Signed in as {user_info.get('name', 'Unknown user')}")
            if st.button("Sign out", use_container_width=True):
                logout_user()
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
                save_current_user_chats()
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
    user_info = st.session_state.user_info or {}
    user_name = user_info.get("name")
    st.caption(f"Your friendly AI assistant{' for ' + user_name if user_name else ''}")

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
                save_current_user_chats()
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
            save_current_user_chats()
            st.rerun()


def main() -> None:
    init_session_state()
    authenticate_user()
    render_sidebar()
    render_main()


if __name__ == "__main__":
    main()
