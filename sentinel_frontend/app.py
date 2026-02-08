import os
import sys
import sqlite3
import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config

# ---------------- Paths (so we can import backend code) ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # SentinelOS/
BACKEND_DIR = os.path.join(BASE_DIR, "sentinel_backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from intelligence import (
    extract_tasks, extract_truths, compute_risk,
    routing_suggestions, detect_conflicts, build_graph,
    executive_briefing
)

DB_PATH = os.path.join(BACKEND_DIR, "sentinel.db")


# ---------------- DB Helpers ----------------
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

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

def _get_columns(conn, table_name: str):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    return [r[1] for r in c.fetchall()]

def _ensure_column(conn, table: str, col: str, col_type: str):
    cols = _get_columns(conn, table)
    if col not in cols:
        cur = conn.cursor()
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        conn.commit()

def ensure_schema():
    # in case someone has an older db without created_at etc.
    conn = get_connection()
    for (table, col, col_type) in [
        ("ingests","created_at","DATETIME"),
        ("truths","created_at","DATETIME"),
        ("conflicts","created_at","DATETIME"),
        ("tasks","created_at","DATETIME"),
        ("risks","created_at","DATETIME"),
    ]:
        try:
            _ensure_column(conn, table, col, col_type)
        except Exception:
            pass
    conn.close()

def _get_latest_truth(conn, org_id: int, key: str):
    c = conn.cursor()
    c.execute(
        "SELECT value, version FROM truths WHERE org_id=? AND key=? ORDER BY version DESC LIMIT 1",
        (org_id, key),
    )
    row = c.fetchone()
    return (row[0], row[1]) if row else (None, 0)

def ingest_update(org_id: int, text: str, source: str):
    text = (text or "").strip()
    if not text:
        return {"error": "Empty text"}

    tasks = extract_tasks(text)
    truths = extract_truths(text)
    risk_score, risk_level, reasons = compute_risk(text, tasks)
    routing = routing_suggestions(text, tasks)

    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "INSERT INTO ingests (org_id, source, text) VALUES (?, ?, ?)",
        (org_id, source, text),
    )
    ingest_id = c.lastrowid

    conflicts_out = []
    for tr in truths:
        key, val = tr["key"], tr["value"]
        old_val, old_ver = _get_latest_truth(conn, org_id, key)

        if detect_conflicts(old_val, val):
            question = f"Which is correct for {key}: '{old_val}' or '{val}'?"
            c.execute(
                "INSERT INTO conflicts (org_id, key, old_value, new_value, question) VALUES (?, ?, ?, ?, ?)",
                (org_id, key, old_val, val, question),
            )
            conflicts_out.append(
                {"key": key, "old_value": old_val, "new_value": val, "question": question}
            )

        new_ver = old_ver + 1
        c.execute(
            "INSERT INTO truths (org_id, key, value, version) VALUES (?, ?, ?, ?)",
            (org_id, key, val, new_ver),
        )

    for t in tasks:
        c.execute(
            "INSERT INTO tasks (org_id, ingest_id, task, owner, status, deadline, dependency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                org_id,
                ingest_id,
                t.get("task"),
                t.get("owner"),
                t.get("status"),
                t.get("deadline"),
                t.get("dependency"),
            ),
        )

    c.execute(
        "INSERT INTO risks (org_id, ingest_id, score, level, reasons) VALUES (?, ?, ?, ?, ?)",
        (org_id, ingest_id, risk_score, risk_level, ", ".join(reasons)),
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
        "briefing": briefing,
    }

