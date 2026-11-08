import asyncio
import hashlib
import hmac
import secrets
import json
import os
import re
import sqlite3
import time
from array import array
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cloud
from docs_search import extract_text, hybrid, split_chunks
from slides import SLIDES_SCHEMA, build_pptx, clean_deck, outline_markdown

# Настройки можно поменять переменными окружения (удобно для слабых компьютеров):
#   LUMELLE_MODEL=qwen3:4b  LUMELLE_VISION_MODEL=qwen3-vl:4b  OLLAMA_HOST=http://localhost:11434
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/api/chat"
MODEL = os.environ.get("LUMELLE_MODEL", "qwen3:8b")                # модель для текста
VISION_MODEL = os.environ.get("LUMELLE_VISION_MODEL", "qwen3-vl:8b")  # модель для картинок
# Сколько модель остаётся в памяти после ответа (по умолчанию Ollama выгружает её через 5 минут,
# и следующий ответ снова ждёт загрузку). "0" — выгружать сразу, если памяти мало.
KEEP_ALIVE = os.environ.get("LUMELLE_KEEP_ALIVE", "30m")
# Распознавание речи (кнопка 🎤): локальная модель Whisper. tiny / base / small / medium:
# чем больше, тем точнее и медленнее. small (~0,5 ГБ) хорошо понимает русский.
WHISPER_MODEL = os.environ.get("LUMELLE_WHISPER", "small")
# Поиск по смыслу в документах: модель «эмбеддингов» в Ollama. Если она не скачана,
# работает обычный поиск по словам. Скачать один раз (нужен интернет): ollama pull qwen3-embedding:0.6b
EMBED_MODEL = os.environ.get("LUMELLE_EMBED", "qwen3-embedding:0.6b")
# Сколько текста модель видит за раз (в токенах). 8192 хватает на документы и историю чата;
# Ollama по умолчанию даёт меньше и молча обрезает начало.
NUM_CTX = int(os.environ.get("LUMELLE_CTX", "8192"))
MAX_AUDIO_BYTES = 25_000_000  # ≈ несколько минут речи
MAX_IMAGES = 4
MAX_IMAGE_B64 = 8_000_000  # ≈6 МБ на картинку
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "lumelle.db"
DECKS_DIR = BASE_DIR / "generated"  # презентации, созданные в режиме Slides
HISTORY_LIMIT = 20  # сколько последних сообщений модель «помнит» в чате

Mode = Literal["chat", "write", "translate", "slides", "learn", "science"]

# Инструкции для каждого режима
MODES = {
    "chat": (
        "You are Lumelle, a helpful offline assistant. "
        "Reply in the same language as the user (any language), unless the user asks for another language. "
        "Start with a short direct answer, then explain step by step if needed. "
        "Be logical and precise. If you are not sure about a fact, say so instead of guessing. "
        "If the user sends an image (for example a screenshot of a test or a task), read all the text "
        "in it carefully first. For test questions give the correct option, then a short explanation. "
        "Use the earlier messages of this conversation as context."
    ),
    "write": (
        "You are Lumelle, a writing assistant. Write in the same language as the request, "
        "unless the user asks for the text in another language (for example 'write in German'). "
        "If an image is attached, use what is in it as the material for the text. "
        "Follow the requested format, length and tone exactly. "
        "Structure the text clearly: a beginning, the main part and an ending. "
        "Return only the finished text, without comments about it."
    ),
    "translate": (
        "You are a professional translator who works with any language. "
        "If the user names a target language (for example 'на испанский:', 'to German', 'in Chinese'), translate into it. "
        "If no language is named: text in {ui} goes to {other}, text in any other language goes to {ui}. "
        "If an image is attached, translate the text in the image. "
        "Keep the meaning, tone and formatting; translate idioms naturally, not word for word. "
        "Output only the translation, with no explanations. If the user asks about a word or phrase, "
        "you may add a short note with variants."
    ),
    "slides": (
        "You are Lumelle, a presentation designer. Turn the user's request into a slide outline. "
        "Write in the same language as the request (any language), unless the user asks for another language. "
        "Fill 'labels' in that same language: 'presentation' (the word 'Presentation'), 'thanks' ('Thank you for your attention!'), "
        "'questions' ('Any questions?'), 'download' ('Download the presentation'). Make 5-8 content slides unless the user asks for another number. "
        "Keep slides minimal: a short title (up to 6 words) and 2-4 bullets of up to 8 words each. "
        "Put what the speaker should say in 'notes' (2-4 simple sentences). "
        "Do not add a title slide or a thank-you slide: they are added automatically. "
        "'title' is the presentation title, 'subtitle' is one short line. "
        "If an image is attached, use it as material. If the user asks to change the previous presentation, "
        "return the whole updated presentation."
    ),
    "learn": (
        "You are Lumelle, a patient and friendly language tutor. By default the student is a Russian speaker learning English; "
        "if the student names another language, teach that one. Explain rules and give feedback in the student's own language "
        "(the language they write to you in), and write examples and exercises in the language being learned.\n"
        "How to teach:\n"
        "- If you don't know the student's level yet, ask 3-5 short questions of growing difficulty, one message, "
        "then name the level (A1-C1) and suggest what to practise.\n"
        "- Explain a rule briefly: one simple sentence for the rule, a short pattern, 2-3 examples with translation.\n"
        "- Give exercises in small portions (3-5 numbered items) and WAIT for the student's answers. Never give the answers "
        "in the same message as the exercise.\n"
        "- When checking answers, go item by item: ✅ correct, or ❌ with the correct version and a one-line reason. "
        "End with a short score like 4/5 and one next step.\n"
        "- When the student writes in the language being learned, first answer the content, then add a short "
        "'Corrections' part with their mistakes (wrong → right, why). If there are no mistakes, praise briefly.\n"
        "- For vocabulary give 5-8 words or phrases with transcription, translation and an example sentence, "
        "then a quick check exercise.\n"
        "- In conversation practice stay in role, keep your replies short (2-4 sentences) and end with a question.\n"
        "- If the student sends an image (a textbook page, a test, a worksheet), read it carefully and help with it, "
        "explaining why each answer is correct.\n"
        "Adapt the difficulty to the student's results. Be encouraging but accurate: never call a wrong answer right. "
        "Use the earlier messages of this conversation as context."
    ),
    "science": (
        "You are Lumelle, a careful research assistant for a student writing a thesis or dissertation. "
        "Reply in the user's language (any language); for Russian use a clear academic style.\n"
        "Rules you never break:\n"
        "- Never invent sources, authors, titles, years, page numbers, quotes, statistics or survey results. "
        "Cite only the user's documents (as [1], [2] … from the excerpts) or sources the user named. "
        "If a claim needs a source you don't have, write [нужен источник] / [source needed].\n"
        "- If excerpts from the user's documents are given, rely on them first. Clearly separate what comes from "
        "the documents and what is your general knowledge or suggestion.\n"
        "- If you are not sure, say so. Do not fill gaps with plausible-sounding details.\n"
        "- When editing the user's text, keep their meaning and voice; show what you changed and why.\n"
        "- Be structured: short headings, numbered lists, tables where they help.\n"
        "- For calculations show the formula, the numbers and the result, step by step.\n"
        "- The work belongs to the student: explain your reasoning so they can check it and write in their own words.\n"
        "If an image is attached (a page, a table, a chart, a task), read it carefully first. "
        "Use the earlier messages of this conversation as context."
    ),
}

