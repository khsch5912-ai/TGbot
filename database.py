import sqlite3
from datetime import datetime
from config import settings

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
    return lesson_id

def update_audio_url(lesson_id: int, audio_url: str):
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("UPDATE lessons SET audio_url = ? WHERE id = ?", (audio_url, lesson_id))
    conn.commit()
    conn.close()

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
