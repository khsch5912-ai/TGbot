import sqlite3
import json
from datetime import datetime
from config import settings

def init_db():
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_title TEXT,
            author TEXT,
            lesson_number INTEGER,
            lesson_title TEXT,
            sections TEXT,
            audio_url TEXT,
            uploaded_by INTEGER,
            uploaded_by_name TEXT,
            created_at TEXT,
            raw_caption TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_lesson(data: dict) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO lessons
        (book_title, author, lesson_number, lesson_title, sections, audio_url, uploaded_by, uploaded_by_name, created_at, raw_caption)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["book_title"], data["author"], data["lesson_number"],
        data["lesson_title"], json.dumps(data["sections"], ensure_ascii=False),
        data.get("audio_url", ""), data.get("uploaded_by"), data.get("uploaded_by_name"),
        datetime.utcnow().isoformat(), data.get("raw_caption", "")
    ))
    lesson_id = c.lastrowid
    conn.commit()
    conn.close()
    return lesson_id

def update_audio_url(lesson_id: int, audio_url: str) -> int | None:
    """Обновляет ссылку на аудио и возвращает chat_id пользователя."""
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("UPDATE lessons SET audio_url = ? WHERE id = ?", (audio_url, lesson_id))
    
    c.execute("SELECT uploaded_by FROM lessons WHERE id = ?", (lesson_id,))
    row = c.fetchone()
    chat_id = row[0] if row else None
    
    conn.commit()
    conn.close()
    return chat_id