# Команды «/…»: короткое слово в начале сообщения заменяется подробной инструкцией.
# У каждой встроенной команды есть русское и английское имя.
COMMANDS = {
    "plan": {
        "names": ("план", "plan"),
        "prompt": (
            "Make a plan (outline) for a thesis/dissertation on the topic below. If no topic is given, ask for the topic, "
            "the field and the type of work (bachelor, master, PhD) and stop. Structure:\n"
            "1) Introduction: relevance, problem, aim, 4-6 objectives (matching the chapters), object, subject, "
            "hypothesis (if suitable), methods, scientific novelty, practical significance.\n"
            "2) Chapter 1 (theory), Chapter 2 (analysis), Chapter 3 (proposals / solution) — each with 2-4 numbered "
            "sections (1.1, 1.2 …) and one line on what goes into each section.\n"
            "3) Conclusion and the kinds of sources needed for the reference list (do not invent concrete titles).\n"
            "If the user's documents are given, build the plan on them and mention which document fits which section. "
            "End with 2-3 questions that would help to make the plan more precise."
        ),
    },
    "review": {
        "names": ("обзор", "review"),
        "prompt": (
            "Write a literature review on the topic below, based ONLY on the user's documents (the excerpts [1], [2] …). "
            "If no excerpts are given, do not invent any sources: say that the user should upload the articles in "
            "«Documents» and turn on «From my documents», then suggest what kinds of sources to look for and stop.\n"
            "Structure: 1) the main approaches or groups of views; 2) for each source — author and year only if they are "
            "in the text, the main idea, the method, the findings, with a reference [n]; 3) comparison: where the authors "
            "agree and disagree; 4) the gap — what is not studied enough, which the student's work can fill; "
            "5) a reference list of ONLY these sources in the style of GOST R 7.0.100-2018, leaving [нет данных] where "
            "the documents lack a detail."
        ),
    },
    "test": {
        "names": ("тест", "test"),
        "prompt": (
            "Go through the test or tasks below (text or image) and explain each one step by step, so the student "
            "understands the topic. For every question: 1) the question in a few words; 2) the correct answer; "
            "3) why it is correct — the rule, definition or formula, and for calculations every step with the numbers; "
            "4) briefly why the other options are wrong. If a question is ambiguous or you are not sure, say so and "
            "explain both readings. If the user's documents are given, check the answers against them and cite [n]. "
            "End with a short table: question → answer → key idea, and 1-2 topics worth repeating."
        ),
    },
    "edit": {
        "names": ("редактура", "edit"),
        "prompt": (
            "Review the text below as a strict but kind academic supervisor. Do NOT rewrite the whole text.\n"
            "1) Overall impression in 2-3 sentences and a mark: ready / needs minor edits / needs serious work.\n"
            "2) Structure and logic: is the aim clear, do the parts connect, are the conclusions supported.\n"
            "3) Scientific style: colloquial words, repetitions, too long sentences, vague words, wrong terms.\n"
            "4) Claims without a source — mark them [нужен источник].\n"
            "5) A table «Было → Стало → Почему» with the 5-10 most important fixes (spelling, grammar, punctuation, style).\n"
            "6) Three priorities: what to fix first.\n"
            "If no text is given, ask the user to paste it."
        ),
    },
}
COMMAND_NAMES = {name: key for key, c in COMMANDS.items() for name in c["names"]}
COMMAND_RE = re.compile(r"^/([\w-]{1,32})(?:\s+|$)(.*)", re.S)

# «Температура»: ниже = точнее и предсказуемее, выше = креативнее
# Interface languages: the model uses them for the default translation direction
# and for messages that have no clear language (for example only an image)
UI_LANGS = {"ru": "Russian", "en": "English", "zh": "Chinese", "fr": "French", "de": "German"}


def system_prompt(mode: str, ui_lang: Optional[str]) -> str:
    ui = UI_LANGS.get(ui_lang or "ru", "Russian")
    other = "Russian" if ui == "English" else "English"
    prompt = MODES[mode].replace("{ui}", ui).replace("{other}", other)
    return prompt + f" The user's interface language is {ui}: use it when the message itself has no clear language."


