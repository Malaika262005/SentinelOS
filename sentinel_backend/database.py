import sqlite3

import os
DB_NAME = os.path.join(os.path.dirname(__file__), "sentinel.db")

def get_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS orgs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        name TEXT,
        role TEXT,
        team TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS ingests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        source TEXT,
        text TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS truths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        key TEXT,
        value TEXT,
        version INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        key TEXT,
        old_value TEXT,
        new_value TEXT,
        question TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        ingest_id INTEGER,
        task TEXT,
        owner TEXT,
        status TEXT,
        deadline TEXT,
        dependency TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS risks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        ingest_id INTEGER,
        score INTEGER,
        level TEXT,
        reasons TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()