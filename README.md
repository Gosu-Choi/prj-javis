# Minimal Voice Assistant for Academic Reading (Windows)

This project is a small, voice-first assistant focused on:
- Discussing academic papers
- Zotero navigation (page up/down, find)
- Google / Google Scholar search

It is intentionally narrow in scope and uses:
- Python for audio, STT, intent parsing, and dispatch
- AutoHotkey for keyboard-driven control
- GPT API for STT, intent classification, and responses

## Architecture (pipeline)
1. Microphone audio capture with simple VAD (energy threshold)
2. High-quality STT via GPT API
3. Optional STT post-processing for non-native speech cleanup
4. Intent classification into: chat, zotero_command, web_search
5. Action dispatch:
   - Zotero commands via AutoHotkey
   - Web search via AutoHotkey opening Chrome
   - Chat response via GPT API

## Folder structure
```
prj-javis/
  assistant/
    main.py
    prompts.py
  ahk/
    assistant.ahk
  requirements.txt
  config.example.env
  README.md
```

## Setup
1. Install Python deps:
```
pip install -r requirements.txt
```

2. Install AutoHotkey (v2). Update the path if needed.

3. Copy and edit config:
```
copy config.example.env .env
```
Then set your API key and any overrides.

## Run (voice CLI)
```
set OPENAI_API_KEY=YOUR_KEY
python assistant\main.py
```

Speak after you see "Listening...". Silence ends the utterance.

## Run (desktop UI)
```
python assistant\ui.py
```

Use the Mode toggle to switch between Voice and Text. Sessions keep separate conversational history.
Use Record: Auto/Manual to control whether recording stops on silence or by clicking Stop.

## Global hotkeys (UI)
- Ctrl+L: toggle manual recording (start/stop)
- Page Up / Page Down: switch sessions
- Note: global hotkeys require the `keyboard` package and may need admin privileges on Windows.

## Streaming responses
Set `STREAM_CHAT=1` in `.env` to stream responses in the UI as they are generated.
If the stream stalls, lower `STREAM_READ_TIMEOUT` (default 5 seconds).
If the server keeps the stream open after the last token, lower `STREAM_IDLE_TIMEOUT` (default 2 seconds).

## Screenshot context (chat only)
If `SCREENSHOT_ENABLED=1`, the assistant captures a full-screen screenshot and sends it
along with the user message for chat responses.
Set `SCREENSHOT_MAX_WIDTH` (default 1000) to downscale screenshots and reduce cost.
Set `SCREENSHOT_LOG_DIR` to save screenshots for debugging (default `log`).
Set `VISION_MODEL` to a vision-capable model (e.g., `gpt-4o`) if your chat model cannot see images.

## Notes
- Zotero must already be focused for page up/down/find to work.
- Chrome must be installed and accessible as `chrome.exe`.
- If you want spoken output, set `ENABLE_TTS=1`.
 - To force an English voice, set `TTS_VOICE` (partial name match), e.g. `TTS_VOICE=Zira`.
 - To speed up or slow down speech, set `TTS_RATE` (range -10 to 10).
 - For streaming speech while text is generating, set `TTS_STREAMING=1` and tune:
   - `TTS_STREAM_START_WORDS` (default 1) to begin after N words are buffered
- To reduce gaps between spoken chunks, set `TTS_STREAM_COALESCE_MS` (default 200ms).
- To bring the UI to front on chat responses, set `APP_WINDOW_TITLE` to the window title.
- Set `SHORT_AUDIO_REPEAT_SECONDS` (default 2). Voice taps shorter than this repeat the previous non-chat action without another GPT call; if the last intent was chat, the assistant logs `[ignored]` instead.
- Say "Take note..." (e.g., "Take note followings ...") to append the rest of the utterance to Notepad; the app opens Notepad if needed and adds a new line before your note.
