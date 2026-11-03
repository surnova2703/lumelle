# Lumo AI

**A private AI assistant that runs entirely on your own laptop. No internet, no cloud, no data leaks.**

Lumo runs on Windows, macOS and Linux. It combines a local open-source language model with a small web app. It has five modes: **Chat**, **Write**, **Translate**, **Slides** and **Learn**. Conversations are saved locally, and the model remembers the context of each chat.

![Lumo AI interface](docs/screenshot-en.png)

## Features

- **Any language**: every mode answers in the language you write in, or in the one you ask for
- **Chat**: ask questions and get structured answers (a short answer first, then the details)
- **Write**: emails, posts and essays in the requested tone and length
- **Translate**: any language pair — name the target ("to Spanish: …"); by default Russian → English and everything else → Russian
- **Slides**: give a topic and get a slide plan, speaker notes and a ready PowerPoint file (.pptx) to download
- **Learn**: a language tutor (English by default, or any language you name): finds your level, explains grammar, gives exercises and checks them, teaches words, practises conversation and corrects your mistakes
- **Chat history**: chats are stored in a local SQLite database and survive restarts
- **Context memory**: the model sees the last 20 messages of the current chat
- **Images**: attach a screenshot (📎, paste with Ctrl+V / Cmd+V or drag and drop), for example a test question, and Lumo reads it with a local vision model
- **"Think before answering"**: optional reasoning mode for harder questions (slower, more logical)
- **Fully offline**: no external requests, no CDNs, system fonts only
- Interface in Russian, English, Chinese, French and German (auto-detected, switchable)
- Light and dark theme, mobile layout

## How it works

```
Browser (static/index.html)
        │  HTTP, localhost
        ▼
FastAPI backend (main.py) ──── SQLite (lumo.db): chats and messages
        │  HTTP, localhost:11434
        ▼
Ollama ──── qwen3:8b (local LLM)
```

The backend adds a system prompt and a temperature for the chat's mode (chat 0.4, write 0.7, translate 0.1, slides 0.5, learn 0.4), loads the chat history from SQLite and sends everything to the model through the Ollama API.

## Tech stack