TEMPERATURE = {"chat": 0.4, "write": 0.7, "translate": 0.1, "slides": 0.5, "learn": 0.4, "science": 0.3}

app = FastAPI(title="Lumelle AI")


@app.on_event("startup")
async def warm_up_model():
    """Загружаем текстовую модель в память заранее, пока открывается браузер:
    тогда первый ответ приходит без долгого ожидания."""
    async def load():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                await client.post(OLLAMA_URL.replace("/api/chat", "/api/generate"),
                                  json={"model": local_model(), "keep_alive": KEEP_ALIVE})
        except httpx.HTTPError:
            pass  # Ollama не запущена — run.py уже подсказал, что делать
    asyncio.create_task(load())
    asyncio.create_task(embed_missing())

# Презентации о проекте (PDF из папки docs) открываются с начального экрана
if (BASE_DIR / "docs").is_dir():
    app.mount("/presentations", StaticFiles(directory=BASE_DIR / "docs"), name="presentations")


# ---------- аккаунты ----------

# Два аккаунта: admin (владелец) и guest (гость). Пароли по умолчанию простые,
# admin может сменить оба в интерфейсе (кнопка 👥 внизу боковой панели).
DEFAULT_PASSWORDS = {"admin": "admin", "guest": "guest"}
SESSION_COOKIE = "lumelle_session"
SESSION_DAYS = 30           # «Запомнить меня»
SESSION_SHORT = 12 * 3600   # без галочки: 12 часов
MAX_FAILS = 5               # неудачных попыток подряд с одного адреса…
LOCK_SECONDS = 300          # …и вход закрывается на 5 минут
_fails = {}                 # ip -> (сколько ошибок, до какого времени закрыто)


def hash_password(password: str, salt: str) -> str:
    """Пароль хранится только в виде хеша (PBKDF2), сам пароль нигде не записан."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def check_password(name: str, password: str):
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    if u and hmac.compare_digest(hash_password(password, u["salt"]), u["pw_hash"]):
        return dict(u)
    return None


def current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db() as conn:
            row = conn.execute(
                "SELECT u.name, u.role, u.must_change FROM sessions s JOIN users u ON u.name = s.user "
                "WHERE s.token = ? AND s.expires > ?", (token, time.time())).fetchone()
        if row:
            return dict(row)
    raise HTTPException(status_code=401, detail="Login required")


def admin_only(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admin can do this")
    return user


def using_default_passwords():
    return [n for n, pw in DEFAULT_PASSWORDS.items() if check_password(n, pw) and not must_change(n)]


def must_change(name: str) -> bool:
    with db() as conn:
        r = conn.execute("SELECT must_change FROM users WHERE name = ?", (name,)).fetchone()
    return bool(r and r["must_change"])


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
        # картинки к сообщению (список base64 в JSON); добавляем столбец в старые базы
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
        if "images" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN images TEXT")
        # аккаунты: у каждого свои чаты; старые чаты (до появления входа) принадлежат admin
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                name TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                salt TEXT NOT NULL,
                pw_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                chars INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS doc_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                idx INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                PRIMARY KEY (owner, name)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user TEXT NOT NULL REFERENCES users(name) ON DELETE CASCADE,
                expires REAL NOT NULL
            );
            """
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(chats)")]
        if "owner" not in cols:
            conn.execute("ALTER TABLE chats ADD COLUMN owner TEXT NOT NULL DEFAULT 'admin'")
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
        if "must_change" not in cols:  # 1 = временный пароль, сменить при входе
            conn.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(documents)")]
        if "collection" not in cols:  # папка в базе знаний: «Диссертация» и т. п.; '' = без папки
            conn.execute("ALTER TABLE documents ADD COLUMN collection TEXT NOT NULL DEFAULT ''")
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(doc_chunks)")]
        if "vec" not in cols:  # вектор «смысла» фрагмента для поиска по смыслу
            conn.execute("ALTER TABLE doc_chunks ADD COLUMN vec BLOB")
        if not conn.execute("SELECT 1 FROM users").fetchone():  # первый запуск
            for name, role in (("admin", "admin"), ("guest", "guest")):
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO users (name, role, salt, pw_hash) VALUES (?, ?, ?, ?)",
                             (name, role, salt, hash_password(DEFAULT_PASSWORDS[name], salt)))


init_db()


def now():
    return datetime.now().isoformat(timespec="seconds")


def get_chat_or_404(conn, chat_id: int, owner: str):
    chat = conn.execute("SELECT * FROM chats WHERE id = ? AND owner = ?", (chat_id, owner)).fetchone()
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


# ---------- обращение к модели ----------

def current_model() -> str:
    """Текстовая модель: выбранная admin в интерфейсе, иначе — по умолчанию (qwen3:8b или --model)."""
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'model'").fetchone()
    return row["value"] if row else MODEL


def local_model() -> str:
    """Модель Ollama: выбранная, а если выбрана онлайн-модель — модель по умолчанию."""
    m = current_model()
    return MODEL if m.startswith("cloud:") else m


def setting(key: str) -> Optional[str]:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: Optional[str]):
    with db() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))


async def ollama_models() -> List[str]:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(OLLAMA_URL.replace("/api/chat", "/api/tags"))
    return sorted(m["name"] for m in r.json().get("models", []))


