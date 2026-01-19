import time

from core import Assistant


def main() -> int:
    assistant = Assistant()
    session_id = "cli"
    while True:
        print("Listening...", flush=True)
        result = assistant.handle_voice(session_id)
        transcript = result.get("transcript", "")
        response = result.get("response", "")
        if not transcript:
            print(response, flush=True)
            continue
        print(f"You: {transcript}", flush=True)
        print(response, flush=True)
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(main())
