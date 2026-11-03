"""Starts Lumo AI on Windows, macOS or Linux and opens it in the browser.

Usage:  python run.py              (only this computer)
        python run.py --lan        (also other devices on your Wi-Fi / Tailscale)
        python run.py --port 9000
        python run.py --model qwen3:4b   (a smaller model for weaker computers)
        python run.py --keep-alive 0     (unload the model right after answering, to free memory)
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)


def check_ollama():
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    text_model = os.environ.get("LUMO_MODEL", "qwen3:8b")
    vision_model = os.environ.get("LUMO_VISION_MODEL", "qwen3-vl:8b")
    try:
        tags = httpx.get(host + "/api/tags", timeout=3).json().get("models", [])
    except Exception:
        print("! Ollama is not running. Install it from https://ollama.com/download and start it.")
        return
    names = {m.get("name", "") for m in tags}

    def have(model):
        return model in names or (":" not in model and model + ":latest" in names)

    for model, what in ((text_model, "text"), (vision_model, "images, optional")):
        if have(model):
            print(f"  ok  {model} ({what})")
        else:
            print(f"  !   {model} ({what}) is not downloaded. Run:  ollama pull {model}")


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Lumo AI")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true", help="allow other devices in your network")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--model", help="text model, e.g. qwen3:4b")
    parser.add_argument("--vision-model", help="image model, e.g. qwen3-vl:4b")
    parser.add_argument("--keep-alive", help="how long the model stays in memory, e.g. 30m, 2h or 0 (default 30m)")
    args = parser.parse_args()
    if args.model:
        os.environ["LUMO_MODEL"] = args.model
    if args.vision_model:
        os.environ["LUMO_VISION_MODEL"] = args.vision_model
    if args.keep_alive:
        os.environ["LUMO_KEEP_ALIVE"] = args.keep_alive

    print("Lumo AI")
    check_ollama()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Open: {url}")
    if args.lan:
        ip = local_ip()
        if ip:
            print(f"  From other devices: http://{ip}:{args.port}")
        print("  Note: Lumo has no password yet — use --lan only in a network you trust.")
    print("  Stop: Ctrl+C\n")

    if not args.no_browser:
        def open_when_ready():
            # открываем браузер, как только сервер ответил (а не через фиксированную паузу)
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            webbrowser.open(url)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("main:app", host=host, port=args.port)


if __name__ == "__main__":
    main()