async def call_model(mode: str, messages: List[dict], think: bool, fmt=None, ui_lang: Optional[str] = None,
                     context: str = "", cloud_ok: bool = False) -> str:
    has_images = any(m.get("images") for m in messages)
    chosen = current_model()
    note = ""
    # Онлайн-модель: только для владельца (это его ключ и его деньги), только текст и не для файлов презентаций
    if chosen.startswith("cloud:") and cloud_ok and not has_images and fmt is None:
        provider = chosen.split(":", 1)[1]
        key, cmodel = setting(f"cloud_key_{provider}"), setting(f"cloud_model_{provider}")
        if key and cmodel:
            try:
                text = await cloud.ask(provider, key, cmodel, system_prompt(mode, ui_lang) + context,
                                       messages, TEMPERATURE[mode])
                if text:
                    return text
            except cloud.CloudError as e:
                note = f"\n\n⚠️ {e}. Ответила локальная модель {MODEL}."
    model = VISION_MODEL if has_images else local_model()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt(mode, ui_lang) + context}] + messages,
        "stream": False,
        "think": think,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": TEMPERATURE[mode], "num_ctx": NUM_CTX},
    }
    if fmt is not None:
        payload["format"] = fmt  # структурированный ответ (JSON по схеме)
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            if r.status_code == 400 and think and "think" in r.text.lower():
                payload["think"] = False  # модель не умеет «думать» — отвечаем без этого
                r = await client.post(OLLAMA_URL, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ollama is not available: {e}")
    if r.status_code == 404:
        raise HTTPException(
            status_code=502,
            detail=f"Model {model} is not installed. Run in Terminal: ollama pull {model}",
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Ollama error: {r.text[:300]}")
    return r.json()["message"]["content"].strip() + note


async def answer_for(mode: str, messages: List[dict], think: bool, ui_lang: Optional[str] = None,
                     context: str = "", cloud_ok: bool = False) -> str:
    """Ответ модели; в режиме Slides — план слайдов + готовый .pptx."""
    if mode != "slides":
        return await call_model(mode, messages, think, ui_lang=ui_lang, context=context, cloud_ok=cloud_ok)
    raw = await call_model(mode, messages, think, fmt=SLIDES_SCHEMA, ui_lang=ui_lang, context=context)
    try:
        deck = clean_deck(json.loads(raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=502, detail="The model returned an invalid outline. Please try again.")
    name = f"lumelle_{int(time.time() * 1000)}.pptx"
    build_pptx(deck, DECKS_DIR / name)
    return outline_markdown(deck, f"/decks/{name}")


def check_images(images: List[str]):
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_IMAGES} images per message")
    for img in images:
        if len(img) > MAX_IMAGE_B64:
            raise HTTPException(status_code=400, detail="Image is too large")


# ---------- модели запросов ----------

class AskRequest(BaseModel):
    mode: Mode = "chat"
    text: str = ""
    think: bool = False  # True = модель сначала «думает»: дольше, но логичнее
    images: List[str] = []  # картинки в base64 (без префикса data:)
    ui_lang: Optional[str] = None  # язык интерфейса: ru, en, zh, fr, de


class NewChat(BaseModel):
    mode: Mode = "chat"


class ChatMessage(BaseModel):
    text: str = ""
    think: bool = False
    images: List[str] = []
    ui_lang: Optional[str] = None
    use_docs: bool = False  # отвечать по документам пользователя
    collection: Optional[str] = None  # только документы из этой папки (None — все)
    strict_docs: bool = False  # отвечать только по документам, без общих знаний


class ChatUpdate(BaseModel):
    mode: Mode


class LoginRequest(BaseModel):
    name: str
    password: str
    remember: bool = True


class PasswordChange(BaseModel):
    name: str
    password: str


class OwnPassword(BaseModel):
    password: str


class NewUser(BaseModel):
    name: str
    password: str


USERNAME_RE = re.compile(r"[\w.-]{1,32}")  # буквы любого языка, цифры, _ . -


def set_password(conn, name: str, password: str, must_change: bool):
    if len(password) < 4:
        raise HTTPException(status_code=400, detail={"code": "short"})
    salt = secrets.token_hex(16)
    conn.execute("UPDATE users SET salt = ?, pw_hash = ?, must_change = ? WHERE name = ?",
                 (salt, hash_password(password, salt), int(must_change), name))


# ---------- эндпоинты ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/guide/{lang}", include_in_schema=False)
def install_guide(lang: str):
    """Installation guide (markdown) shown inside the app."""
    files = {"ru": "INSTALL_RU.md", "en": "INSTALL_EN.md"}
    if lang not in files:
        raise HTTPException(status_code=404, detail="Guide not found")
    return FileResponse(BASE_DIR / files[lang], media_type="text/markdown; charset=utf-8")


@app.post("/login", summary="Войти (admin или guest)")
def login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    fails, locked_until = _fails.get(ip, (0, 0))
    if locked_until > time.time():
        raise HTTPException(status_code=429, detail={"code": "locked", "seconds": int(locked_until - time.time())})
    user = check_password(req.name.strip().lower(), req.password)
    if not user:
        fails += 1
        _fails[ip] = (0, time.time() + LOCK_SECONDS) if fails >= MAX_FAILS else (fails, 0)
        raise HTTPException(status_code=401, detail={"code": "wrong", "left": max(MAX_FAILS - fails, 0)})
    _fails.pop(ip, None)
    token = secrets.token_urlsafe(32)
    ttl = SESSION_DAYS * 86400 if req.remember else SESSION_SHORT
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token, user["name"], time.time() + ttl))
    response.set_cookie(SESSION_COOKIE, token, max_age=ttl if req.remember else None,
                        httponly=True, samesite="lax")
    return {"name": user["name"], "role": user["role"], "must_change": user.get("must_change", 0)}


@app.post("/logout", summary="Выйти")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/me", summary="Кто вошёл")
def me(user: dict = Depends(current_user)):
    weak = using_default_passwords() if user["role"] == "admin" else []
    return {**user, "default_passwords": weak}


@app.post("/me/password", summary="Сменить свой пароль")
def change_own_password(req: OwnPassword, user: dict = Depends(current_user)):
    with db() as conn:
        set_password(conn, user["name"], req.password, must_change=False)
    return {"ok": True}


