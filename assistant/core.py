import atexit
import base64
import io
import json
import os
import queue
import re
import subprocess
import sys
import time
import wave
from collections import deque
import threading
from pathlib import Path
from urllib.parse import quote_plus

import numpy as np
import requests
import sounddevice as sd
from PyPDF2 import PdfReader
from mss import mss
from PIL import Image

from prompts import (
    CHAT_SYSTEM,
    CHAT_USER_TEMPLATE,
    INTENT_CLASSIFIER_SYSTEM,
    INTENT_CLASSIFIER_USER_TEMPLATE,
    STT_CONTEXT_HINT,
    STT_POSTPROCESS_PROMPT,
)


def _load_dotenv():
    dotenv_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY")

STT_MODEL = os.environ.get("STT_MODEL", "gpt-4o-mini-transcribe")
INTENT_MODEL = os.environ.get("INTENT_MODEL", "gpt-4o-mini")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")
VISION_MODEL = os.environ.get("VISION_MODEL", "")
POSTPROCESS_MODEL = os.environ.get("POSTPROCESS_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "20"))
STREAM_CHAT = os.environ.get("STREAM_CHAT", "0") == "1"
STREAM_READ_TIMEOUT = float(os.environ.get("STREAM_READ_TIMEOUT", "5"))
STREAM_IDLE_TIMEOUT = float(os.environ.get("STREAM_IDLE_TIMEOUT", "2"))
VOICE_RECORD_MODE = os.environ.get("VOICE_RECORD_MODE", "auto").lower()
SCREENSHOT_ENABLED_DEFAULT = os.environ.get("SCREENSHOT_ENABLED", "0") == "1"
SCREENSHOT_MAX_WIDTH = int(os.environ.get("SCREENSHOT_MAX_WIDTH", "1000"))
SCREENSHOT_LOG_DIR = os.environ.get("SCREENSHOT_LOG_DIR", "log")
SCREENSHOT_DEBUG = os.environ.get("SCREENSHOT_DEBUG", "0") == "1"
SHORT_AUDIO_REPEAT_SECONDS = float(os.environ.get("SHORT_AUDIO_REPEAT_SECONDS", "2"))
STT_INCLUDE_HISTORY = os.environ.get("STT_INCLUDE_HISTORY", "1") == "1"
PAPER_EXPORT_DIR = os.environ.get("PAPER_EXPORT_DIR")

ENABLE_POSTPROCESS = os.environ.get("ENABLE_POSTPROCESS", "1") == "1"
ENABLE_TTS = os.environ.get("ENABLE_TTS", "0") == "1"
TTS_VOICE = os.environ.get("TTS_VOICE", "")
TTS_RATE = int(os.environ.get("TTS_RATE", "0"))
TTS_STREAMING = os.environ.get("TTS_STREAMING", "0") == "1"
TTS_STREAM_START_WORDS = int(os.environ.get("TTS_STREAM_START_WORDS", "1"))
TTS_STREAM_COALESCE_MS = int(os.environ.get("TTS_STREAM_COALESCE_MS", "200"))

SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
INPUT_DEVICE = os.environ.get("INPUT_DEVICE")
BLOCK_SECONDS = float(os.environ.get("BLOCK_SECONDS", "0.1"))
START_THRESHOLD = float(os.environ.get("START_THRESHOLD", "0.015"))
SILENCE_SECONDS = float(os.environ.get("SILENCE_SECONDS", "1.2"))
MAX_RECORD_SECONDS = float(os.environ.get("MAX_RECORD_SECONDS", "12"))
PREROLL_SECONDS = float(os.environ.get("PREROLL_SECONDS", "0.4"))

AHK_PATH = os.environ.get("AHK_PATH", r"C:\Program Files\AutoHotkey\AutoHotkey.exe")
AHK_SCRIPT = os.environ.get(
    "AHK_SCRIPT", os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "ahk", "assistant.ahk"))
)
APP_WINDOW_TITLE = os.environ.get("APP_WINDOW_TITLE", "Academic Voice Assistant")


def _require_api_key():
    if not API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")


