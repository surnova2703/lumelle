import asyncio
import hashlib
import hmac
import secrets
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
MAX_AUDIO_BYTES = 25_000_000  # ≈ несколько минут речи
MAX_IMAGES = 4
MAX_IMAGE_B64 = 8_000_000  # ≈6 МБ на картинку
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "lumelle.db"
DECKS_DIR = BASE_DIR / "generated"  # презентации, созданные в режиме Slides
HISTORY_LIMIT = 20  # сколько последних сообщений модель «помнит» в чате

Mode = Literal["chat", "write", "translate", "slides", "learn"]

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
}

# «Температура»: ниже = точнее и предсказуемее, выше = креативнее
# Interface languages: the model uses them for the default translation direction
# and for messages that have no clear language (for example only an image)
UI_LANGS = {"ru": "Russian", "en": "English", "zh": "Chinese", "fr": "French", "de": "German"}


def system_prompt(mode: str, ui_lang: Optional[str]) -> str:
    ui = UI_LANGS.get(ui_lang or "ru", "Russian")
    other = "Russian" if ui == "English" else "English"
    prompt = MODES[mode].replace("{ui}", ui).replace("{other}", other)
    return prompt + f" The user's interface language is {ui}: use it when the message itself has no clear language."


TEMPERATURE = {"chat": 0.4, "write": 0.7, "translate": 0.1, "slides": 0.5, "learn": 0.4}

app = FastAPI(title="Lumelle AI")


@app.on_event("startup")
async def warm_up_model():
    """Загружаем текстовую модель в память заранее, пока открывается браузер:
    тогда первый ответ приходит без долгого ожидания."""
    async def load():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                await client.post(OLLAMA_URL.replace("/api/chat", "/api/generate"),
                                  json={"model": current_model(), "keep_alive": KEEP_ALIVE})
        except httpx.HTTPError:
            pass  # Ollama не запущена — run.py уже подсказал, что делать
    asyncio.create_task(load())

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
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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


async def ollama_models() -> List[str]:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(OLLAMA_URL.replace("/api/chat", "/api/tags"))
    return sorted(m["name"] for m in r.json().get("models", []))


async def call_model(mode: str, messages: List[dict], think: bool, fmt=None, ui_lang: Optional[str] = None) -> str:
    has_images = any(m.get("images") for m in messages)
    model = VISION_MODEL if has_images else current_model()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt(mode, ui_lang)}] + messages,
        "stream": False,
        "think": think,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": TEMPERATURE[mode]},
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
    return r.json()["message"]["content"].strip()


async def answer_for(mode: str, messages: List[dict], think: bool, ui_lang: Optional[str] = None) -> str:
    """Ответ модели; в режиме Slides — план слайдов + готовый .pptx."""
    if mode != "slides":
        return await call_model(mode, messages, think, ui_lang=ui_lang)
    raw = await call_model(mode, messages, think, fmt=SLIDES_SCHEMA, ui_lang=ui_lang)
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
        conn.execute("DELETE FROM sessions WHERE user = ?", (name,))
        conn.execute("DELETE FROM users WHERE name = ?", (name,))
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
    return {"models": text_models, "current": current_model(), "default": MODEL}


@app.post("/models/current", summary="Выбрать модель (только admin)")
async def choose_model(req: ModelChoice, user: dict = Depends(admin_only)):
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
    answer = await answer_for(req.mode, [msg], req.think, req.ui_lang)
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
    current = {"role": "user", "content": text}
    if req.images:
        current["images"] = req.images
    messages.append(current)

    answer = await answer_for(chat["mode"], messages, req.think, req.ui_lang)

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
