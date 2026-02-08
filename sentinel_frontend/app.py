import streamlit as st
import requests
from datetime import datetime
from streamlit_agraph import agraph, Node, Edge, Config

API = "http://127.0.0.1:8011"

st.set_page_config(page_title="SentinelOS — AI Chief of Staff", layout="wide")

# ---------------- Helpers ----------------
def api_get(path: str):
    r = requests.get(f"{API}{path}", timeout=20)
    r.raise_for_status()
    return r.json()

def api_post(path: str, payload: dict):
    r = requests.post(f"{API}{path}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

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

def get_state(org_id: int):
    """Centralized safe state loader with error handling."""
    try:
        state = api_get(f"/orgs/{int(org_id)}/state")
        return state
    except Exception as e:
        return {"error": str(e)}

def state_has_error(state: dict) -> bool:
    return isinstance(state, dict) and ("error" in state)

# ---------------- UI Theme ----------------
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

# ---------------- Sidebar ----------------
st.sidebar.title("SentinelOS")
st.sidebar.caption("AI Chief of Staff — coordination clarity (not surveillance)")

# Connection debug
st.sidebar.markdown("### 🔌 Connection")
st.sidebar.code(API, language="text")

# org id input
org_id = st.sidebar.number_input("Org ID", min_value=1, value=1, step=1)

# Quick live state preview
with st.sidebar.expander("🔎 Debug /state response", expanded=False):
    dbg = get_state(int(org_id))
    st.json(dbg)

page = st.sidebar.radio(
    "Menu",
    ["Dashboard", "Analyze Update", "Source of Truth", "Conflicts", "History", "Graph"]
)

st.sidebar.markdown("---")
st.sidebar.subheader("Demo Samples")
if "sample" not in st.session_state:
    st.session_state.sample = ""

if st.sidebar.button("Sample: Blocked + Launch"):
    st.session_state.sample = "Backend is blocked. Launch Friday. Frontend waiting on API. Deadline tomorrow."
if st.sidebar.button("Sample: Conflict (launch change)"):
    st.session_state.sample = "Launch Friday. Priority P0."
if st.sidebar.button("Sample: Make Conflict Now"):
    st.session_state.sample = "Launch Monday. Priority P0."
if st.sidebar.button("Sample: Normal update"):
    st.session_state.sample = "Weekly update: UI polish done. No blockers. Next: add routing panel."

# ---------------- Header ----------------
st.title("SentinelOS — Superhuman AI Chief of Staff")

# ---------------- Pages ----------------
if page == "Dashboard":
    st.subheader("Executive Dashboard")

    state = get_state(int(org_id))
    if state_has_error(state):
        st.error("Backend state fetch failed.")
        st.write(state.get("error"))
        st.stop()

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
            st.info("No truths stored yet. Go to Analyze Update and ingest: 'Launch Friday. Priority P0.'")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### 🗞 Auto Digest (What changed today?)")
    if st.button("Generate Executive Digest", use_container_width=True):
        try:
            digest = api_post("/ask", {"org_id": int(org_id), "question": "What changed today?"})
            st.json(digest.get("answer", digest))
        except Exception as e:
            st.error(f"Digest failed: {e}")

    st.markdown("### Recent updates")
    if history:
        for h in history[:10]:
            st.markdown(f"- **#{h.get('id')}** ({h.get('source')}) — {h.get('text')}")
    else:
        st.write("No updates yet. Analyze an update first.")

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
        if not text.strip():
            st.warning("Please paste an update (or click a sample in the sidebar).")
            st.stop()

        try:
            result = api_post("/ingest", {"org_id": int(org_id), "text": text.strip(), "source": source})
        except Exception as e:
            st.error(f"Backend /ingest call failed: {e}")
            st.stop()

        if isinstance(result, dict) and result.get("error"):
            st.error("Backend returned an error payload:")
            st.json(result)
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
            st.dataframe(truths, use_container_width=True) if truths else st.info("No truth updates detected. Try: 'Launch Friday. Priority P0.'")

        with t4:
            if conflicts:
                st.error("Conflict detected — needs human confirmation")
                for cc in conflicts:
                    st.write(f"**Key:** {cc.get('key')}")
                    st.write(f"Old: `{cc.get('old_value')}`  →  New: `{cc.get('new_value')}`")
                    st.write(f"Resolution question: **{cc.get('question')}**")
                    st.markdown("---")
            else:
                st.success("No conflicts detected (yet). Tip: ingest 'Launch Friday' then 'Launch Monday'.")

        with t5:
            st.write("**Communication Flow Map**")
            render_graph(result.get("graph", {}))

elif page == "Source of Truth":
    st.subheader("Source of Truth (Versioned Memory)")

    state = get_state(int(org_id))
    if state_has_error(state):
        st.error("State endpoint not available.")
        st.write(state.get("error"))
        st.stop()

    latest_truths = state.get("latest_truths") or {}
    timeline = state.get("truth_timeline") or {}

    if not latest_truths and not timeline:
        st.warning("No truths found for this Org ID.")
        st.info("Go to Analyze Update and ingest: `Launch Friday. Priority P0.`")
        st.markdown("### Debug /state")
        st.json(state)
        st.stop()

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("### Latest Truths")
        for k, v in latest_truths.items():
            st.write(f"• **{k}** → `{v.get('value')}` (v{v.get('version')})")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("### Version History")
        for key, versions in timeline.items():
            st.write(f"**{key}**")
            st.dataframe(versions, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "Conflicts":
    st.subheader("Conflicts (Deconfliction)")

    state = get_state(int(org_id))
    if state_has_error(state):
        st.error("State endpoint not available.")
        st.write(state.get("error"))
        st.stop()

    conflicts = safe_list(state.get("conflicts"))
    if not conflicts:
        st.warning("No conflicts stored yet.")
        st.info("Create one now:\n1) Ingest: `Launch Friday.`\n2) Ingest: `Launch Monday.`")
        st.markdown("### Debug /state")
        st.json(state)
    else:
        for cc in conflicts:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.error(f"Conflict: {cc.get('key')}")
            st.write(f"Old: `{cc.get('old_value')}` → New: `{cc.get('new_value')}`")
            st.write(f"Question: **{cc.get('question')}**")
            st.caption(f"Time: {cc.get('time')}")
            st.markdown('</div>', unsafe_allow_html=True)
            st.markdown("")

elif page == "History":
    st.subheader("History (All ingested updates)")

    state = get_state(int(org_id))
    if state_has_error(state):
        st.error("State endpoint not available.")
        st.write(state.get("error"))
        st.stop()

    history = safe_list(state.get("history"))

    if not history:
        st.warning("No history found for this Org ID.")
        st.info("Go to Analyze Update and ingest something.")
        st.markdown("### Debug /state")
        st.json(state)
    else:
        for h in history:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"**#{h.get('id')} — {h.get('source')}**")
            st.write(h.get("text"))
            st.caption(f"Time: {h.get('time')}")
            st.markdown('</div>', unsafe_allow_html=True)
            st.markdown("")

elif page == "Graph":
    st.subheader("Graph View (Visual)")

    text = st.text_area("Paste update to see graph", value=st.session_state.sample or "Backend is blocked. Launch Friday.", height=120)

    if st.button("Generate Graph"):
        try:
            result = api_post("/ingest", {"org_id": int(org_id), "text": text.strip(), "source": "Chat update"})
            if isinstance(result, dict) and result.get("error"):
                st.error("Backend returned an error payload:")
                st.json(result)
            else:
                render_graph(result.get("graph", {}))
        except Exception as e:
            st.error(f"Graph generation failed: {e}")