def _record_utterance_auto() -> tuple[str, float]:
    block_size = int(SAMPLE_RATE * BLOCK_SECONDS)
    max_blocks = int(MAX_RECORD_SECONDS / BLOCK_SECONDS)
    silence_blocks = int(SILENCE_SECONDS / BLOCK_SECONDS)
    preroll_blocks = int(PREROLL_SECONDS / BLOCK_SECONDS)

    q = queue.Queue()
    pre_roll = deque(maxlen=max(1, preroll_blocks))

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        q.put(indata.copy())

    started = False
    silence_count = 0
    chunks = []

    device = int(INPUT_DEVICE) if INPUT_DEVICE is not None else None
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=block_size,
        callback=callback,
        device=device,
    ):
        while True:
            block = q.get()
            rms = float(np.sqrt(np.mean(block**2)))

            if not started:
                pre_roll.append(block)
                if rms >= START_THRESHOLD:
                    started = True
                    chunks.extend(list(pre_roll))
                    pre_roll.clear()
                continue

            chunks.append(block)
            if rms < START_THRESHOLD:
                silence_count += 1
            else:
                silence_count = 0

            if silence_count >= silence_blocks or len(chunks) >= max_blocks:
                break

    audio = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 1), dtype="float32")
    audio = np.clip(audio, -1.0, 1.0)

    wav_path = os.path.join(os.path.dirname(__file__), "last_utterance.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())

    duration = float(len(audio) / SAMPLE_RATE) if len(audio) else 0.0
    return wav_path, duration


def _record_utterance_manual(stop_event: "threading.Event") -> tuple[str, float]:
    block_size = int(SAMPLE_RATE * BLOCK_SECONDS)
    max_blocks = int(MAX_RECORD_SECONDS / BLOCK_SECONDS)

    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        q.put(indata.copy())

    chunks = []
    device = int(INPUT_DEVICE) if INPUT_DEVICE is not None else None
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=block_size,
        callback=callback,
        device=device,
    ):
        while not stop_event.is_set() and len(chunks) < max_blocks:
            try:
                block = q.get(timeout=BLOCK_SECONDS)
            except queue.Empty:
                continue
            chunks.append(block)

    if not chunks:
        return "", 0.0

    audio = np.concatenate(chunks, axis=0)
    audio = np.clip(audio, -1.0, 1.0)

    wav_path = os.path.join(os.path.dirname(__file__), "last_utterance.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())

    duration = float(len(audio) / SAMPLE_RATE) if len(audio) else 0.0
    return wav_path, duration


def _build_stt_prompt(history: list | None = None) -> str:
    base = STT_CONTEXT_HINT.strip()
    if not STT_INCLUDE_HISTORY or not history:
        return base
    recent_user = []
    for msg in history[-4:]:
        if msg.get("role") == "user":
            recent_user.append(_content_as_text(msg.get("content", "")))
    if not recent_user:
        return base
    joined = " ".join(recent_user).strip()
    if len(joined) > 200:
        joined = joined[-200:]
    return f"{base}\nRecent utterances: {joined}"


def _stt_transcribe(wav_path: str, prompt: str | None = None) -> str:
    _require_api_key()
    with open(wav_path, "rb") as f:
        files = {"file": ("audio.wav", f, "audio/wav")}
        data = {"model": STT_MODEL, "response_format": "json"}
        if prompt:
            data["prompt"] = prompt
        headers = {"Authorization": f"Bearer {API_KEY}"}
        resp = requests.post(
            f"{BASE_URL}/audio/transcriptions", headers=headers, files=files, data=data, timeout=REQUEST_TIMEOUT
        )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("text", "").strip()


def _postprocess_transcript(text: str) -> str:
    if not ENABLE_POSTPROCESS or not text:
        return text
    _require_api_key()
    messages = [
        {"role": "system", "content": STT_POSTPROCESS_PROMPT},
        {"role": "user", "content": text},
    ]
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    body = {"model": POSTPROCESS_MODEL, "messages": messages, "temperature": 0.0}
    resp = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _content_as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text":
                parts.append(item.get("text", ""))
            elif item_type == "image_url":
                url = item.get("image_url", {}).get("url", "")
                if url:
                    parts.append(f"[image: {url}]")
        return "\n".join(parts)
    return str(content)


def _format_history(history: list) -> str:
    if not history:
        return "(none)"
    lines = []
    for msg in history[-6:]:
        role = msg.get("role", "")
        content = _content_as_text(msg.get("content", ""))
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines) if lines else "(none)"


