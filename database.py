import sqlite3
import json
import base64
import requests
from datetime import datetime
from config import settings

DB_FILE_ON_GITHUB = "db_backup.json"

def get_github_headers():
    return {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json"
    }

# ==========================================
# СИНХРОНИЗАЦИЯ С GITHUB
# ==========================================
def sync_from_github():
    """Скачивает JSON с GitHub и заполняет локальную БД."""
    print("Синхронизация с GitHub (загрузка)...", flush=True)
    url = f"https://api.github.com/repos/{settings.github_repo}/contents/{DB_FILE_ON_GITHUB}"
    res = requests.get(url, headers=get_github_headers())
    
    if res.status_code != 200:
        print("Файл резервной копии не найден на GitHub. Начинаем с чистой базы.", flush=True)
        return

    try:
        content = base64.b64decode(res.json()['content']).decode('utf-8')
        data = json.loads(content)
        
        conn = sqlite3.connect(settings.db_path)
        c = conn.cursor()
        
        # Очищаем текущие таблицы перед заливкой
        c.execute("DELETE FROM lessons")
        c.execute("DELETE FROM books")
        
        # Восстанавливаем книги
        for b in data.get("books", []):
            c.execute("INSERT INTO books (id, title, author, slug) VALUES (?, ?, ?, ?)",
                      (b["id"], b["title"], b["author"], b["slug"]))
                      
        # Восстанавливаем уроки
        for l in data.get("lessons", []):
            c.execute("INSERT INTO lessons (id, book_id, lesson_title, part, audio_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (l["id"], l["book_id"], l["lesson_title"], l["part"], l["audio_url"], l["created_at"]))
                      
        conn.commit()
        conn.close()
        print("База данных успешно восстановлена с GitHub!", flush=True)
    except Exception as e:
        print(f"Ошибка при восстановлении БД: {e}", flush=True)

def sync_to_github():
    """Выгружает локальную БД в JSON и отправляет на GitHub."""
    print("Синхронизация с GitHub (сохранение)...", flush=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("SELECT * FROM books")
    books = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT * FROM lessons")
    lessons = [dict(r) for r in c.fetchall()]
    conn.close()

    data = {"books": books, "lessons": lessons}
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

    url = f"https://api.github.com/repos/{settings.github_repo}/contents/{DB_FILE_ON_GITHUB}"
    headers = get_github_headers()
    
    # Получаем текущий SHA файла (он нужен для обновления)
    res = requests.get(url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None

    payload = {
        "message": "Auto-update database from Telegram Bot",
        "content": encoded_content
    }
    if sha:
        payload["sha"] = sha

    put_res = requests.put(url, headers=headers, json=payload)
    if put_res.status_code in [200, 201]:
        print("Резервная копия БД успешно сохранена на GitHub!", flush=True)
    else:
        print(f"Ошибка сохранения на GitHub: {put_res.text}", flush=True)

# ==========================================
# ОСНОВНЫЕ ФУНКЦИИ БД
# ==========================================
def init_db():
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            author TEXT,
            slug TEXT UNIQUE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            lesson_title TEXT,
            part INTEGER,
            audio_url TEXT,
            created_at TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
    """)
    conn.commit()
    conn.close()
    
    # При запуске восстанавливаем данные с GitHub
    sync_from_github()

def get_or_create_book(title: str, author: str, slug: str) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("SELECT id FROM books WHERE slug = ?", (slug,))
    row = c.fetchone()
    if row:
        book_id = row[0]
        c.execute("UPDATE books SET title = ?, author = ? WHERE id = ?", (title, author, book_id))
    else:
        c.execute("INSERT INTO books (title, author, slug) VALUES (?, ?, ?)", (title, author, slug))
        book_id = c.lastrowid
    conn.commit()
    conn.close()
    # Сохраняем изменения на GitHub
    sync_to_github()
    return book_id

def save_lesson(data: dict) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO lessons (book_id, lesson_title, part, audio_url, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        data["book_id"], data["lesson_title"], data.get("part", 1),
        data.get("audio_url", ""), datetime.utcnow().isoformat()
    ))
    lesson_id = c.lastrowid
    conn.commit()
    conn.close()
    # Сохраняем изменения на GitHub
    sync_to_github()
    return lesson_id

def update_audio_url(lesson_id: int, audio_url: str):
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("UPDATE lessons SET audio_url = ? WHERE id = ?", (audio_url, lesson_id))
    conn.commit()
    conn.close()
    # Сохраняем изменения на GitHub
    sync_to_github()

def get_last_book() -> dict | None:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("SELECT id, title, author, slug FROM books ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row: return {"id": row[0], "title": row[1], "author": row[2], "slug": row[3]}
    return None

def delete_lesson(lesson_id: int) -> bool:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        # Сохраняем изменения на GitHub
        sync_to_github()
    return deleted

def get_all_lessons() -> list:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT l.id, l.part, l.lesson_title, b.title as book_title 
        FROM lessons l 
        JOIN books b ON l.book_id = b.id 
        ORDER BY b.title, l.part
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]
