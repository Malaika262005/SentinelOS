from fastapi import FastAPI
from pydantic import BaseModel
from database import init_db, get_connection
from intelligence import (
    extract_tasks, extract_truths, compute_risk,
    routing_suggestions, detect_conflicts, build_graph,
    executive_briefing
)
import traceback

app = FastAPI(title="SentinelOS Backend", version="2.4")

# ---------- Auto Schema Fix (SQLite-safe) ----------
def _get_columns(conn, table_name: str):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    return [r[1] for r in c.fetchall()]

def _ensure_table(conn, ddl: str):
    c = conn.cursor()
    c.execute(ddl)
    conn.commit()

def _ensure_column(conn, table: str, col: str, col_type: str):
    """
    SQLite limitation: ALTER TABLE ADD COLUMN cannot use non-constant defaults (like CURRENT_TIMESTAMP).
    So we add created_at as plain TEXT/DATETIME without DEFAULT.
    """
    cols = _get_columns(conn, table)
    if col not in cols:
        c = conn.cursor()
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        conn.commit()

def ensure_schema():
    conn = get_connection()

    # tables (CREATE TABLE defaults are OK, but old DB might not have them)
    _ensure_table(conn, """
    CREATE TABLE IF NOT EXISTS orgs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    )
    """)

    _ensure_table(conn, """
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        name TEXT,
        role TEXT,
        team TEXT
    )
    """)

    _ensure_table(conn, """
    CREATE TABLE IF NOT EXISTS ingests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        source TEXT,
        text TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    _ensure_table(conn, """
    CREATE TABLE IF NOT EXISTS truths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER,
        key TEXT,
        value TEXT,
        version INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    _ensure_table(conn, """
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

    _ensure_table(conn, """
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

    _ensure_table(conn, """
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

    # ensure all important columns exist (migration-safe)
    cols_to_ensure = [
        ("members","org_id","INTEGER"), ("members","name","TEXT"), ("members","role","TEXT"), ("members","team","TEXT"),

        ("ingests","org_id","INTEGER"), ("ingests","source","TEXT"), ("ingests","text","TEXT"),
        ("ingests","created_at","TEXT"),  # IMPORTANT for old DBs

        ("truths","org_id","INTEGER"), ("truths","key","TEXT"), ("truths","value","TEXT"), ("truths","version","INTEGER"),
        ("truths","created_at","TEXT"),

        ("conflicts","org_id","INTEGER"), ("conflicts","key","TEXT"), ("conflicts","old_value","TEXT"),
        ("conflicts","new_value","TEXT"), ("conflicts","question","TEXT"),
        ("conflicts","created_at","TEXT"),

        ("tasks","org_id","INTEGER"), ("tasks","ingest_id","INTEGER"), ("tasks","task","TEXT"),
        ("tasks","owner","TEXT"), ("tasks","status","TEXT"), ("tasks","deadline","TEXT"), ("tasks","dependency","TEXT"),
        ("tasks","created_at","TEXT"),

        ("risks","org_id","INTEGER"), ("risks","ingest_id","INTEGER"), ("risks","score","INTEGER"),
        ("risks","level","TEXT"), ("risks","reasons","TEXT"),
        ("risks","created_at","TEXT"),
    ]

    for table, col, col_type in cols_to_ensure:
        _ensure_column(conn, table, col, col_type)

    conn.close()

# init + ensure schema
init_db()
ensure_schema()

# ---------- Models ----------
class IngestInput(BaseModel):
    org_id: int
    text: str
    source: str = "Chat update"

class AskInput(BaseModel):
    org_id: int
    question: str

# ---------- Helpers ----------
def _get_latest_truth(conn, org_id: int, key: str):
    c = conn.cursor()
    c.execute(
        "SELECT value, version FROM truths WHERE org_id=? AND key=? ORDER BY version DESC LIMIT 1",
        (org_id, key)
    )
    row = c.fetchone()
    return (row[0], row[1]) if row else (None, 0)

# ---------- Routes ----------
@app.get("/")
def home():
    return {"status": "SentinelOS backend running", "docs": "/docs"}

@app.post("/ingest")
def ingest(data: IngestInput):
    try:
        text = (data.text or "").strip()
        if not text:
            return {"error": "Empty text"}

        tasks = extract_tasks(text)
        truths = extract_truths(text)
        risk_score, risk_level, reasons = compute_risk(text, tasks)
        routing = routing_suggestions(text, tasks)

        conn = get_connection()
        c = conn.cursor()

        # Always set created_at explicitly (works even if column was added later)
        c.execute(
            "INSERT INTO ingests (org_id, source, text, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (data.org_id, data.source, text)
        )
        ingest_id = c.lastrowid

        conflicts_out = []
        for tr in truths:
            key, val = tr["key"], tr["value"]
            old_val, old_ver = _get_latest_truth(conn, data.org_id, key)

            if detect_conflicts(old_val, val):
                question = f"Which is correct for {key}: '{old_val}' or '{val}'?"
                c.execute(
                    "INSERT INTO conflicts (org_id, key, old_value, new_value, question, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (data.org_id, key, old_val, val, question)
                )
                conflicts_out.append({
                    "key": key, "old_value": old_val, "new_value": val, "question": question
                })

            new_ver = old_ver + 1
            c.execute(
                "INSERT INTO truths (org_id, key, value, version, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (data.org_id, key, val, new_ver)
            )

        for t in tasks:
            c.execute(
                "INSERT INTO tasks (org_id, ingest_id, task, owner, status, deadline, dependency, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (
                    data.org_id, ingest_id,
                    t.get("task"),
                    t.get("owner"),
                    t.get("status"),
                    t.get("deadline"),
                    t.get("dependency"),
                )
            )

        c.execute(
            "INSERT INTO risks (org_id, ingest_id, score, level, reasons, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (data.org_id, ingest_id, risk_score, risk_level, ", ".join(reasons))
        )

        conn.commit()
        conn.close()

        graph = build_graph(text, tasks, routing, truths)
        briefing = executive_briefing(text, tasks, truths, risk_level, routing, conflicts_out)

        return {
            "ingest_id": ingest_id,
            "tasks": tasks,
            "truths_updated": truths,
            "conflicts": conflicts_out,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "reasons": reasons,
            "routing": routing,
            "graph": graph,
            "briefing": briefing
        }

    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}

@app.get("/orgs/{org_id}/state")
def org_state(org_id: int):
    try:
        conn = get_connection()
        c = conn.cursor()

        c.execute("""
            SELECT key, value, version, created_at
            FROM truths
            WHERE org_id=?
            ORDER BY key, version DESC
        """, (org_id,))
        rows = c.fetchall()

        latest_truths = {}
        timeline = {}
        for key, value, version, created_at in rows:
            timeline.setdefault(key, []).append({"value": value, "version": version, "time": created_at})
            if key not in latest_truths:
                latest_truths[key] = {"value": value, "version": version, "time": created_at}

        c.execute("""
            SELECT key, old_value, new_value, question, created_at
            FROM conflicts
            WHERE org_id=?
            ORDER BY id DESC LIMIT 10
        """, (org_id,))
        conflicts = [
            {"key": r[0], "old_value": r[1], "new_value": r[2], "question": r[3], "time": r[4]}
            for r in c.fetchall()
        ]

        c.execute("""
            SELECT id, source, text, created_at
            FROM ingests
            WHERE org_id=?
            ORDER BY id DESC LIMIT 50
        """, (org_id,))
        history = [{"id": r[0], "source": r[1], "text": r[2], "time": r[3]} for r in c.fetchall()]

        c.execute("""
            SELECT score, level, reasons, created_at
            FROM risks
            WHERE org_id=?
            ORDER BY id DESC LIMIT 1
        """, (org_id,))
        rr = c.fetchone()
        latest_risk = {"score": rr[0], "level": rr[1], "reasons": rr[2], "time": rr[3]} if rr else None

        tasks = []
        if history:
            last_ingest_id = history[0]["id"]
            c.execute("""
                SELECT task, owner, status, deadline, dependency, created_at
                FROM tasks
                WHERE org_id=? AND ingest_id=?
                ORDER BY id DESC
            """, (org_id, last_ingest_id))
            tasks = [{
                "task": r[0], "owner": r[1], "status": r[2], "deadline": r[3], "dependency": r[4], "time": r[5]
            } for r in c.fetchall()]

        conn.close()

        return {
            "org_id": org_id,
            "latest_truths": latest_truths,
            "truth_timeline": timeline,
            "latest_risk": latest_risk,
            "conflicts": conflicts,
            "latest_tasks": tasks,
            "history": history
        }

    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}

@app.post("/ask")
def ask(q: AskInput):
    try:
        state = org_state(q.org_id)
        if "error" in state:
            return {"answer": state}

        question = (q.question or "").lower()
        if "changed" in question or "today" in question or "digest" in question:
            digest = {
                "latest_risk": state["latest_risk"],
                "truths": state["latest_truths"],
                "conflicts": state["conflicts"][:5],
                "recent_updates": state["history"][:5]
            }
            return {"answer": digest}

        return {"answer": {"hint": "Try: What changed today?"}}
    except Exception as e:
        return {"answer": {"error": str(e), "trace": traceback.format_exc()}}