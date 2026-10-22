import sqlite3
import json
from datetime import datetime
from config import settings

def init_db():
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    
    # Таблица книг
    c.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            author TEXT
        )
    """)
    
    # Таблица уроков (добавили video_url)
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            lesson_number INTEGER,
            lesson_title TEXT,
            sections TEXT,
            audio_url TEXT,
            video_url TEXT,
            uploaded_by INTEGER,
            uploaded_by_name TEXT,
            created_at TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
    """)
    conn.commit()
    conn.close()

def get_or_create_book(title: str, author: str) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("SELECT id FROM books WHERE title = ?", (title,))
    row = c.fetchone()
    if row:
        book_id = row[0]
        # Обновим автора, если он изменился
        c.execute("UPDATE books SET author = ? WHERE id = ?", (author, book_id))
    else:
        c.execute("INSERT INTO books (title, author) VALUES (?, ?)", (title, author))
        book_id = c.lastrowid
    conn.commit()
    conn.close()
    return book_id

def get_last_book() -> dict | None:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("SELECT id, title, author FROM books ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "title": row[1], "author": row[2]}
    return None

def save_lesson(data: dict) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO lessons
        (book_id, lesson_number, lesson_title, sections, audio_url, video_url, uploaded_by, uploaded_by_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["book_id"], data["lesson_number"],
        data["lesson_title"], json.dumps(data["sections"], ensure_ascii=False),
        data.get("audio_url", ""), data.get("video_url", ""),
        data.get("uploaded_by"), data.get("uploaded_by_name"),
        datetime.utcnow().isoformat()
    ))
    lesson_id = c.lastrowid
    conn.commit()
    conn.close()
    return lesson_id

def update_audio_url(lesson_id: int, audio_url: str) -> int | None:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("UPDATE lessons SET audio_url = ? WHERE id = ?", (audio_url, lesson_id))
    c.execute("SELECT uploaded_by FROM lessons WHERE id = ?", (lesson_id,))
    row = c.fetchone()
    chat_id = row[0] if row else None
    conn.commit()
    conn.close()
    return chat_id

def delete_lesson(lesson_id: int) -> bool:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
