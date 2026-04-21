# Old-MrBunny-AI

MrBunny AI is a Streamlit assistant with:

- Multi-conversation chat
- Optional OCR text extraction from uploaded images
- Voice playback with gTTS
- OpenRouter-backed responses

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Add secrets in Streamlit app settings, `.streamlit/secrets.toml`, or environment variables:

```toml
OPENROUTER_API_KEY = "your-openrouter-key"
OCR_API_KEY = "your-ocr-space-key"
```

3. Run the app:

```bash
streamlit run app.py
```

## Notes

- Do not commit real API keys.
- OCR is optional. If `OCR_API_KEY` is missing, image upload still works but text extraction is skipped.
