import sqlite3
import json
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, 'E:/pi')
from app.config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "pi.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # Users — only Ash
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            pin_hash TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Devices — laptop, phone, etc
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY,
            device_name TEXT NOT NULL,
            device_type TEXT,
            trusted INTEGER DEFAULT 0,
            last_seen TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Threads — conversations
    c.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY,
            title TEXT,
            mode TEXT DEFAULT 'normie',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Messages — every exchange
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model_used TEXT,
            tokens_used INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (thread_id) REFERENCES threads(id)
        )
    """)

    # Memory — permanent facts Pi stores
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            tier TEXT DEFAULT 'active',
            content TEXT NOT NULL,
            source TEXT,
            importance INTEGER DEFAULT 5,
            confirmed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        )
    """)

    # Documents
    c.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            file_type TEXT,
            file_path TEXT,
            summary TEXT,
            indexed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Tool runs — audit log
    c.execute("""
        CREATE TABLE IF NOT EXISTS tool_runs (
            id INTEGER PRIMARY KEY,
            tool_name TEXT NOT NULL,
            input_data TEXT,
            output_data TEXT,
            success INTEGER DEFAULT 1,
            error_msg TEXT,
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Cost log
    c.execute("""
        CREATE TABLE IF NOT EXISTS cost_log (
            id INTEGER PRIMARY KEY,
            api_name TEXT NOT NULL,
            model TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            mode TEXT DEFAULT 'normie',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Settings
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Audit log — security events
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            detail TEXT,
            device_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("Pi state database initialised.")

if __name__ == "__main__":
    init_db()