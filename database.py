import sqlite3
import json
from datetime import datetime
from config import settings

# ... начало файла database.py без изменений ...

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
    
    # Таблица аудиоуроков
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            lesson_number INTEGER,
            lesson_title TEXT,
            sections TEXT,
            audio_url TEXT,
            uploaded_by INTEGER,
            uploaded_by_name TEXT,
            created_at TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
    """)

    # НОВАЯ ТАБЛИЦА: Видеоуроки
    c.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            video_url TEXT,
            uploaded_by INTEGER,
            uploaded_by_name TEXT,
            created_at TEXT
        )
    """)
    
    conn.commit()
    conn.close()

# ... функции для аудио (get_or_create_book, get_last_book, save_lesson, update_audio_url) без изменений ...

# НОВАЯ ФУНКЦИЯ: Сохранение видео
def save_video(title: str, url: str, user_id: int, user_name: str) -> int:
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO videos (title, video_url, uploaded_by, uploaded_by_name, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (title, url, user_id, user_name, datetime.utcnow().isoformat()))
    video_id = c.lastrowid
    conn.commit()
    conn.close()
    return video_id

# ... остальные функции (delete_lesson и т.д.) без изменений ...
