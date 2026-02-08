import re
from typing import List, Dict, Tuple

DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

TEAM_KEYWORDS = {
    "backend": "Backend Lead",
    "frontend": "Frontend Lead",
    "ui": "Frontend Lead",
    "design": "Design Lead",
    "qa": "QA Lead",
    "testing": "QA Lead",
    "api": "Backend Lead",
    "infra": "Infra Lead",
    "devops": "Infra Lead",
    "security": "Security Lead",
}

def _low(s: str) -> str:
    return (s or "").lower()

def _find_day(text_low: str):
    m = re.search(r"\b(" + "|".join(DAYS) + r")\b", text_low)
    return m.group(1).capitalize() if m else None

def _normalize_person_name(name: str) -> str:
    # clean spaces, remove trailing punctuation
    name = (name or "").strip()
    name = re.sub(r"[\.\,\;\:\-]+$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name

# ---------------- Truth Extraction ----------------
def extract_truths(text: str) -> List[Dict]:
    """
    Returns list of truth updates like:
    [{"key":"launch_date","value":"Friday"}, {"key":"priority","value":"P0"}]
    """
    t = _low(text)
    truths: List[Dict] = []

    # launch date
    if "launch" in t:
        day = _find_day(t)
        if day:
            truths.append({"key": "launch_date", "value": day})

    # priority
    m = re.search(r"priority\s*[:\-]?\s*(p0|p1|p2|high|medium|low)", t)
    if m:
        truths.append({"key": "priority", "value": m.group(1).upper()})

    # decision signals
    if any(kw in t for kw in ["we decided", "decision", "approved", "finalized", "confirmed"]):
        truths.append({"key": "decision_status", "value": "UPDATED"})

    # scope changes
    if "scope" in t and any(kw in t for kw in ["change", "changed", "reduced", "expanded", "updated"]):
        truths.append({"key": "scope", "value": "CHANGED"})

    return truths

# ---------------- Task Extraction ----------------
def extract_tasks(text: str) -> List[Dict]:
    """
    Simple heuristic task extraction: blockers, deadlines, launch readiness, owners
    """
    t = _low(text)
    tasks: List[Dict] = []

    # deadline detection (global)
    deadline = None
    if "tomorrow" in t:
        deadline = "Tomorrow"
    elif "today" in t:
        deadline = "Today"
    elif "eod" in t or "end of day" in t:
        deadline = "EOD"
    else:
        day = _find_day(t)
        if day and ("by" in t or "deadline" in t):
            deadline = day

    # blocker detection
    if "blocked" in t or "waiting on" in t or "depends on" in t:
        dep = None
        m = re.search(r"blocked by ([a-z0-9 \-_]+)", t)
        if m:
            dep = m.group(1).strip()
        m2 = re.search(r"waiting on ([a-z0-9 \-_]+)", t)
        if not dep and m2:
            dep = m2.group(1).strip()

        tasks.append({
            "task": "Resolve blocker / dependency",
            "status": "blocked",
            "dependency": dep,
            "deadline": deadline,
            "owner": None
        })

    # launch task
    if "launch" in t:
        day = _find_day(t)
        tasks.append({
            "task": "Launch readiness",
            "status": "in_progress" if ("in progress" in t or "progress" in t) else None,
            "deadline": day or deadline,
            "owner": "Project Manager" if ("pm" in t or "project manager" in t) else None,
            "dependency": None
        })

    # submission/deliverable task
    if any(kw in t for kw in ["submit", "submission", "deliver", "deliverable", "deadline"]):
        tasks.append({
            "task": "Prepare submission / deliverable",
            "status": None,
            "deadline": deadline,
            "owner": None,
            "dependency": None
        })

    # owner extraction
    m = re.search(r"(assigned to|owner is|handled by|owned by)\s+([a-zA-Z ]{2,40})", text)
    if m:
        owner = _normalize_person_name(m.group(2))
        if tasks:
            tasks[-1]["owner"] = owner
        else:
            tasks.append({
                "task": "Owned item",
                "status": None,
                "deadline": deadline,
                "owner": owner,
                "dependency": None
            })

    # capture multiple owners like:
    # "Ali Khan was assigned..." / "Sana will prepare..."
    m3 = re.findall(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(will|was)\s+(handle|prepare|coordinate|own|lead)", text)
    # attach as owners to a generic coordination task (optional)
    for name, _, action in m3:
        nm = _normalize_person_name(name)
        tasks.append({
            "task": f"{action.capitalize()} task",
            "status": None,
            "deadline": deadline,
            "owner": nm,
            "dependency": None
        })

    return tasks

# ---------------- Conflict Detection ----------------
def detect_conflicts(latest_truth_value: str, new_value: str) -> bool:
    if latest_truth_value and new_value and latest_truth_value.strip() != new_value.strip():
        return True
    return False

# ---------------- Risk Computation ----------------
def compute_risk(text: str, tasks: List[Dict]) -> Tuple[int, str, List[str]]:
    t = _low(text)
    score = 15
    reasons: List[str] = []

    if "blocked" in t or any(task.get("status") == "blocked" for task in tasks):
        score += 35
        reasons.append("Blocker / dependency detected")

    if any(kw in t for kw in ["unclear", "not sure", "unknown", "needs confirmation"]):
        score += 20
        reasons.append("Ambiguity signal (unclear/unknown)")

    if any(kw in t for kw in ["deadline", "submission", "submit"]):
        score += 15
        reasons.append("Deadline / submission mentioned")

    if any(kw in t for kw in ["tomorrow", "today", "eod", "end of day"]):
        score += 20
        reasons.append("Very near-term deadline (today/tomorrow/EOD)")

    if tasks and not any(task.get("owner") for task in tasks):
        score += 10
        reasons.append("Tasks present but owner not identified")

    score = min(score, 100)

    if score >= 70:
        level = "HIGH"
    elif score >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    return score, level, reasons

# ---------------- Routing Suggestions ----------------
def routing_suggestions(text: str, tasks: List[Dict]) -> List[str]:
    t = _low(text)
    notify = set()

    for kw, role in TEAM_KEYWORDS.items():
        if kw in t:
            notify.add(role)

    if any(kw in t for kw in ["launch", "deadline", "submission", "submit"]):
        notify.add("Project Manager")

    for task in tasks:
        if task.get("owner"):
            notify.add(task["owner"])

    if not notify:
        notify.add("Project Manager")

    return list(notify)

# ---------------- Graph Builder ----------------
def build_graph(text: str, tasks: List[Dict], routing: List[str], truths: List[Dict]):
    nodes = []
    edges = []

    def add_node(n, typ):
        if not any(x["id"] == n for x in nodes):
            nodes.append({"id": n, "type": typ})

    # people
    for p in routing:
        add_node(p, "person")

    # tasks
    for task in tasks:
        tname = task.get("task") or "Task"
        add_node(tname, "task")
        for p in routing:
            edges.append({"source": p, "target": tname, "type": "notified_about"})
        if task.get("dependency"):
            dep = f"Dependency: {task['dependency']}"
            add_node(dep, "dependency")
            edges.append({"source": tname, "target": dep, "type": "blocked_by"})

    # truths
    for tr in truths:
        truth_node = f"{tr.get('key')} = {tr.get('value')}"
        add_node(truth_node, "truth")
        for task in tasks:
            edges.append({"source": truth_node, "target": task.get("task") or "Task", "type": "context"})

    return {"nodes": nodes, "edges": edges}

# ---------------- Executive Briefing (SINGLE + CONSISTENT) ----------------
def executive_briefing(original_text: str, tasks: list, truths: list, risk_level: str, routing: list, conflicts: list):
    """
    Clean single version. No duplicates. Consistent output.
    """
    risk_level = (risk_level or "LOW").upper()

    lines = []
    lines.append("Chief of Staff Briefing")
    lines.append("")
    lines.append("Situation:")
    lines.append(f"- {original_text.strip()}")
    lines.append("")
    lines.append(f"Risk Level: {risk_level}")
    lines.append("")

    if truths:
        lines.append("Truth Updates:")
        for tr in truths:
            lines.append(f"- {tr.get('key')} = {tr.get('value')}")
        lines.append("")

    if tasks:
        lines.append("Action Items:")
        for t in tasks:
            owner = t.get("owner") or "Unassigned"
            status = t.get("status") or "open"
            deadline = t.get("deadline") or "-"
            dep = t.get("dependency") or "-"
            lines.append(
                f"- {t.get('task','Task')} | owner={owner} | status={status} | deadline={deadline} | dependency={dep}"
            )
        lines.append("")

    if conflicts:
        lines.append("Conflicts Needing Confirmation:")
        for c in conflicts:
            if isinstance(c, dict) and c.get("question"):
                lines.append(f"- {c.get('question')}")
            else:
                lines.append(f"- {str(c)}")
        lines.append("")

    lines.append("Notify:")
    if routing:
        for r in routing:
            lines.append(f"- {r}")
    else:
        lines.append("- Project Manager")

    return "\n".join(lines)