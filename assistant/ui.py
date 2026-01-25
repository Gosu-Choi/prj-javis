import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import keyboard

from core import Assistant, stop_tts


class AssistantUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Academic Voice Assistant")
        self.root.geometry("820x560")

        self.assistant = Assistant()
        self.sessions = []
        self.current_session = None
        self.chat_logs = {}
        self.mode = tk.StringVar(value="voice")
        self._stream_active = {}
        self._stream_ui_active = False
        self.voice_record_mode = tk.StringVar(value="auto")
        self.screenshot_var = tk.BooleanVar(value=self.assistant.screenshot_enabled)
        self._recording = False
        self._record_stop_event = None
        self._hotkey_handles = {}
        self._session_hotkeys_active = False
        self._session_hotkey_resume_job = None

        self._build_ui()
        self._add_session(announce=False)
        self._register_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Mode:").pack(side="left")
        ttk.Radiobutton(top, text="Voice", variable=self.mode, value="voice", command=self._render_mode).pack(
            side="left", padx=6
        )
        ttk.Radiobutton(top, text="Text", variable=self.mode, value="text", command=self._render_mode).pack(
            side="left"
        )
        ttk.Checkbutton(top, text="Screenshots", variable=self.screenshot_var, command=self._toggle_screenshot).pack(
            side="left", padx=(10, 0)
        )

        ttk.Label(top, text="Record:").pack(side="left", padx=(16, 0))
        ttk.Radiobutton(
            top, text="Auto", variable=self.voice_record_mode, value="auto", command=self._render_mode
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            top, text="Manual", variable=self.voice_record_mode, value="manual", command=self._render_mode
        ).pack(side="left")

        self.status = ttk.Label(top, text="Ready")
        self.status.pack(side="right")

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=10, pady=5)

        left = ttk.Frame(main, width=180)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="Sessions").pack(anchor="w")
        self.session_list = tk.Listbox(left, height=18)
        self.session_list.pack(fill="y", expand=True, pady=6)
        self.session_list.bind("<<ListboxSelect>>", self._on_session_select)

        ttk.Button(left, text="New Session", command=self._add_session).pack(fill="x", pady=4)
        ttk.Button(left, text="Delete Session", command=self._delete_session).pack(fill="x")

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self.chat = tk.Text(right, wrap="word", height=20)
        self.chat.pack(fill="both", expand=True)
        self.chat.configure(state="disabled")
        base_font = tkfont.nametofont(self.chat.cget("font"))
        self._speaker_font = base_font.copy()
        self._speaker_font.configure(weight="bold")
        self.chat.tag_configure("speaker_label", font=self._speaker_font)

        self.input_frame = ttk.Frame(right)
        self.input_frame.pack(fill="x", pady=8)

        self.text_entry = ttk.Entry(self.input_frame)
        self.text_entry.pack(side="left", fill="x", expand=True)
        self.text_entry.bind("<Return>", lambda event: self._send_text())

        self.send_button = ttk.Button(self.input_frame, text="Send", command=self._send_text)
        self.send_button.pack(side="left", padx=6)

        self.record_button = ttk.Button(self.input_frame, text="Record", command=self._record_voice)
        self.record_button.pack(side="left", padx=6)

        self._render_mode()

    def _render_mode(self):
        mode = self.mode.get()
        if mode == "voice":
            self.text_entry.configure(state="disabled")
            self.send_button.configure(state="disabled")
            self.record_button.configure(state="normal")
        else:
            if self._recording:
                self._stop_manual_record()
            self.text_entry.configure(state="normal")
            self.send_button.configure(state="normal")
            self.record_button.configure(state="disabled")

    def _toggle_screenshot(self):
        enabled = bool(self.screenshot_var.get())
        self.assistant.set_screenshot_enabled(enabled)
        self._append_system(f"Screenshots {'enabled' if enabled else 'disabled'}")

    def _register_hotkeys(self):
        self._enable_record_hotkey()
        self._enable_session_hotkeys()

    def _enable_record_hotkey(self):
        if "page_up_record" in self._hotkey_handles:
            return
        self._hotkey_handles["page_up_record"] = keyboard.add_hotkey(
            "page up", self._hotkey_toggle_record, suppress=True
        )

    def _disable_record_hotkey(self):
        handle = self._hotkey_handles.pop("page_up_record", None)
        if handle is not None:
            keyboard.remove_hotkey(handle)

    def _enable_session_hotkeys(self):
        if self._session_hotkeys_active:
            return
        self._hotkey_handles["page_down"] = keyboard.add_hotkey(
            "page down", self._hotkey_alt_tab, suppress=True
        )
        self._session_hotkeys_active = True

    def _disable_session_hotkeys(self):
        if not self._session_hotkeys_active:
            return
        if "page_down" in self._hotkey_handles:
            keyboard.remove_hotkey(self._hotkey_handles["page_down"])
        self._session_hotkeys_active = False

    def _hotkey_toggle_record(self):
        self.root.after(0, self._toggle_record_from_hotkey)

    def _hotkey_next_session(self):
        self.root.after(0, self._move_session, 1)

    def _hotkey_prev_session(self):
        self.root.after(0, self._move_session, -1)

    def _hotkey_alt_tab(self):
        keyboard.send("alt+tab")

    def _on_close(self):
        stop_tts()
        self.root.destroy()

    def _suspend_session_hotkeys(self, duration_ms: int = 300):
        self._disable_session_hotkeys()
        self._disable_record_hotkey()
        if self._session_hotkey_resume_job:
            self.root.after_cancel(self._session_hotkey_resume_job)
        self._session_hotkey_resume_job = self.root.after(duration_ms, self._resume_hotkeys)

    def _resume_hotkeys(self):
        self._enable_session_hotkeys()
        self._enable_record_hotkey()

    def _toggle_record_from_hotkey(self):
        if self.mode.get() != "voice":
            self.mode.set("voice")
            self._render_mode()
        if self.voice_record_mode.get() != "manual":
            self.voice_record_mode.set("manual")
        if self._recording:
            self._stop_manual_record()
        else:
            self._start_manual_record()

    def _move_session(self, delta: int):
        if not self.sessions:
            return
        if self.current_session not in self.sessions:
            return
        index = self.sessions.index(self.current_session)
        new_index = (index + delta) % len(self.sessions)
        self.session_list.select_clear(0, "end")
        self.session_list.select_set(new_index)
        self.current_session = self.sessions[new_index]
        self._render_chat()
        self._append_system(f"Switched to {self.current_session}", self.current_session)

    def _add_session(self, announce: bool = True):
        name = f"Session {len(self.sessions) + 1}"
        self.sessions.append(name)
        self.chat_logs[name] = []
        self._stream_active[name] = False
        self.session_list.insert("end", name)
        self.session_list.select_clear(0, "end")
        self.session_list.select_set("end")
        self.current_session = name
        self._render_chat()
        if announce:
            self._append_system(f"Switched to {name}", name)

    def _on_session_select(self, event):
        selection = self.session_list.curselection()
        if not selection:
            return
        name = self.sessions[selection[0]]
        self.current_session = name
        self._render_chat()
        self._append_system(f"Switched to {name}", name)

    def _delete_session(self):
        selection = self.session_list.curselection()
        if not selection:
            return
        index = selection[0]
        name = self.sessions.pop(index)
        self.chat_logs.pop(name, None)
        self._stream_active.pop(name, None)
        self.session_list.delete(index)

        if not self.sessions:
            self.current_session = None
            self._render_chat()
            self._append_system("No sessions. Create a new one.", None)
            return

        new_index = min(index, len(self.sessions) - 1)
        self.session_list.select_set(new_index)
        self.current_session = self.sessions[new_index]
        self._render_chat()
        self._append_system(f"Switched to {self.current_session}", self.current_session)

    def _append_chat(self, role, text, session_id=None):
        session_id = session_id or self.current_session
        if session_id:
            if session_id not in self.chat_logs:
                self.chat_logs[session_id] = []
            self.chat_logs[session_id].append((role, text))
            if role == "Assistant":
                self._stream_active[session_id] = False
        if session_id != self.current_session:
            return
        self.chat.configure(state="normal")
        self._write_chat_line(role, text)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _append_system(self, text, session_id=None):
        session_id = session_id or self.current_session
        if session_id:
            if session_id not in self.chat_logs:
                self.chat_logs[session_id] = []
            self.chat_logs[session_id].append(("System", text))
        if session_id != self.current_session:
            return
        self.chat.configure(state="normal")
        self.chat.insert("end", f"[{text}]\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _render_chat(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        if self.current_session:
            entries = self.chat_logs.get(self.current_session, [])
            stream_active = self._stream_active.get(self.current_session, False)
            for idx, (role, text) in enumerate(entries):
                is_last = idx == len(entries) - 1
                if role == "System":
                    self.chat.insert("end", f"[{text}]\n")
                elif stream_active and is_last and role == "Assistant":
                    self._write_chat_line(role, text, trailing="")
                else:
                    self._write_chat_line(role, text)
        self.chat.see("end")
        self.chat.configure(state="disabled")
        self._stream_ui_active = bool(self.current_session and self._stream_active.get(self.current_session))

    def _write_chat_line(self, role: str, text: str, trailing: str = "\n\n"):
        if role in {"You", "Assistant"}:
            self.chat.insert("end", f"{role}: ", "speaker_label")
            self.chat.insert("end", f"{text}{trailing}")
        else:
            self.chat.insert("end", f"{role}: {text}{trailing}")

    def _set_status(self, text):
        self.status.configure(text=text)
        self.root.update_idletasks()

    def _ui_call(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    def _send_text(self):
        if self.mode.get() != "text":
            return
        text = self.text_entry.get().strip()
        if not text:
            return
        self.text_entry.delete(0, "end")
        self._append_chat("You", text)
        self._run_async(self.assistant.handle_text, text, show_user=False)

    def _record_voice(self):
        if self.mode.get() != "voice":
            return
        if self.voice_record_mode.get() == "manual":
            if self._recording:
                self._stop_manual_record()
            else:
                self._start_manual_record()
        else:
            stop_tts()
            self._run_async(self.assistant.handle_voice, show_user=True, record_mode="auto")

    def _start_manual_record(self):
        self._record_stop_event = threading.Event()
        self._recording = True
        self.record_button.configure(text="Stop")
        stop_tts()
        self._run_async(
            self.assistant.handle_voice,
            show_user=True,
            record_mode="manual",
            stop_event=self._record_stop_event,
        )

    def _stop_manual_record(self):
        if self._record_stop_event:
            self._record_stop_event.set()
        self._recording = False
        self.record_button.configure(text="Record")

    def _run_async(self, fn, *args, show_user: bool, **kwargs):
        session_id = self.current_session
        if not session_id:
            return

        def target():
            stream_state = {"used": False}
            transcript_state = {"shown": False}

            def on_stream(delta: str):
                stream_state["used"] = True
                self.root.after(0, lambda: self._append_stream(session_id, delta))

            def on_action(intent_type: str, command: str):
                if intent_type == "zotero_command" and command in {"page_down", "page_up"}:
                    self.root.after(0, lambda: self._suspend_session_hotkeys(300))

            def on_transcript(text: str):
                transcript_state["shown"] = True
                self.root.after(0, lambda: self._append_chat("You", text, session_id))

            try:
                self._ui_call(self._set_status, "Working...")
                if not args:
                    result = fn(
                        session_id,
                        on_stream=on_stream,
                        on_action=on_action,
                        on_transcript=on_transcript,
                        **kwargs,
                    )
                else:
                    result = fn(
                        *args,
                        session_id,
                        on_stream=on_stream,
                        on_action=on_action,
                        on_transcript=on_transcript,
                        **kwargs,
                    )
                transcript = result.get("transcript", "")
                response = result.get("response", "")
                if transcript and show_user and not transcript_state["shown"]:
                    self._ui_call(self._append_chat, "You", transcript, session_id)
                if stream_state["used"]:
                    self._ui_call(self._end_stream, session_id)
                else:
                    self._ui_call(self._append_chat, "Assistant", response, session_id)
            except Exception as exc:
                self._ui_call(self._append_system, f"Error: {exc}", session_id)
            finally:
                self._ui_call(self._set_status, "Ready")
                if self.voice_record_mode.get() == "manual" and self._recording:
                    self._ui_call(self._stop_manual_record)

        threading.Thread(target=target, daemon=True).start()

    def _append_stream(self, session_id, delta):
        if session_id:
            if session_id not in self.chat_logs:
                return
            if not self._stream_active.get(session_id):
                self._stream_active[session_id] = True
                self.chat_logs[session_id].append(("Assistant", ""))
            role, text = self.chat_logs[session_id][-1]
            if role == "Assistant":
                self.chat_logs[session_id][-1] = (role, text + delta)

        if session_id != self.current_session:
            return

        self.chat.configure(state="normal")
        if not self._stream_ui_active:
            self._stream_ui_active = True
            self.chat.insert("end", "Assistant: ", "speaker_label")
        self.chat.insert("end", delta)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _end_stream(self, session_id):
        if not self._stream_active.get(session_id):
            return
        self._stream_active[session_id] = False
        if session_id != self.current_session:
            return
        self.chat.configure(state="normal")
        self.chat.insert("end", "\n\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")
        self._stream_ui_active = False


def main():
    root = tk.Tk()
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    app = AssistantUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
