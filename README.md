# Lumo AI

**A private AI assistant that runs entirely on your own laptop. No internet, no cloud, no data leaks.**

Lumo combines a local open-source language model with a small web app. It has four modes: **Chat**, **Write**, **Translate** and **Slides**. Conversations are saved locally, and the model remembers the context of each chat.

![Lumo AI interface](docs/screenshot-en.png)

## Features

- **Chat**: ask questions and get structured answers (a short answer first, then the details)
- **Write**: emails, posts and essays in the requested tone and length
- **Translate**: Russian ↔ English, only the translation, no extra comments
- **Slides**: give a topic and get a slide plan, speaker notes and a ready PowerPoint file (.pptx) to download
- **Chat history**: chats are stored in a local SQLite database and survive restarts
- **Context memory**: the model sees the last 20 messages of the current chat
- **Images**: attach a screenshot (📎, paste with Cmd+V or drag and drop), for example a test question, and Lumo reads it with a local vision model
- **"Think before answering"**: optional reasoning mode for harder questions (slower, more logical)
- **Fully offline**: no external requests, no CDNs, system fonts only
- English and Russian interface (auto-detected, switchable)
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

The backend adds a system prompt and a temperature for the chat's mode (chat 0.4, write 0.7, translate 0.1, slides 0.5), loads the chat history from SQLite and sends everything to the model through the Ollama API.

## Tech stack

| Layer | Technology |
|---|---|
| Model | Qwen3 8B (text) and Qwen3-VL 8B (images) via [Ollama](https://ollama.com) |
| Backend | Python, FastAPI, httpx, Uvicorn, python-pptx |
| Storage | SQLite (Python standard library) |
| Frontend | HTML, CSS, vanilla JavaScript (no frameworks, no build step) |

## Getting started (macOS)

Tested on a MacBook Air (Apple Silicon, 16 GB RAM).

1. Install [Ollama](https://ollama.com/download) and download the model:
   ```bash
   ollama pull qwen3:8b      # text
   ollama pull qwen3-vl:8b   # images (optional, ≈6 GB)
   ```
2. Clone the repository and install the dependencies:
   ```bash
   git clone https://github.com/surnova2703/lumo-ai.git
   cd lumo-ai
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Start the server:
   ```bash
   uvicorn main:app --reload
   ```
4. Open http://127.0.0.1:8000. The API docs are at http://127.0.0.1:8000/docs.

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
├── static/index.html  # web interface
├── requirements.txt
└── docs/              # screenshots and presentations
```

## Roadmap

- [ ] Streaming answers (word by word)
- [ ] Desktop app for macOS (Tauri)
- [ ] Model selection in the interface
- [ ] Ask questions about your own documents (PDF, DOCX) with local RAG
- [ ] Password protection for access from other devices

## Presentations

The start screen has an **About the product** button: choose a language, then an introduction or a technical presentation, and the slides open right inside the app.

| Presentation | PowerPoint | PDF |
|---|---|---|
| Product presentation (English) | [pptx](docs/Lumo_AI_presentation_EN.pptx) | [pdf](docs/Lumo_AI_presentation_EN.pdf) |
| Презентация о продукте (русский) | [pptx](docs/Lumo_AI_presentation_RU.pptx) | [pdf](docs/Lumo_AI_presentation_RU.pdf) |
| Technical presentation (English) | [pptx](docs/Lumo_AI_tech_EN.pptx) | [pdf](docs/Lumo_AI_tech_EN.pdf) |
| Техническая презентация (русский) | [pptx](docs/Lumo_AI_tech_RU.pptx) | [pdf](docs/Lumo_AI_tech_RU.pdf) |

---

Author: Varvara Surnova · [@surnova2703](https://github.com/surnova2703)
