import threading
import tkinter as tk
from tkinter import ttk

from core import Assistant


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

        self._build_ui()
        self._add_session()

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
            self.text_entry.configure(state="normal")
            self.send_button.configure(state="normal")
            self.record_button.configure(state="disabled")

    def _add_session(self):
        name = f"Session {len(self.sessions) + 1}"
        self.sessions.append(name)
        self.chat_logs[name] = []
        self._stream_active[name] = False
        self.session_list.insert("end", name)
        self.session_list.select_clear(0, "end")
        self.session_list.select_set("end")
        self.current_session = name
        self._render_chat()
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
        self.chat.insert("end", f"{role}: {text}\n")
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
                    self.chat.insert("end", f"{role}: {text}")
                else:
                    self.chat.insert("end", f"{role}: {text}\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")
        self._stream_ui_active = bool(self.current_session and self._stream_active.get(self.current_session))

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
        self._run_async(self.assistant.handle_voice, show_user=True)

    def _run_async(self, fn, *args, show_user: bool):
        session_id = self.current_session
        if not session_id:
            return

        def target():
            stream_state = {"used": False}

            def on_stream(delta: str):
                stream_state["used"] = True
                self.root.after(0, lambda: self._append_stream(session_id, delta))

            try:
                self._ui_call(self._set_status, "Working...")
                result = fn(session_id, on_stream=on_stream) if not args else fn(*args, session_id, on_stream=on_stream)
                transcript = result.get("transcript", "")
                response = result.get("response", "")
                if transcript and show_user:
                    self._ui_call(self._append_chat, "You", transcript, session_id)
                if stream_state["used"]:
                    self._ui_call(self._end_stream, session_id)
                else:
                    self._ui_call(self._append_chat, "Assistant", response, session_id)
            except Exception as exc:
                self._ui_call(self._append_system, f"Error: {exc}", session_id)
            finally:
                self._ui_call(self._set_status, "Ready")

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
            self.chat.insert("end", "Assistant: ")
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
        self.chat.insert("end", "\n")
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