@app.get("/users", summary="Пользователи (только admin)")
def list_users(user: dict = Depends(admin_only)):
    with db() as conn:
        rows = conn.execute(
            "SELECT u.name, u.role, u.must_change, (SELECT COUNT(*) FROM chats c WHERE c.owner = u.name) AS chats "
            "FROM users u ORDER BY u.role = 'admin' DESC, u.name").fetchall()
    return [dict(r) for r in rows]


@app.post("/users", summary="Добавить пользователя (только admin)")
def add_user(req: NewUser, user: dict = Depends(admin_only)):
    name = req.name.strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail={"code": "bad_name"})
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
            raise HTTPException(status_code=409, detail={"code": "taken"})
        conn.execute("INSERT INTO users (name, role, salt, pw_hash) VALUES (?, 'user', '', '')", (name,))
        set_password(conn, name, req.password, must_change=True)
    return {"name": name}


@app.post("/users/password", summary="Сбросить пароль пользователя (только admin)")
def change_password(req: PasswordChange, user: dict = Depends(admin_only)):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE name = ?", (req.name,)).fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        # чужой пароль = временный: человек сменит его при входе; свой — постоянный
        set_password(conn, req.name, req.password, must_change=req.name != user["name"])
        if req.name != user["name"]:  # выкидываем того, кому сменили пароль
            conn.execute("DELETE FROM sessions WHERE user = ?", (req.name,))
    return {"ok": True}


@app.delete("/users/{name}", summary="Удалить пользователя и его чаты (только admin)")
def delete_user(name: str, user: dict = Depends(admin_only)):
    with db() as conn:
        u = conn.execute("SELECT role FROM users WHERE name = ?", (name,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        if u["role"] == "admin":
            raise HTTPException(status_code=400, detail="The admin account cannot be deleted")
        conn.execute("DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE owner = ?)", (name,))
        conn.execute("DELETE FROM chats WHERE owner = ?", (name,))
        conn.execute("DELETE FROM doc_chunks WHERE doc_id IN (SELECT id FROM documents WHERE owner = ?)", (name,))
        conn.execute("DELETE FROM documents WHERE owner = ?", (name,))
        conn.execute("DELETE FROM commands WHERE owner = ?", (name,))
        conn.execute("DELETE FROM sessions WHERE user = ?", (name,))
        conn.execute("DELETE FROM users WHERE name = ?", (name,))
    return {"deleted": name}


# ---------- документы ----------

MAX_DOC_BYTES = 15_000_000
DOC_TYPES = (".docx", ".pdf", ".xlsx", ".xlsm", ".txt", ".md", ".csv")


# ---------- поиск по смыслу (эмбеддинги) ----------

EMBED_URL = OLLAMA_URL.replace("/api/chat", "/api/embed")
QUERY_PREFIX = "Instruct: Given a question, find the passages of the user's documents that answer it\nQuery: "
_embed_state = {"ok": None, "checked": 0.0}


async def embed(texts: List[str], query: bool = False):
    """Векторы для текстов через Ollama; None — модель не скачана или Ollama недоступна."""
    if not texts:
        return []
    if _embed_state["ok"] is False and time.time() - _embed_state["checked"] < 60:
        return None  # недавно проверяли: модели нет — не ждём лишний раз
    out = []
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            for i in range(0, len(texts), 16):
                part = [(QUERY_PREFIX + t if query else t)[:6000] for t in texts[i:i + 16]]
                r = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "input": part, "keep_alive": KEEP_ALIVE})
                if r.status_code >= 400:
                    raise ValueError(r.text[:200])
                out += r.json()["embeddings"]
    except (httpx.HTTPError, ValueError, KeyError):
        _embed_state.update(ok=False, checked=time.time())
        return None
    _embed_state.update(ok=True, checked=time.time())
    return out


def pack(vec) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes):
    a = array("f")
    a.frombytes(blob)
    return a


_embed_lock = asyncio.Lock()


async def embed_missing():
    """Досчитываем векторы для новых фрагментов и для загруженных до того, как скачали модель."""
    if _embed_lock.locked():
        return
    async with _embed_lock:
        await _embed_missing()


async def _embed_missing():
    with db() as conn:
        rows = conn.execute("SELECT id, text FROM doc_chunks WHERE vec IS NULL").fetchall()
    for i in range(0, len(rows), 64):
        part = rows[i:i + 64]
        vecs = await embed([r["text"] for r in part])
        if not vecs:
            return
        with db() as conn:
            conn.executemany("UPDATE doc_chunks SET vec = ? WHERE id = ?", [(pack(v), r["id"]) for v, r in zip(vecs, part)])