def _parse_tab_number(text: str) -> int | None:
    lowered = text.lower()
    word_map = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    ordinal_map = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    match = re.search(r"\b(?:go to )?(\d+)(st|nd|rd|th)?\s+tab\b", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"\btab\s+(\d+)(st|nd|rd|th)?\b", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:go to )?(\d+)(st|nd|rd|th)?\s+paper\b", lowered)
    if match:
        return int(match.group(1))
    for word, value in {**word_map, **ordinal_map}.items():
        if re.search(rf"\b(?:go to )?{word}\s+tab\b", lowered):
            return value
        if re.search(rf"\btab\s+{word}\b", lowered):
            return value
        if re.search(rf"\b(?:go to )?{word}\s+paper\b", lowered):
            return value
    return None


def _split_complete_sentences(text: str) -> tuple[list[str], str]:
    sentences = []
    start = 0
    for idx, ch in enumerate(text):
        if ch in ".?!\n":
            chunk = text[start : idx + 1]
            sentences.append(chunk)
            start = idx + 1
    remainder = text[start:]
    return sentences, remainder


def _split_words_buffer(text: str) -> tuple[list[str], str]:
    if not text:
        return [], ""
    parts = text.split()
    if not parts:
        return [], text
    last_char = text[-1]
    if last_char.isspace():
        return parts, ""
    if last_char in ".?!,:;":
        return parts, ""
    if len(parts) == 1:
        return [], text
    return parts[:-1], parts[-1]


def _parse_tab_number(text: str) -> int | None:
    lowered = text.lower()
    word_map = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    ordinal_map = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    match = re.search(r"\b(?:go to )?(\d+)(st|nd|rd|th)?\s+tab\b", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"\btab\s+(\d+)(st|nd|rd|th)?\b", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:go to )?(\d+)(st|nd|rd|th)?\s+paper\b", lowered)
    if match:
        return int(match.group(1))
    for word, value in {**word_map, **ordinal_map}.items():
        if re.search(rf"\b(?:go to )?{word}\s+tab\b", lowered):
            return value
        if re.search(rf"\btab\s+{word}\b", lowered):
            return value
        if re.search(rf"\b(?:go to )?{word}\s+paper\b", lowered):
            return value
    return None


def _classify_intent(text: str, history: list) -> dict:
    _require_api_key()
    messages = [
        {"role": "system", "content": INTENT_CLASSIFIER_SYSTEM},
        {
            "role": "user",
            "content": INTENT_CLASSIFIER_USER_TEMPLATE.format(text=text, history=_format_history(history)),
        },
    ]
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    body = {"model": INTENT_MODEL, "messages": messages, "temperature": 0.0}
    resp = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


def _open_url(url: str) -> None:
    subprocess.run([AHK_PATH, AHK_SCRIPT, "open_url", url], check=False)


def _run_ahk(action: str, param: str | None = None) -> None:
    args = [AHK_PATH, AHK_SCRIPT, action]
    if param:
        args.append(param)
    subprocess.run(args, check=False)


def _export_current_paper_file() -> Path:
    if not PAPER_EXPORT_DIR:
        raise RuntimeError("PAPER_EXPORT_DIR is not set in the environment.")
    target_dir = Path(PAPER_EXPORT_DIR).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    before = {}
    for entry in target_dir.iterdir():
        if entry.is_file():
            before[entry.name] = entry.stat().st_mtime
    _run_ahk("activate_window", "Zotero")
    time.sleep(0.2)
    _run_ahk("export_paper", str(target_dir))
    deadline = time.time() + 15
    newest: Path | None = None
    newest_mtime = -1.0
    while time.time() < deadline:
        for entry in target_dir.iterdir():
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
            prev = before.get(entry.name, -1.0)
            if prev == -1.0 or mtime > prev + 1e-3:
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = entry
        if newest is not None:
            if newest.stat().st_size > 0:
                return newest
        time.sleep(0.25)
    raise RuntimeError("Timed out while exporting paper from Zotero.")


def _extract_pdf_chunks(path: Path, chunk_chars: int = 6000) -> list[tuple[str, str]]:
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    chunks = []
    acc = []
    acc_len = 0
    chunk_start = 1
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        acc.append(f"[Page {idx}]\n{text.strip()}")
        acc_len += len(text)
        if acc_len >= chunk_chars:
            end_label = idx
            label = f"{chunk_start}-{end_label}" if chunk_start != end_label else f"{end_label}"
            chunks.append((label, "\n\n".join(acc).strip()))
            acc = []
            acc_len = 0
            chunk_start = idx + 1
    if acc:
        end_label = total_pages
        label = f"{chunk_start}-{end_label}" if chunk_start != end_label else f"{end_label}"
        chunks.append((label, "\n\n".join(acc).strip()))
    if not chunks:
        raise RuntimeError("No text could be extracted from the PDF.")
    return chunks