def org_state(org_id: int):
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
        ORDER BY id DESC LIMIT 20
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
            SELECT task, owner, status, deadline, dependency
            FROM tasks
            WHERE org_id=? AND ingest_id=?
            ORDER BY id DESC
        """, (org_id, last_ingest_id))
        tasks = [
            {"task": r[0], "owner": r[1], "status": r[2], "deadline": r[3], "dependency": r[4]}
            for r in c.fetchall()
        ]

    conn.close()
    return {
        "org_id": org_id,
        "latest_truths": latest_truths,
        "truth_timeline": timeline,
        "latest_risk": latest_risk,
        "conflicts": conflicts,
        "latest_tasks": tasks,
        "history": history,
    }


# ---------------- UI Helpers ----------------
def pill(level: str):
    level = (level or "").upper()
    if level == "HIGH":
        return "🔴 HIGH"
    if level == "MEDIUM":
        return "🟠 MEDIUM"
    return "🟢 LOW"

def safe_list(x):
    return x if isinstance(x, list) else []

def render_graph(graph_data):
    if not graph_data or "nodes" not in graph_data:
        st.info("No graph data yet. Analyze an update first.")
        return

    nodes = []
    edges = []

    for n in graph_data.get("nodes", []):
        typ = n.get("type", "unknown")
        color = "#9aa0a6"
        size = 25

        if typ == "person":
            color = "#FF6B6B"; size = 30
        elif typ == "task":
            color = "#4ECDC4"; size = 28
        elif typ == "truth":
            color = "#FFD166"; size = 26
        elif typ == "dependency":
            color = "#6C5CE7"; size = 26

        nodes.append(Node(id=n["id"], label=n["id"], size=size, color=color))

    for e in graph_data.get("edges", []):
        edges.append(Edge(source=e["source"], target=e["target"]))

    config = Config(width=950, height=600, directed=True, physics=True, hierarchical=False)
    agraph(nodes=nodes, edges=edges, config=config)


# ---------------- Boot DB once ----------------
init_db()
ensure_schema()

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="SentinelOS — AI Chief of Staff", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 1.2rem; }
h1, h2, h3 { letter-spacing: 0.2px; }
.card {
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 14px;
  padding: 14px 16px;
  background: rgba(255,255,255,0.03);
}
.small { opacity: 0.85; font-size: 0.92rem; }
</style>
""", unsafe_allow_html=True)

# Sidebar
st.sidebar.title("SentinelOS")
st.sidebar.caption("AI Chief of Staff — coordination clarity (not surveillance)")
org_id = st.sidebar.number_input("Org ID", min_value=1, value=1, step=1)

page = st.sidebar.radio("Menu", ["Dashboard", "Analyze Update", "Source of Truth", "Conflicts", "History", "Graph"])

st.sidebar.markdown("---")
st.sidebar.subheader("Demo Samples")
if "sample" not in st.session_state:
    st.session_state.sample = ""

if st.sidebar.button("Sample: Blocked + Launch"):
    st.session_state.sample = "Backend is blocked. Launch Friday. Frontend waiting on API. Deadline tomorrow."
if st.sidebar.button("Sample: Conflict (launch change)"):
    st.session_state.sample = "Launch Monday. Deadline tomorrow. Owner is unclear. Backend blocked."
if st.sidebar.button("Sample: Normal update"):
    st.session_state.sample = "Weekly update: UI polish done. No blockers. Next: add routing panel."

st.title("SentinelOS — Superhuman AI Chief of Staff")