async def docs_context(owner: str, question: str, collection: Optional[str] = None, strict: bool = False, k: int = 4):
    """Находит в документах пользователя фрагменты по вопросу → (текст для модели, подписи источников).
    collection: только эта папка; strict: отвечать только по документам."""
    sql = ("SELECT c.id, c.idx, c.text, c.vec, d.name FROM doc_chunks c JOIN documents d ON d.id = c.doc_id "
           "WHERE d.owner = ?")
    args = [owner]
    if collection is not None:
        sql += " AND d.collection = ?"
        args.append(collection)
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    if not rows:
        return "", []
    by_id = {r["id"]: r for r in rows}
    vecs = {r["id"]: unpack(r["vec"]) for r in rows if r["vec"]}
    qvec = None
    if vecs:
        got = await embed([question], query=True)
        qvec = got[0] if got else None
    found = hybrid(question, [(r["id"], r["text"]) for r in rows], k=k, qvec=qvec, vecs=vecs)
    if not found:
        if strict:
            return ("\n\nThe user asked to answer ONLY from their documents, and nothing relevant was found in them. "
                    "Say clearly that the documents don't contain this, do NOT answer from general knowledge, "
                    "and suggest uploading the right file or asking with the words used in the documents."), []
        return ("\n\nThe user asked to answer from their documents, but nothing relevant was found in them. "
                "Say so briefly, then answer from general knowledge and mark that part as not from the documents."), []
    parts, used = [], {}
    for n, (_, cid, text) in enumerate(found, 1):
        r = by_id[cid]
        parts.append(f"[{n}] {r['name']}, part {r['idx'] + 1}:\n{text}")
        used.setdefault(r["name"], []).append(r["idx"] + 1)
    sources = [f"{name} — {', '.join(str(i) for i in sorted(idx))}" for name, idx in used.items()]
    rule = ("Use ONLY these excerpts. If they answer only part of the question, answer that part and say exactly what "
            "is missing in the documents. Do not add facts from general knowledge."
            if strict else
            "They take priority over general knowledge. If the excerpts do not contain the answer, say that the "
            "documents don't cover it and only then use general knowledge, marked as such.")
    context = ("\n\nAnswer using the excerpts from the user's own documents below, and refer to them as [1], [2] … "
               "Tables are written as «cell | cell»; «Лист:» is a spreadsheet sheet. If an answer is marked as disputed "
               "(«спорно»), say so. " + rule + "\n\n" + "\n\n".join(parts))
    return context, sources


@app.get("/documents", summary="Мои документы")
def list_documents(user: dict = Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.chars, d.created_at, d.collection, COUNT(c.id) AS parts, "
            "COUNT(c.vec) AS parts_ready FROM documents d "
            "LEFT JOIN doc_chunks c ON c.doc_id = d.id WHERE d.owner = ? GROUP BY d.id ORDER BY d.id DESC",
            (user["name"],)).fetchall()
    return [dict(r) for r in rows]


def clean_collection(name: str) -> str:
    name = " ".join((name or "").split())[:60]
    if any(c in name for c in "<>\"/\\"):
        raise HTTPException(status_code=400, detail={"code": "col_name"})
    return name


@app.get("/documents/status", summary="Работает ли поиск по смыслу")
async def documents_status(user: dict = Depends(current_user)):
    try:
        models = await ollama_models()
    except (httpx.HTTPError, ValueError):
        models = []
    installed = EMBED_MODEL in models or (":" not in EMBED_MODEL and EMBED_MODEL + ":latest" in models)
    with db() as conn:
        missing = conn.execute("SELECT COUNT(*) FROM doc_chunks c JOIN documents d ON d.id = c.doc_id "
                               "WHERE d.owner = ? AND c.vec IS NULL", (user["name"],)).fetchone()[0]
    if installed and missing:
        _embed_state["ok"] = None
        asyncio.create_task(embed_missing())
    return {"semantic": installed, "model": EMBED_MODEL, "missing": missing}


@app.post("/documents", summary="Загрузить документ (.docx, .pdf, .xlsx, .txt, .md)")
async def upload_document(file: UploadFile = File(...), collection: str = Form(""),
                          user: dict = Depends(current_user)):
    collection = clean_collection(collection)
    name = Path(file.filename or "document").name[:120]
    if not name.lower().endswith(DOC_TYPES):
        raise HTTPException(status_code=400, detail={"code": "doc_type"})
    data = await file.read()
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(status_code=413, detail={"code": "doc_big"})
    try:
        text = await run_in_threadpool(extract_text, name, data)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "doc_read"})
    chunks = split_chunks(text)
    if not chunks:
        raise HTTPException(status_code=400, detail={"code": "doc_empty"})
    with db() as conn:
        cur = conn.execute("INSERT INTO documents (owner, name, chars, created_at, collection) VALUES (?, ?, ?, ?, ?)",
                           (user["name"], name, len(text), now(), collection))
        conn.executemany("INSERT INTO doc_chunks (doc_id, idx, text) VALUES (?, ?, ?)",
                         [(cur.lastrowid, i, c) for i, c in enumerate(chunks)])
    asyncio.create_task(embed_missing())  # векторы считаются в фоне, загрузка не ждёт
    return {"id": cur.lastrowid, "name": name, "parts": len(chunks), "collection": collection}


class DocMove(BaseModel):
    collection: str = ""


