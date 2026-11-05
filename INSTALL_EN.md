# Lumelle AI — Installation guide

[Русская версия](INSTALL_RU.md)

Lumelle installs in four steps: Python, Ollama, a model and Lumelle itself. You only need the internet during installation; after that everything works offline. Pick the section for your system.

## What you need

You need a computer with Windows 10+, macOS 12+ or Linux, at least 8 GB of RAM and about 15 GB of free disk space. Choose the model that fits your computer.

| Computer | Text model | Disk space |
| --- | --- | --- |
| Apple Silicon Mac (M1–M4), 16 GB | `qwen3:8b` | ≈ 5 GB |
| Windows / Linux with an NVIDIA GPU, 8 GB+ | `qwen3:8b` | ≈ 5 GB |
| Laptop without a GPU, 8–16 GB | `qwen3:4b` | ≈ 2.5 GB |

To work with images you need one more model: `qwen3-vl:8b` (≈ 6 GB), or `qwen3-vl:4b` for weaker computers. Everything else works without it.

You can download Lumelle from GitHub (**Code → Download ZIP**) or get the archive from the author. The repository is private for now, so other people can’t open it by the link.

## Windows

1. **Python.** Download the installer from [python.org/downloads](https://www.python.org/downloads/) and run it. On the first screen, be sure to tick **Add python.exe to PATH**, then click **Install Now**.
2. **Ollama.** Download the Windows version from [ollama.com/download](https://ollama.com/download) and install it. After installation a llama icon appears in the system tray at the bottom right.
3. **Model.** Open Command Prompt (Start → type `cmd`) and run the command from the table above, for example `ollama pull qwen3:8b`. Optionally add the image model: `ollama pull qwen3-vl:8b`.
4. **Lumelle.** Unpack the archive: right-click → **Extract All**. Don’t run it straight from the ZIP.
5. **Start.** Double-click `start.bat`. If you see “Windows protected your PC”, click **More info → Run anyway**.

On a weaker computer, start it from Command Prompt in the Lumelle folder: `start.bat --model qwen3:4b`.

## macOS

1. **Python.** Open Terminal (`Cmd + Space` → “Terminal”) and run `xcode-select --install`. Click **Install** in the window that appears. Python comes with these developer tools. Check it: `python3 --version`.
2. **Ollama.** Download the Mac version from [ollama.com/download](https://ollama.com/download), move it to Applications and open it. A llama icon appears in the menu bar.
3. **Model.** In Terminal: `ollama pull qwen3:8b` and, if you want images, `ollama pull qwen3-vl:8b`.
4. **Lumelle.** Double-click the archive to unpack it and move the folder somewhere handy, for example your home folder.
5. **Start.** Double-click `start.command`. The first time, macOS may say the file is from an unidentified developer: right-click the file → **Open** → **Open**.

If the file doesn’t start, open Terminal in the Lumelle folder and run `bash start.sh`.

## Linux

The commands are for Ubuntu and Debian; other distributions use the same package names.

1. **Python:** `sudo apt install python3 python3-venv`
2. **Ollama:** `curl -fsSL https://ollama.com/install.sh | sh`. The service starts by itself.
3. **Model:** `ollama pull qwen3:8b` (or `qwen3:4b`), and for images `ollama pull qwen3-vl:8b`.
4. **Lumelle:** unpack the archive and go to the folder: `cd lumelle`.
5. **Start:** `./start.sh` (if it isn’t allowed to run: `bash start.sh`).

On a computer with an NVIDIA GPU, install the graphics driver first: then Ollama uses the GPU and runs much faster.

## First start

The first start takes 1–3 minutes: the script creates a Python environment and installs the libraries. Then it checks Ollama and the models and opens Lumelle in the browser at http://127.0.0.1:8000. Later starts take a couple of seconds and work without the internet.

Keep the text window open while you use Lumelle. To stop it, press `Ctrl + C` or just close the window.

**Sign-in.** Lumelle has two accounts: `admin` (owner, password `admin`) and `guest` (password `guest`). Each has its own chats. The passwords are simple, so change them right away: sign in as admin and press 👥 at the bottom of the sidebar. There you can also add users: each gets their own chats and replaces the temporary password at first sign-in.

| Option | What it does |
| --- | --- |
| `--model qwen3:4b` | another text model |
| `--vision-model qwen3-vl:4b` | another image model |
| `--lan` | access from other devices in the same network |
| `--port 9000` | another port, if 8000 is busy |
| `--no-browser` | don’t open the browser automatically |

Add options to the start command: `start.bat --lan` on Windows or `./start.sh --lan` on macOS and Linux.

### From a phone or tablet

1. Start Lumelle on the computer with the `--lan` option.
2. The start window shows a line like `From other devices: http://192.168.…:8000`.
3. Open this address in the browser of a phone (iPhone or Android) connected to the same Wi‑Fi.
4. Add the page to the home screen so Lumelle opens like an app.

While the default passwords are set, use `--lan` only on your home network or through Tailscale. In a café or a dorm, strangers could open your chats.

## Voice

- **🔊 Read aloud** under every answer and the **“Read answers aloud”** checkbox at the bottom. The system voices speak (on a Mac you can add more in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices); no internet needed.
- **🎤 Speak:** press, talk, press again. A Whisper model recognises the speech right on the computer. The first time it is downloaded (about 0.5 GB), which needs the internet. With “Read answers aloud” on, what you say is sent at once and Lumelle answers out loud, so it works as a conversation.
- A smaller, faster model: `--whisper-model base`; a more accurate, slower one: `--whisper-model medium`.
- **Microphone on a phone.** Browsers allow the microphone only on a secure (https) address. Tailscale can provide one: in its admin console (login.tailscale.com → DNS) turn on HTTPS Certificates, then run on the Mac
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8000`
  and open the `https://your-mac.something.ts.net` address it prints on the phone. Reading aloud works without this.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `Python 3.9+ is required` or “'python' is not recognized” | Reinstall Python with **Add python.exe to PATH** ticked (Windows) or run `xcode-select --install` (macOS) |
| `Read timed out` or `Could not install the libraries` | Slow internet. Run the script again: what was already downloaded is kept. A different network can help |
| `Ollama is not running` | Open the Ollama app (the llama icon should appear); on Linux run `ollama serve` |
| `Model qwen3:8b is not installed` | Run the command from the message, for example `ollama pull qwen3:8b` |
| Answers are very slow | Use a smaller model: `--model qwen3:4b`. Close heavy programs. Tick “Think” only for hard questions |
| Images aren’t recognised | Download the image model: `ollama pull qwen3-vl:8b` |
| “Unidentified developer” (macOS) | Right-click `start.command` → **Open** |
| “Windows protected your PC” | **More info → Run anyway** |
| `address already in use` | Port 8000 is busy: Lumelle is already running in another window, or start it with `--port 9000` |
| It doesn’t open from a phone | Check `--lan` and that both are on the same Wi‑Fi; on Windows allow Python in the firewall when asked |

## How to update and uninstall

**Update.** Download the new version and copy its files over the old ones, replacing them. Chats are stored in `lumelle.db` and stay in place. If the new version adds libraries, delete the file `.venv/.deps-ok`: they will install themselves on the next start.

**Uninstall.**

1. Delete the Lumelle folder: the Python environment, chats and generated presentations go with it.
2. Remove the models to free up space: `ollama rm qwen3:8b` and `ollama rm qwen3-vl:8b`.
3. If you don’t need Ollama anymore, uninstall it like any other app.