def _capture_fullscreen_png_data_url() -> str:
    with mss() as sct:
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    if SCREENSHOT_MAX_WIDTH > 0 and img.width > SCREENSHOT_MAX_WIDTH:
        ratio = SCREENSHOT_MAX_WIDTH / float(img.width)
        new_size = (SCREENSHOT_MAX_WIDTH, int(img.height * ratio))
        img = img.resize(new_size, Image.BICUBIC)
    _write_screenshot_log(img)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    if SCREENSHOT_DEBUG:
        print(f"[screenshot] captured {img.width}x{img.height} bytes={len(encoded)}", file=sys.stderr)
    return f"data:image/png;base64,{encoded}"


def _write_screenshot_log(img: Image.Image) -> None:
    try:
        log_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", SCREENSHOT_LOG_DIR))
        os.makedirs(log_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(log_dir, f"screenshot-{ts}.png")
        img.save(path, format="PNG")
    except OSError:
        pass


def _respond_chat(text: str, history: list, on_stream=None, image_data_url: str | None = None) -> str:
    global _tts_streamed_last
    global _tts_stream_id
    global _tts_stream_buffer
    _stop_tts()
    _tts_streamed_last = False
    _tts_stream_id += 1
    current_stream_id = _tts_stream_id
    with _tts_stream_lock:
        _tts_stream_buffer = ""
    _require_api_key()
    messages = [{"role": "system", "content": CHAT_SYSTEM}]
    if image_data_url:
        messages.append({"role": "system", "content": "A screenshot image is provided by the user message."})
    if history:
        messages.extend(history[-6:])
    if image_data_url:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CHAT_USER_TEMPLATE.format(text=text)},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": CHAT_USER_TEMPLATE.format(text=text)})
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    model = VISION_MODEL if image_data_url and VISION_MODEL else CHAT_MODEL
    body = {"model": model, "messages": messages, "temperature": 0.3}

    if STREAM_CHAT and on_stream is not None:
        body["stream"] = True
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers=headers,
            json=body,
            timeout=(REQUEST_TIMEOUT, STREAM_READ_TIMEOUT),
            stream=True,
        )
        resp.raise_for_status()
        full = []
        tts_buffer = ""
        tts_words: list[str] = []
        tts_started = False
        last_delta_time = time.time()
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    if full and (time.time() - last_delta_time) > STREAM_IDLE_TIMEOUT:
                        break
                    continue
                if raw.startswith("data: "):
                    data = raw[6:]
                else:
                    data = raw
                if data.strip() == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                    delta = payload["choices"][0].get("delta", {}).get("content", "")
                except (KeyError, json.JSONDecodeError):
                    continue
                if delta:
                    last_delta_time = time.time()
                    full.append(delta)
                    on_stream(delta)
                    if ENABLE_TTS and TTS_STREAMING:
                        tts_buffer += delta
                        new_words, remainder = _split_words_buffer(tts_buffer)
                        if new_words:
                            tts_words.extend(new_words)
                            if not tts_started and len(tts_words) >= TTS_STREAM_START_WORDS:
                                tts_started = True
                            if tts_started and tts_words:
                                _enqueue_tts_stream(" ".join(tts_words), current_stream_id)
                                tts_words = []
                        tts_buffer = remainder
        except requests.exceptions.ReadTimeout:
            if full:
                if ENABLE_TTS and TTS_STREAMING:
                    if tts_buffer.strip():
                        tts_words.extend(tts_buffer.split())
                    if tts_words:
                        _enqueue_tts_stream(" ".join(tts_words), current_stream_id)
                _tts_streamed_last = ENABLE_TTS and TTS_STREAMING
                return "".join(full).strip()
            raise
        if ENABLE_TTS and TTS_STREAMING:
            if tts_buffer.strip():
                tts_words.extend(tts_buffer.split())
            if tts_words:
                _enqueue_tts_stream(" ".join(tts_words), current_stream_id)
        _tts_streamed_last = ENABLE_TTS and TTS_STREAMING
        return "".join(full).strip()

    resp = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