@app.patch("/documents/{doc_id}", summary="Переложить документ в другую папку")
def move_document(doc_id: int, req: DocMove, user: dict = Depends(current_user)):
    collection = clean_collection(req.collection)
    with db() as conn:
        cur = conn.execute("UPDATE documents SET collection = ? WHERE id = ? AND owner = ?",
                           (collection, doc_id, user["name"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Document not found")
    return {"id": doc_id, "collection": collection}


@app.delete("/documents/{doc_id}", summary="Удалить документ")
def delete_document(doc_id: int, user: dict = Depends(current_user)):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id = ? AND owner = ?", (doc_id, user["name"])).fetchone():
            raise HTTPException(status_code=404, detail="Document not found")
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return {"deleted": doc_id}


# ---------- команды «/…» ----------

def resolve_command(owner: str, text: str):
    """«/тест вопрос» → (инструкция, «вопрос»); None, если это не команда."""
    m = COMMAND_RE.match(text.strip())
    if not m:
        return None
    name, rest = m.group(1).lower(), m.group(2).strip()
    if name in COMMAND_NAMES:
        return COMMANDS[COMMAND_NAMES[name]]["prompt"], rest
    with db() as conn:
        row = conn.execute("SELECT prompt FROM commands WHERE owner = ? AND name = ?", (owner, name)).fetchone()
    return (row["prompt"], rest) if row else None


class NewCommand(BaseModel):
    name: str
    prompt: str


@app.get("/commands", summary="Команды «/…»: встроенные и свои")
def list_commands(user: dict = Depends(current_user)):
    with db() as conn:
        own = conn.execute("SELECT name, prompt FROM commands WHERE owner = ? ORDER BY name", (user["name"],)).fetchall()
    return {"builtin": [{"key": k, "names": list(c["names"])} for k, c in COMMANDS.items()],
            "own": [dict(r) for r in own]}


@app.post("/commands", summary="Добавить или изменить свою команду")
def save_command(req: NewCommand, user: dict = Depends(current_user)):
    name = req.name.strip().lstrip("/").lower()
    prompt = req.prompt.strip()
    if not re.fullmatch(r"[\w-]{1,32}", name):
        raise HTTPException(status_code=400, detail={"code": "cmd_name"})
    if name in COMMAND_NAMES:
        raise HTTPException(status_code=400, detail={"code": "cmd_taken"})
    if not 5 <= len(prompt) <= 4000:
        raise HTTPException(status_code=400, detail={"code": "cmd_prompt"})
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM commands WHERE owner = ?", (user["name"],)).fetchone()[0] >= 50 and \
                not conn.execute("SELECT 1 FROM commands WHERE owner = ? AND name = ?", (user["name"], name)).fetchone():
            raise HTTPException(status_code=400, detail={"code": "cmd_many"})
        conn.execute("INSERT OR REPLACE INTO commands (owner, name, prompt) VALUES (?, ?, ?)", (user["name"], name, prompt))
    return {"name": name}


@app.delete("/commands/{name}", summary="Удалить свою команду")
def delete_command(name: str, user: dict = Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM commands WHERE owner = ? AND name = ?", (user["name"], name.lower()))
    return {"deleted": name}


# ---------- выбор модели ----------

class ModelChoice(BaseModel):
    name: str


@app.get("/models", summary="Скачанные модели и какая сейчас отвечает")
async def list_models(user: dict = Depends(current_user)):
    try:
        names = await ollama_models()
    except (httpx.HTTPError, ValueError):
        names = []
    # модели для картинок и распознавания речи отвечать на текст не умеют — прячем их из списка
    text_models = [n for n in names if "-vl" not in n and "embed" not in n]
    online = []
    if user["role"] == "admin":
        for pid, p in cloud.PROVIDERS.items():
            m = setting(f"cloud_model_{pid}")
            if setting(f"cloud_key_{pid}") and m:
                online.append({"name": f"cloud:{pid}", "label": f"{p['label']} · {m}"})
    return {"models": text_models, "online": online, "current": current_model(), "default": MODEL}


@app.post("/models/current", summary="Выбрать модель (только admin)")
async def choose_model(req: ModelChoice, user: dict = Depends(admin_only)):
    if req.name.startswith("cloud:"):
        pid = req.name.split(":", 1)[1]
        if pid not in cloud.PROVIDERS or not setting(f"cloud_key_{pid}"):
            raise HTTPException(status_code=404, detail="Add the API key first")
        set_setting("model", req.name)
        return {"current": req.name}
    try:
        names = await ollama_models()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Ollama is not available")
    if req.name not in names:
        raise HTTPException(status_code=404, detail=f"Model {req.name} is not downloaded")
    with db() as conn:
        if req.name == MODEL:
            conn.execute("DELETE FROM settings WHERE key = 'model'")  # вернулись к модели по умолчанию
        else:
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('model', ?)",
                         (req.name,))
    return {"current": current_model()}


# ---------- онлайн-модели (по API-ключам владельца) ----------

class CloudSettings(BaseModel):
    provider: str
    key: Optional[str] = None    # None — не менять; "" — удалить ключ
    model: Optional[str] = None


@app.get("/cloud", summary="Онлайн-модели: какие ключи добавлены (только admin)")
def cloud_settings(user: dict = Depends(admin_only)):
    out = []
    for pid, p in cloud.PROVIDERS.items():
        key = setting(f"cloud_key_{pid}")
        out.append({"id": pid, "label": p["label"], "keys_url": p["keys_url"], "has_key": bool(key),
                    "key_end": key[-4:] if key else "", "model": setting(f"cloud_model_{pid}") or ""})
    return {"providers": out}


@app.get("/cloud/{provider}/models", summary="Модели, доступные по ключу (нужен интернет)")
async def cloud_models(provider: str, user: dict = Depends(admin_only)):
    key = setting(f"cloud_key_{provider}")
    if provider not in cloud.PROVIDERS or not key:
        raise HTTPException(status_code=404, detail="No key")
    try:
        return {"models": await cloud.list_models(provider, key)}
    except cloud.CloudError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/cloud", summary="Сохранить ключ или модель (только admin)")
async def save_cloud(req: CloudSettings, user: dict = Depends(admin_only)):
    pid = req.provider
    if pid not in cloud.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    models = None
    if req.key is not None:
        key = req.key.strip()
        if not key:  # удалить ключ; если эта модель была выбрана — вернуться к локальной
            set_setting(f"cloud_key_{pid}", None)
            set_setting(f"cloud_model_{pid}", None)
            if current_model() == f"cloud:{pid}":
                set_setting("model", None)
            return {"ok": True}
        try:
            models = await cloud.list_models(pid, key)  # заодно проверяем, что ключ работает
        except cloud.CloudError as e:
            raise HTTPException(status_code=400, detail=str(e))
        set_setting(f"cloud_key_{pid}", key)
        if not req.model and not setting(f"cloud_model_{pid}"):
            set_setting(f"cloud_model_{pid}", cloud.default_model(pid, models))
    if req.model:
        set_setting(f"cloud_model_{pid}", req.model.strip()[:100])
    return {"ok": True, "models": models, "model": setting(f"cloud_model_{pid}")}


# ---------- распознавание речи ----------

_whisper = None


def _load_whisper():
    """Модель загружается при первом использовании (в первый раз скачивается, ~0,5 ГБ)."""
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise HTTPException(status_code=501, detail={"code": "stt_missing"})
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper


def _transcribe(path: str, lang):
    model = _load_whisper()
    segments, info = model.transcribe(path, language=lang, vad_filter=True, beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip(), info.language


@app.post("/transcribe", summary="Голос → текст (локально, Whisper)")
async def transcribe(audio: UploadFile = File(...), lang: str = Form(""), user: dict = Depends(current_user)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too long")
    import tempfile
    suffix = Path(audio.filename or "voice.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        text, detected = await run_in_threadpool(_transcribe, tmp, lang if lang in UI_LANGS else None)
    except HTTPException:
        raise
    except Exception as e:  # повреждённый файл, нет сети при первой загрузке модели и т. п.
        raise HTTPException(status_code=500, detail={"code": "stt_failed", "error": str(e)[:200]})
    finally:
        os.unlink(tmp)
    return {"text": text, "language": detected}


@app.get("/decks/{name}", include_in_schema=False)
def download_deck(name: str, user: dict = Depends(current_user)):
    if not re.fullmatch(r"lumelle_\d+\.pptx", name) or not (DECKS_DIR / name).is_file():
        raise HTTPException(status_code=404, detail="Presentation not found")
    return FileResponse(
        DECKS_DIR / name,
        filename=name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.post("/ask", summary="Быстрый вопрос без сохранения")
async def ask(req: AskRequest, user: dict = Depends(current_user)):
    check_images(req.images)
    msg = {"role": "user", "content": req.text}
    if req.images:
        msg["images"] = req.images
    answer = await answer_for(req.mode, [msg], req.think, req.ui_lang, cloud_ok=user["role"] == "admin")
    return {"mode": req.mode, "answer": answer}


@app.post("/chats", summary="Создать новый чат")
def create_chat(req: NewChat, user: dict = Depends(current_user)):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO chats (title, mode, created_at, owner) VALUES (?, ?, ?, ?)",
            ("New chat", req.mode, now(), user["name"]),
        )
        return {"id": cur.lastrowid, "mode": req.mode}


@app.get("/chats", summary="Список чатов")
def list_chats(user: dict = Depends(current_user)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM chats WHERE owner = ? ORDER BY id DESC", (user["name"],)).fetchall()
        return [dict(r) for r in rows]


@app.get("/chats/{chat_id}", summary="Чат со всей историей")
def get_chat(chat_id: int, user: dict = Depends(current_user)):
    with db() as conn:
        chat = get_chat_or_404(conn, chat_id, user["name"])
        msgs = conn.execute(
            "SELECT role, content, images, created_at FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
        messages = []
        for m in msgs:
            item = dict(m)
            item["images"] = json.loads(item["images"]) if item["images"] else []
            messages.append(item)
        return {**dict(chat), "messages": messages}


@app.post("/chats/{chat_id}/ask", summary="Написать в чат (модель помнит контекст)")
async def ask_in_chat(chat_id: int, req: ChatMessage, user: dict = Depends(current_user)):
    check_images(req.images)
    if not req.text.strip() and not req.images:
        raise HTTPException(status_code=400, detail="Empty message")
    with db() as conn:
        chat = get_chat_or_404(conn, chat_id, user["name"])
        history = conn.execute(
            "SELECT role, content, images FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, HISTORY_LIMIT),
        ).fetchall()
    messages = []
    for m in reversed(history):
        messages.append({"role": m["role"], "content": m["content"], "_images": m["images"]})
    # чтобы уточняющие вопросы понимали картинку, оставляем в контексте только последнюю из них
    if not req.images:
        for m in reversed(messages):
            if m["_images"]:
                m["images"] = json.loads(m["_images"])
                break
    for m in messages:
        m.pop("_images")
    text = req.text.strip() or "Answer the question in the image."
    mode = chat["mode"]
    question = req.text.strip()
    command = resolve_command(user["name"], text)
    if command:  # «/тест …» → подробная инструкция + текст пользователя; отвечает научный ассистент
        instruction, question = command
        text = instruction + ("\n\n---\n" + question if question else
                              ("\n\n(The material is in the attached image.)" if req.images else ""))
        mode = "science"
    current = {"role": "user", "content": text}
    if req.images:
        current["images"] = req.images
    messages.append(current)

    context, sources = ("", [])
    if req.use_docs and question:
        k = 8 if command and command[0] == COMMANDS["review"]["prompt"] else 4  # обзору нужно больше источников
        context, sources = await docs_context(user["name"], question, req.collection, req.strict_docs, k)
    answer = await answer_for(mode, messages, req.think, req.ui_lang, context, cloud_ok=user["role"] == "admin")
    if sources and mode != "slides":
        answer += "\n\n📎 " + "; ".join(sources)

    with db() as conn:
        conn.executemany(
            "INSERT INTO messages (chat_id, role, content, images, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                (chat_id, "user", req.text, json.dumps(req.images) if req.images else None, now()),
                (chat_id, "assistant", answer, None, now()),
            ],
        )
        if chat["title"] == "New chat":  # название чата = начало первого сообщения
            first = req.text.strip() or "🖼 Image"
            title = first[:40] + ("…" if len(first) > 40 else "")
            conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
    return {"chat_id": chat_id, "answer": answer}


@app.patch("/chats/{chat_id}", summary="Сменить режим чата")
def update_chat(chat_id: int, req: ChatUpdate, user: dict = Depends(current_user)):
    with db() as conn:
        get_chat_or_404(conn, chat_id, user["name"])
        conn.execute("UPDATE chats SET mode = ? WHERE id = ?", (req.mode, chat_id))
    return {"id": chat_id, "mode": req.mode}


@app.delete("/chats/{chat_id}", summary="Удалить чат")
def delete_chat(chat_id: int, user: dict = Depends(current_user)):
    with db() as conn:
        get_chat_or_404(conn, chat_id, user["name"])
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return {"deleted": chat_id}
