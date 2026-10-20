import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:8b"
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "lumo.db"
HISTORY_LIMIT = 20  # сколько последних сообщений модель «помнит» в чате

Mode = Literal["chat", "write", "translate"]

# Инструкции для каждого режима
MODES = {
    "chat": (
        "You are Lumo, a helpful offline assistant. "
        "Always reply in the same language as the user. "
        "Start with a short direct answer, then explain step by step if needed. "
        "Be logical and precise. If you are not sure about a fact, say so instead of guessing. "
        "Use the earlier messages of this conversation as context."
    ),
    "write": (
        "You are Lumo, a writing assistant. Write in the same language as the request. "
        "Follow the requested format, length and tone exactly. "
        "Structure the text clearly: a beginning, the main part and an ending. "
        "Return only the finished text, without comments about it."
    ),
    "translate": (
        "You are a professional translator. If the text is in Russian, translate it to English. "
        "Otherwise translate it to Russian. Keep the meaning, tone and formatting. "
        "Output only the translation, with no explanations."
    ),
}

# «Температура»: ниже = точнее и предсказуемее, выше = креативнее
TEMPERATURE = {"chat": 0.4, "write": 0.7, "translate": 0.1}

app = FastAPI(title="Lumo AI")

# Презентации о проекте (PDF из папки docs) открываются с начального экрана
if (BASE_DIR / "docs").is_dir():
    app.mount("/presentations", StaticFiles(directory=BASE_DIR / "docs"), name="presentations")


# ---------- база данных (история чатов) ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


init_db()


def now():
    return datetime.now().isoformat(timespec="seconds")


def get_chat_or_404(conn, chat_id: int):
    chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


# ---------- обращение к модели ----------

async def call_model(mode: str, messages: List[dict], think: bool) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": MODES[mode]}] + messages,
        "stream": False,
        "think": think,
        "options": {"temperature": TEMPERATURE[mode]},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ollama is not available: {e}")
    return r.json()["message"]["content"].strip()


# ---------- модели запросов ----------

class AskRequest(BaseModel):
    mode: Mode = "chat"
    text: str
    think: bool = False  # True = модель сначала «думает»: дольше, но логичнее


class NewChat(BaseModel):
    mode: Mode = "chat"


class ChatMessage(BaseModel):
    text: str
    think: bool = False


class ChatUpdate(BaseModel):
    mode: Mode


# ---------- эндпоинты ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/ask", summary="Быстрый вопрос без сохранения")
async def ask(req: AskRequest):
    answer = await call_model(req.mode, [{"role": "user", "content": req.text}], req.think)
    return {"mode": req.mode, "answer": answer}


@app.post("/chats", summary="Создать новый чат")
def create_chat(req: NewChat):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO chats (title, mode, created_at) VALUES (?, ?, ?)",
            ("New chat", req.mode, now()),
        )
        return {"id": cur.lastrowid, "mode": req.mode}


@app.get("/chats", summary="Список чатов")
def list_chats():
    with db() as conn:
        rows = conn.execute("SELECT * FROM chats ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


@app.get("/chats/{chat_id}", summary="Чат со всей историей")
def get_chat(chat_id: int):
    with db() as conn:
        chat = get_chat_or_404(conn, chat_id)
        msgs = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
        return {**dict(chat), "messages": [dict(m) for m in msgs]}


@app.post("/chats/{chat_id}/ask", summary="Написать в чат (модель помнит контекст)")
async def ask_in_chat(chat_id: int, req: ChatMessage):
    with db() as conn:
        chat = get_chat_or_404(conn, chat_id)
        history = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, HISTORY_LIMIT),
        ).fetchall()
    messages = [dict(m) for m in reversed(history)]
    messages.append({"role": "user", "content": req.text})

    answer = await call_model(chat["mode"], messages, req.think)

    with db() as conn:
        conn.executemany(
            "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            [(chat_id, "user", req.text, now()), (chat_id, "assistant", answer, now())],
        )
        if chat["title"] == "New chat":  # название чата = начало первого сообщения
            title = req.text[:40] + ("…" if len(req.text) > 40 else "")
            conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
    return {"chat_id": chat_id, "answer": answer}


@app.patch("/chats/{chat_id}", summary="Сменить режим чата")
def update_chat(chat_id: int, req: ChatUpdate):
    with db() as conn:
        get_chat_or_404(conn, chat_id)
        conn.execute("UPDATE chats SET mode = ? WHERE id = ?", (req.mode, chat_id))
    return {"id": chat_id, "mode": req.mode}


@app.delete("/chats/{chat_id}", summary="Удалить чат")
def delete_chat(chat_id: int):
    with db() as conn:
        get_chat_or_404(conn, chat_id)
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return {"deleted": chat_id}