_tts_proc: subprocess.Popen | None = None
_tts_queue: queue.Queue[str] | None = None
_tts_thread: threading.Thread | None = None
_tts_streamed_last = False
_tts_stream_buffer = ""
_tts_stream_id = 0
_tts_stream_lock = threading.Lock()


def _ensure_tts_worker() -> None:
    global _tts_queue, _tts_thread
    if _tts_queue is None:
        _tts_queue = queue.Queue()
    if _tts_thread is None or not _tts_thread.is_alive():
        _tts_thread = threading.Thread(target=_tts_worker, daemon=True)
        _tts_thread.start()


def _tts_worker() -> None:
    global _tts_stream_buffer
    if _tts_queue is None:
        return
    while True:
        text = None
        with _tts_stream_lock:
            if _tts_stream_buffer:
                text = _tts_stream_buffer
                _tts_stream_buffer = ""
        if text is None:
            text = _tts_queue.get()
        if text is None:
            break
        _run_tts(text)


def _stop_tts() -> None:
    global _tts_proc
    if _tts_proc and _tts_proc.poll() is None:
        try:
            _tts_proc.terminate()
        except OSError:
            pass
    _tts_proc = None
    if _tts_queue is not None:
        while not _tts_queue.empty():
            try:
                _tts_queue.get_nowait()
            except queue.Empty:
                break
    global _tts_stream_buffer
    with _tts_stream_lock:
        _tts_stream_buffer = ""


def _enqueue_tts(text: str, interrupt: bool = False) -> None:
    if not ENABLE_TTS or not text:
        return
    if interrupt:
        _stop_tts()
    _ensure_tts_worker()
    if _tts_queue is not None:
        _tts_queue.put(text)


def _enqueue_tts_stream(text: str, stream_id: int) -> None:
    global _tts_stream_buffer
    if not ENABLE_TTS or not text:
        return
    if stream_id != _tts_stream_id:
        return
    with _tts_stream_lock:
        _tts_stream_buffer = (_tts_stream_buffer + " " + text).strip() if _tts_stream_buffer else text.strip()
        buffer_text = _tts_stream_buffer
        if not _tts_proc or _tts_proc.poll() is not None:
            _tts_stream_buffer = ""
        else:
            buffer_text = ""
    if buffer_text:
        _enqueue_tts(buffer_text, interrupt=False)


def _run_tts(text: str) -> None:
    if not ENABLE_TTS or not text:
        return
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "if ($env:TTS_VOICE) {"
        "  $voice = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } |"
        "    Where-Object { $_.Name -like \"*$env:TTS_VOICE*\" } | Select-Object -First 1;"
        "  if ($voice) { $s.SelectVoice($voice.Name) }"
        "}"
        "$s.Rate = [int]$env:TTS_RATE;"
        "$s.Speak($env:TTS_TEXT);"
    )
    env = os.environ.copy()
    env["TTS_TEXT"] = text
    env["TTS_VOICE"] = TTS_VOICE
    env["TTS_RATE"] = str(TTS_RATE)
    global _tts_proc
    _tts_proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], env=env)
    _tts_proc.wait()


def _speak(text: str) -> None:
    _enqueue_tts(text, interrupt=True)


def stop_tts() -> None:
    _stop_tts()


atexit.register(_stop_tts)