| Layer | Technology |
|---|---|
| Model | Qwen3 8B (text) and Qwen3-VL 8B (images) via [Ollama](https://ollama.com) |
| Backend | Python, FastAPI, httpx, Uvicorn, python-pptx |
| Storage | SQLite (Python standard library) |
| Frontend | HTML, CSS, vanilla JavaScript (no frameworks, no build step) |

## Getting started

Lumo runs on **Windows, macOS and Linux**. You need [Python 3.9+](https://www.python.org/downloads/) and [Ollama](https://ollama.com/download). The internet is needed only for the first setup.

1. Install Ollama and download the models:
   ```bash
   ollama pull qwen3:8b      # text
   ollama pull qwen3-vl:8b   # images (optional, ≈6 GB)
   ```
2. Download the project (`git clone https://github.com/surnova2703/lumo-ai.git` or **Code → Download ZIP**).
3. Start it:

   | System | How to start |
   |---|---|
   | Windows | double-click `start.bat` |
   | macOS | double-click `start.command` (or run `./start.sh` in Terminal) |
   | Linux | run `./start.sh` |

   The first start creates a Python environment and installs the libraries. Then the browser opens http://127.0.0.1:8000.

Step-by-step instructions for each system: **[Installation guide (English)](INSTALL_EN.md)** · **[Инструкция по установке (русский)](INSTALL_RU.md)**. The same guide opens inside Lumo with the **Installation** button on the start screen.

Options: `--model` picks the text model, `--keep-alive 0` frees memory right after each answer (by default the model stays loaded for 30 minutes and is preloaded at start, so answers come faster), `--lan` lets other devices in your network (or Tailscale) open Lumo, `--port 9000` changes the port, `--no-browser` doesn't open the browser.

### Hardware

| Computer | What works well |
|---|---|
| Apple Silicon Mac, 16 GB | `qwen3:8b` (tested on a MacBook Air) |
| Windows / Linux with an NVIDIA GPU (8 GB+) | `qwen3:8b`, fast |
| Laptop without a GPU, 8–16 GB RAM | `qwen3:4b` — slower, but works |

A smaller model is chosen at start: `start.bat --model qwen3:4b` on Windows, `./start.sh --model qwen3:4b` on macOS/Linux (download it first with `ollama pull qwen3:4b`). The image model: `--vision-model qwen3-vl:4b`.

Phones and tablets (iPhone, Android) open Lumo in the browser when the computer runs it with `--lan`.

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ask` | One-off question, not saved: `{"mode": "chat", "text": "...", "think": false}` |
| `POST` | `/chats` | Create a chat: `{"mode": "translate"}` |
| `GET` | `/chats` | List chats |
| `GET` | `/chats/{id}` | Get a chat with all its messages |
| `POST` | `/chats/{id}/ask` | Send a message in a chat (with context): `{"text": "...", "think": false}` |
| `PATCH` | `/chats/{id}` | Change the chat's mode |
| `DELETE` | `/chats/{id}` | Delete a chat |
| `GET` | `/decks/{file}` | Download a presentation made in Slides mode |

## Project structure

```
lumo-ai/
├── main.py            # FastAPI backend: modes, chat history, Ollama client
├── slides.py          # Slides mode: builds .pptx files from the model's outline
├── run.py             # cross-platform launcher (checks Ollama, opens the browser)
├── start.bat / start.sh / start.command  # double-click starters
├── static/index.html  # web interface
├── INSTALL_EN.md / INSTALL_RU.md  # installation guides (also shown in the app)
├── requirements.txt
└── docs/              # screenshots and presentations
```

## Roadmap

- [ ] Streaming answers (word by word)
- [ ] Desktop app for Windows, macOS and Linux (Tauri)
- [ ] Model selection in the interface
- [ ] Ask questions about your own documents (PDF, DOCX) with local RAG
- [ ] Password protection for access from other devices

## Presentations

The start screen has an **About the product** button: choose a language, then an introduction or a technical presentation, and the slides open right inside the app.

| Presentation | PowerPoint | PDF |
|---|---|---|
| Introduction · English | [pptx](docs/Lumo_AI_presentation_EN.pptx) | [pdf](docs/Lumo_AI_presentation_EN.pdf) |
| Technical · English | [pptx](docs/Lumo_AI_tech_EN.pptx) | [pdf](docs/Lumo_AI_tech_EN.pdf) |
| Introduction · Русский | [pptx](docs/Lumo_AI_presentation_RU.pptx) | [pdf](docs/Lumo_AI_presentation_RU.pdf) |
| Technical · Русский | [pptx](docs/Lumo_AI_tech_RU.pptx) | [pdf](docs/Lumo_AI_tech_RU.pdf) |
| Introduction · 中文 | [pptx](docs/Lumo_AI_presentation_ZH.pptx) | [pdf](docs/Lumo_AI_presentation_ZH.pdf) |
| Technical · 中文 | [pptx](docs/Lumo_AI_tech_ZH.pptx) | [pdf](docs/Lumo_AI_tech_ZH.pdf) |
| Introduction · Français | [pptx](docs/Lumo_AI_presentation_FR.pptx) | [pdf](docs/Lumo_AI_presentation_FR.pdf) |
| Technical · Français | [pptx](docs/Lumo_AI_tech_FR.pptx) | [pdf](docs/Lumo_AI_tech_FR.pdf) |
| Introduction · Deutsch | [pptx](docs/Lumo_AI_presentation_DE.pptx) | [pdf](docs/Lumo_AI_presentation_DE.pdf) |
| Technical · Deutsch | [pptx](docs/Lumo_AI_tech_DE.pptx) | [pdf](docs/Lumo_AI_tech_DE.pdf) |

---

Author: Varvara Surnova · [@surnova2703](https://github.com/surnova2703)