if page == "Dashboard":
    st.subheader("Executive Dashboard")
    state = org_state(int(org_id))

    latest_risk = state.get("latest_risk") or {}
    conflicts = safe_list(state.get("conflicts"))
    history = safe_list(state.get("history"))
    latest_truths = state.get("latest_truths") or {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest Risk", f"{latest_risk.get('score','-')} / 100", pill(latest_risk.get("level","LOW")))
    c2.metric("Conflicts", str(len(conflicts)))
    c3.metric("Updates Stored", str(len(history)))
    c4.metric("Truth Keys", str(len(latest_truths)))

    st.markdown("### Chief of Staff Snapshot")
    left, right = st.columns([1, 1])

    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("**Risk Reasons (evidence)**")
        reasons_text = latest_risk.get("reasons") or ""
        if reasons_text:
            for r in reasons_text.split(","):
                st.write(f"• {r.strip()}")
        else:
            st.write("• No risks yet — ingest an update.")
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("**Source of Truth (latest)**")
        if latest_truths:
            for k, v in latest_truths.items():
                st.write(f"• **{k}** → `{v.get('value')}` (v{v.get('version')})")
        else:
            st.info("No truths stored yet. Try: 'Launch Friday'.")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### Recent updates")
    if history:
        for h in history[:8]:
            st.markdown(f"- **#{h.get('id')}** ({h.get('source')}) — {h.get('text')}")
    else:
        st.write("No updates yet.")

elif page == "Analyze Update":
    st.subheader("Analyze Update")
    st.write("Paste a chat/meeting update. SentinelOS extracts **risk, tasks, routing, truths, conflicts** and shows a **graph**.")

    text = st.text_area(
        "Communication Update",
        value=st.session_state.sample or "",
        height=160,
        placeholder="Example: Backend is blocked. Launch Friday."
    )
    source = st.selectbox("Source type", ["Chat update", "Meeting summary", "Weekly update"])

    col1, col2 = st.columns([1, 1])
    with col1:
        run = st.button("🚀 Analyze", type="primary", use_container_width=True)
    with col2:
        st.button("Clear", use_container_width=True, on_click=lambda: st.session_state.update(sample=""))

    if run:
        result = ingest_update(int(org_id), text.strip(), source)
        if "error" in result:
            st.error(result["error"])
            st.stop()

        risk_score = result.get("risk_score", 0)
        risk_level = result.get("risk_level", "LOW")
        reasons = safe_list(result.get("reasons"))
        routing = safe_list(result.get("routing"))
        tasks = safe_list(result.get("tasks"))
        truths = safe_list(result.get("truths_updated"))
        conflicts = safe_list(result.get("conflicts"))
        briefing = result.get("briefing", "")

        a, b, c = st.columns([1, 2, 2])
        with a:
            st.metric("Risk Score", risk_score, pill(risk_level))
            st.progress(min(max(int(risk_score), 0), 100) / 100)

        with b:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.write("**Evidence**")
            if reasons:
                for r in reasons:
                    st.write(f"• {r}")
            else:
                st.write("• No risk signals detected.")
            st.markdown('</div>', unsafe_allow_html=True)

        with c:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.write("**Targeted Routing (who should know)**")
            if routing:
                for p in routing:
                    st.success(f"Notify: {p}")
            else:
                st.info("No routing suggested.")
            st.markdown('</div>', unsafe_allow_html=True)

        t1, t2, t3, t4, t5 = st.tabs(["🧠 Briefing", "📋 Tasks", "🧾 Truths", "⚠️ Conflicts", "🕸 Graph"])

        with t1:
            st.markdown("### 🧠 Chief of Staff — Executive Brief")
            if briefing:
                st.text_area("Briefing (copy/paste)", value=briefing, height=240)
                st.download_button("⬇️ Download Briefing (.txt)", data=briefing, file_name="sentinelos_briefing.txt")
            else:
                st.info("No briefing generated.")

        with t2:
            st.dataframe(tasks, use_container_width=True) if tasks else st.info("No tasks extracted.")

        with t3:
            st.dataframe(truths, use_container_width=True) if truths else st.info("No truth updates detected.")

        with t4:
            if conflicts:
                st.error("Conflict detected — needs human confirmation")
                for cc in conflicts:
                    st.write(f"**Key:** {cc.get('key')}")
                    st.write(f"Old: `{cc.get('old_value')}`  →  New: `{cc.get('new_value')}`")
                    st.write(f"Resolution question: **{cc.get('question')}**")
                    st.markdown("---")
            else:
                st.success("No conflicts detected.")

        with t5:
            st.write("**Communication Flow Map**")
            render_graph(result.get("graph", {}))

elif page == "Source of Truth":
    st.subheader("Source of Truth (Versioned Memory)")
    state = org_state(int(org_id))

    latest_truths = state.get("latest_truths") or {}
    timeline = state.get("truth_timeline") or {}

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("### Latest Truths")
        if latest_truths:
            for k, v in latest_truths.items():
                st.write(f"• **{k}** → `{v.get('value')}` (v{v.get('version')})")
        else:
            st.info("No truths yet. Ingest: 'Launch Friday'.")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("### Version History")
        if timeline:
            for key, versions in timeline.items():
                st.write(f"**{key}**")
                st.dataframe(versions, use_container_width=True)
        else:
            st.info("No version timeline yet.")
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "Conflicts":
    st.subheader("Conflicts (Deconfliction)")
    state = org_state(int(org_id))
    conflicts = safe_list(state.get("conflicts"))

    if not conflicts:
        st.success("No conflicts stored yet.")
        st.info("Create one by ingesting: 'Launch Friday' then 'Launch Monday'.")
    else:
        for cc in conflicts:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.error(f"Conflict: {cc.get('key')}")
            st.write(f"Old: `{cc.get('old_value')}` → New: `{cc.get('new_value')}`")
            st.write(f"Question: **{cc.get('question')}**")
            st.caption(f"Time: {cc.get('time')}")
            st.markdown('</div>', unsafe_allow_html=True)

elif page == "History":
    st.subheader("History (All ingested updates)")
    state = org_state(int(org_id))
    history = safe_list(state.get("history"))

    if not history:
        st.info("No history yet. Go to Analyze Update and ingest something.")
    else:
        for h in history:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"**#{h.get('id')} — {h.get('source')}**")
            st.write(h.get("text"))
            st.caption(f"Time: {h.get('time')}")
            st.markdown('</div>', unsafe_allow_html=True)

elif page == "Graph":
    st.subheader("Graph View (Visual)")
    text = st.text_area("Paste update to see graph", value=st.session_state.sample or "Backend is blocked. Launch Friday.", height=120)

    if st.button("Generate Graph"):
        result = ingest_update(int(org_id), text.strip(), "Chat update")
        render_graph(result.get("graph", {}))