class Assistant:
    def __init__(self):
        if not os.path.exists(AHK_SCRIPT):
            raise RuntimeError(f"AHK script not found: {AHK_SCRIPT}")
        self.histories = {}
        self.last_search = {}
        self.last_actions = {}
        self.screenshot_enabled = SCREENSHOT_ENABLED_DEFAULT

    def set_screenshot_enabled(self, enabled: bool) -> None:
        self.screenshot_enabled = bool(enabled)

    def _apply_intent_overrides(self, text: str, intent: dict | None) -> dict:
        intent = intent or {"intent": "chat"}
        lowered = text.lower()
        note_prefixes = [
            "take note followings",
            "take note following",
            "take note of",
            "take note",
        ]
        for prefix in note_prefixes:
            if lowered.startswith(prefix):
                content = text[len(prefix) :].strip(" :,-")
                intent = {
                    "intent": "note",
                    "command": "notepad_append",
                    "content": content,
                    "confidence": 1.0,
                }
                return intent
        tab_number = _parse_tab_number(text)
        if tab_number is not None:
            intent = {
                "intent": "zotero_command",
                "command": "tab",
                "query": str(tab_number),
                "confidence": 1.0,
            }
            return intent
        if "activate zotero" in lowered or "focus zotero" in lowered:
            intent = {"intent": "zotero_command", "command": "activate", "confidence": 1.0}
        return intent

    def _get_history(self, session_id: str) -> list:
        if session_id not in self.histories:
            self.histories[session_id] = []
        return self.histories[session_id]

    def _process_text_input(
        self,
        text: str,
        session_id: str,
        on_stream=None,
        on_action=None,
        history: list | None = None,
    ) -> tuple[str, dict]:
        if history is None:
            history = self._get_history(session_id)
        intent = _classify_intent(text, history)
        intent = self._apply_intent_overrides(text, intent)
        response = self._dispatch(intent, text, history, session_id, on_stream=on_stream, on_action=on_action)
        return response, intent

    def _remember_action(self, session_id: str, intent: dict, transcript: str, response: str) -> None:
        if not intent:
            return
        self.last_actions[session_id] = {
            "intent": dict(intent),
            "transcript": transcript,
            "response": response,
        }

    def _handle_short_audio(self, session_id: str, history: list, on_action=None) -> dict | None:
        last = self.last_actions.get(session_id)
        if not last:
            return None
        intent = last.get("intent") or {}
        if intent.get("intent", "chat") == "chat":
            history.append({"role": "assistant", "content": "[ignored]"})
            return {"transcript": "", "response": "[ignored]", "intent": intent}
        transcript = last.get("transcript", "")
        response = self._dispatch(
            intent,
            transcript or "[repeat]",
            history,
            session_id,
            on_stream=None,
            on_action=on_action,
            record_history=False,
        )
        return {"transcript": "", "response": response, "intent": intent}

    def ingest_paper(self, session_id: str, on_stream=None, on_action=None, on_transcript=None) -> dict:
        try:
            path = _export_current_paper_file()
            history = self._get_history(session_id)
            chunks = _extract_pdf_chunks(path)
            total = len(chunks)
            for idx, (label, chunk_text) in enumerate(chunks, start=1):
                history.append(
                    {
                        "role": "user",
                        "content": f"[Paper {path.name} pages {label} chunk {idx}/{total}]\n{chunk_text}",
                    }
                )
            response = f"Paper '{path.name}' converted to text ({total} chunks covering {len(chunks)} segments)."
            return {"transcript": "", "response": response, "intent": {"intent": "note", "command": "paper"}}
        except Exception as exc:
            return {"transcript": "", "response": f"Paper transfer failed: {exc}", "intent": {"intent": "chat"}}

    def _dispatch(
        self,
        intent: dict,
        transcript: str,
        history: list,
        session_id: str,
        on_stream=None,
        on_action=None,
        record_history: bool = True,
    ) -> str:
        intent = intent or {"intent": "chat"}
        intent_type = intent.get("intent", "chat")
        speak_allowed = intent_type == "chat"
        saved_intent = dict(intent)

        if intent_type == "zotero_command":
            command = intent.get("command", "")
            query = intent.get("query", "").strip()
            if not query:
                lowered = transcript.lower()
                if lowered.startswith("find "):
                    query = transcript[5:].strip()
            lowered = transcript.lower()
            if "go to library" in lowered or "goto library" in lowered:
                command = "library"
            saved_intent["command"] = command
            if query:
                saved_intent["query"] = query
            if command in {"page_down", "page_up", "find"}:
                _run_ahk("activate_window", "Zotero")
                if command == "find" and query:
                    if on_action:
                        on_action("zotero_command", command)
                    _run_ahk("find", query)
                    response = f"OK, Zotero find: {query}"
                else:
                    if on_action:
                        on_action("zotero_command", command)
                    _run_ahk(command)
                    response = f"OK, Zotero {command.replace('_', ' ')}."
            elif command == "tab":
                if not query:
                    tab_number = _parse_tab_number(transcript)
                    if tab_number is not None:
                        query = str(tab_number)
                try:
                    n = int(query)
                except ValueError:
                    n = 0
                target = n + 1
                print(f"[debug] zotero_tab target={target}", flush=True)
                if 2 <= target <= 9:
                    if on_action:
                        on_action("zotero_command", command)
                    _run_ahk("activate_window", "Zotero")
                    _run_ahk("zotero_tab", str(target))
                    response = f"OK, Zotero tab {target}."
                else:
                    response = "I can only switch to tabs 2 through 9."
            elif command == "library":
                if on_action:
                    on_action("zotero_command", command)
                _run_ahk("activate_window", "Zotero")
                _run_ahk("zotero_library")
                response = "OK, Zotero library."
            elif command == "activate":
                if on_action:
                    on_action("zotero_command", command)
                _run_ahk("activate_window", "Zotero")
                response = "OK, brought Zotero to front."
            else:
                response = "I did not catch a Zotero command."

        elif intent_type == "web_search":
            engine = intent.get("engine", "google")
            query = intent.get("query", "").strip()
            if not query and session_id in self.last_search:
                query = self.last_search[session_id]
            if not query:
                query = transcript
            saved_intent["engine"] = engine
            saved_intent["query"] = query
            if engine == "scholar":
                url = "https://scholar.google.com/scholar?q=" + quote_plus(query)
            else:
                url = "https://www.google.com/search?q=" + quote_plus(query)
            _open_url(url)
            response = f"Searching {engine} for: {query}"
            self.last_search[session_id] = query

        elif intent_type == "note":
            content = intent.get("content", "").strip()
            saved_intent["content"] = content
            if content:
                _run_ahk("notepad_append", content)
                response = f"Noted: {content}"
            else:
                response = "I didn't hear what to note."

        else:
            image_data_url = None
            if self.screenshot_enabled:
                try:
                    image_data_url = _capture_fullscreen_png_data_url()
                except Exception as exc:
                    if SCREENSHOT_DEBUG:
                        print(f"[screenshot_error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
                    image_data_url = None
            if APP_WINDOW_TITLE:
                _run_ahk("activate_window_no_resize", APP_WINDOW_TITLE)
            response = _respond_chat(transcript, history, on_stream=on_stream, image_data_url=image_data_url)
            if record_history:
                history.append({"role": "user", "content": transcript})
                history.append({"role": "assistant", "content": response})

        if intent_type != "chat" and record_history:
            history.append({"role": "user", "content": transcript})
            history.append({"role": "assistant", "content": response})

        response = response.replace("\n\n", "\n")
        if speak_allowed:
            if not _tts_streamed_last:
                _speak(response)
        else:
            stop_tts()
        self._remember_action(session_id, saved_intent, transcript, response)
        return response

    def handle_text(self, text: str, session_id: str, on_stream=None, on_action=None, on_transcript=None) -> dict:
        try:
            response, intent = self._process_text_input(text, session_id, on_stream=on_stream, on_action=on_action)
            return {"transcript": text, "response": response, "intent": intent}
        except requests.exceptions.RequestException as exc:
            print(f"[api_error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return {
                "transcript": text,
                "response": f"Network error contacting API: {exc.__class__.__name__}",
                "intent": {"intent": "chat"},
            }

    def handle_voice(
        self,
        session_id: str,
        on_stream=None,
        on_action=None,
        on_transcript=None,
        record_mode: str | None = None,
        stop_event=None,
    ) -> dict:
        try:
            history = self._get_history(session_id)
            stt_prompt = _build_stt_prompt(history)
            mode = (record_mode or VOICE_RECORD_MODE).lower()
            if mode == "manual":
                if stop_event is None:
                    return {"transcript": "", "response": "Manual record needs a stop signal.", "intent": {"intent": "chat"}}
                wav_path, record_duration = _record_utterance_manual(stop_event)
            else:
                wav_path, record_duration = _record_utterance_auto()
            if not wav_path:
                return {"transcript": "", "response": "Heard nothing. Try again.", "intent": {"intent": "chat"}}
            if record_duration < SHORT_AUDIO_REPEAT_SECONDS:
                short_result = self._handle_short_audio(session_id, history, on_action=on_action)
                if short_result is not None:
                    return short_result
            transcript = _stt_transcribe(wav_path, prompt=stt_prompt)
            cleaned = _postprocess_transcript(transcript)
            if not cleaned:
                return {"transcript": "", "response": "Heard nothing. Try again.", "intent": {"intent": "chat"}}
            if on_transcript:
                on_transcript(cleaned)
            response, intent = self._process_text_input(
                cleaned, session_id, on_stream=on_stream, on_action=on_action, history=history
            )
            return {"transcript": cleaned, "response": response, "intent": intent}
        except requests.exceptions.RequestException as exc:
            print(f"[api_error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return {
                "transcript": "",
                "response": f"Network error contacting API: {exc.__class__.__name__}",
                "intent": {"intent": "chat"},
            }
