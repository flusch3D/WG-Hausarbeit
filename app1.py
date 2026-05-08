"""
╔══════════════════════════════════════════════════════════╗
║       🏠 Smart WG Task & Fairness System  v2.0           ║
║       Backend: Supabase (PostgreSQL)                     ║
║       Start: streamlit run wg_app.py                     ║
╚══════════════════════════════════════════════════════════╝

Abhängigkeiten:
    pip install streamlit pandas plotly supabase python-dotenv

Supabase Setup:
    1. Projekt auf supabase.com erstellen
    2. SQL aus supabase_schema.sql ausführen (im selben Ordner)
    3. .env Datei anlegen:
       SUPABASE_URL=https://xxxx.supabase.co
       SUPABASE_KEY=eyJhbGci...  (anon/public key)

Start:
    streamlit run wg_app.py
"""

import streamlit as st
import hashlib
import uuid
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date
from typing import Optional
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────
# SUPABASE CLIENT
# ──────────────────────────────────────────────────

@st.cache_resource
def get_supabase() -> Client:
    # 1. Versuche die Werte direkt aus den Streamlit Secrets zu laden
    # Das funktioniert in der Cloud (via Settings) und lokal (via .streamlit/secrets.toml)
    print("-"*30)
    try:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        print(url + "\n" + "key:" +key)
        print("-"*30)
    except KeyError:
        # 2. Fallback für lokale Entwicklung mit .env Datei
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")

    # 3. Sicherheitscheck: Wenn eines von beiden fehlt oder leer ist
    if not url or not key:
        st.error("❌ Supabase-Zugangsdaten fehlen!")
        st.info("Stelle sicher, dass SUPABASE_URL und SUPABASE_KEY in den Streamlit Cloud 'Secrets' eingetragen sind.")
        st.stop()
        
    return create_client(url, key)


# ──────────────────────────────────────────────────
# KONSTANTEN
# ──────────────────────────────────────────────────

CATEGORIES = ["🧹 Putzen", "🛒 Einkaufen", "🗑️ Müll", "🍳 Kochen",
              "🧺 Waschen", "🌿 Pflanzen", "🔧 Reparatur", "📦 Sonstiges"]
RECURRENCES = {"täglich": "daily", "wöchentlich": "weekly", "monatlich": "monthly"}
RECURRENCE_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}

# Berechtigungsstufen
ROLES = {
    "moderator": {"label": "👑 Moderator", "color": "#f59e0b"},
    "editor":    {"label": "✏️ Editor",    "color": "#60a5fa"},
    "member":    {"label": "👤 Mitglied",  "color": "#888"},
}

# Was darf welche Rolle?
PERMISSIONS = {
    "moderator": {"create_task", "edit_task", "delete_task", "run_rotation",
                  "kick_member", "lock_wg", "manage_roles", "complete_own", "complete_all"},
    "editor":    {"create_task", "edit_task", "run_rotation", "complete_own", "complete_all"},
    "member":    {"complete_own"},
}

def can(role: str, action: str) -> bool:
    return action in PERMISSIONS.get(role, set())


# ──────────────────────────────────────────────────
# HELPER
# ──────────────────────────────────────────────────

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def new_id() -> str:
    return str(uuid.uuid4())

def today_str() -> str:
    return date.today().isoformat()

def week_start(offset: int = 0) -> date:
    d = date.today()
    return d - timedelta(days=d.weekday()) + timedelta(weeks=offset)


# ──────────────────────────────────────────────────
# USER AUTH
# ──────────────────────────────────────────────────

def register_user(name: str, email: str, password: str) -> tuple[bool, str]:
    sb = get_supabase()
    uid = new_id()
    try:
        result = sb.table("users").insert({
            "name": name,
            "email": email.lower(),
            "password": hash_pw(password)
        }).execute()
        if result.data:
            return True, uid
        return False, "Fehler beim Registrieren."
    except Exception as e:
        msg = str(e)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            return False, "E-Mail bereits registriert."
        return False, msg

def login_user(email: str, password: str) -> Optional[dict]:
    sb = get_supabase()
    result = sb.table("users").select("*").eq("email", email.lower()).eq("password", hash_pw(password)).execute()
    if result.data:
        return result.data[0]
    return None


# ──────────────────────────────────────────────────
# WG MANAGEMENT
# ──────────────────────────────────────────────────

def create_wg(name: str, user_id: str) -> dict:
    sb = get_supabase()
    wg_id = new_id()
    invite = new_id()
    sb.table("wgs").insert({
        "id": wg_id, "name": name,
        "invite_code": invite, "created_by": user_id,
        "locked": False
    }).execute()
    sb.table("wg_members").insert({
        "wg_id": wg_id, "user_id": user_id, "role": "moderator"
    }).execute()
    return {"id": wg_id, "invite_code": invite}

def join_wg(invite_code: str, user_id: str) -> tuple[bool, str]:
    sb = get_supabase()
    wg_res = sb.table("wgs").select("*").eq("invite_code", invite_code).execute()
    if not wg_res.data:
        return False, "Ungültiger Einladungscode."
    wg = wg_res.data[0]
    if wg.get("locked"):
        return False, "Diese WG ist gesperrt. Neue Mitglieder können nicht beitreten."
    # already member?
    exists = sb.table("wg_members").select("wg_id").eq("wg_id", wg["id"]).eq("user_id", user_id).execute()
    if exists.data:
        return False, "Du bist bereits Mitglied dieser WG."
    sb.table("wg_members").insert({
        "wg_id": wg["id"], "user_id": user_id, "role": "member"
    }).execute()
    return True, wg["name"]

def get_user_wgs(user_id: str) -> list[dict]:
    sb = get_supabase()
    # Join via wg_members
    mem = sb.table("wg_members").select("wg_id").eq("user_id", user_id).execute()
    wg_ids = [m["wg_id"] for m in mem.data]
    if not wg_ids:
        return []
    result = sb.table("wgs").select("*").in_("id", wg_ids).execute()
    return result.data or []

def get_wg_members(wg_id: str) -> list[dict]:
    sb = get_supabase()
    mem = sb.table("wg_members").select("user_id, role, joined_at").eq("wg_id", wg_id).execute()
    if not mem.data:
        return []
    user_ids = [m["user_id"] for m in mem.data]
    users = sb.table("users").select("id, name, email").in_("id", user_ids).execute()
    user_map = {u["id"]: u for u in (users.data or [])}
    result = []
    for m in mem.data:
        u = user_map.get(m["user_id"], {})
        result.append({
            "id": m["user_id"],
            "name": u.get("name", "?"),
            "email": u.get("email", ""),
            "role": m.get("role", "member"),
            "joined_at": m.get("joined_at", ""),
        })
    return result

def get_member_role(wg_id: str, user_id: str) -> str:
    sb = get_supabase()
    res = sb.table("wg_members").select("role").eq("wg_id", wg_id).eq("user_id", user_id).execute()
    if res.data:
        return res.data[0].get("role", "member")
    return "member"

def set_member_role(wg_id: str, user_id: str, role: str):
    sb = get_supabase()
    sb.table("wg_members").update({"role": role}).eq("wg_id", wg_id).eq("user_id", user_id).execute()

def kick_member(wg_id: str, user_id: str):
    sb = get_supabase()
    sb.table("wg_members").delete().eq("wg_id", wg_id).eq("user_id", user_id).execute()

def set_wg_locked(wg_id: str, locked: bool):
    sb = get_supabase()
    sb.table("wgs").update({"locked": locked}).eq("id", wg_id).execute()


# ──────────────────────────────────────────────────
# TASK MANAGEMENT
# ──────────────────────────────────────────────────

def create_task(wg_id: str, title: str, description: str, category: str,
                recurrence: str, effort: int) -> str:
    sb = get_supabase()
    tid = new_id()
    sb.table("tasks").insert({
        "id": tid, "wg_id": wg_id, "title": title,
        "description": description, "category": category,
        "recurrence": recurrence, "effort_minutes": effort, "active": True
    }).execute()
    return tid

def update_task(task_id: str, title: str, description: str, category: str,
                recurrence: str, effort: int):
    sb = get_supabase()
    sb.table("tasks").update({
        "title": title, "description": description,
        "category": category, "recurrence": recurrence,
        "effort_minutes": effort
    }).eq("id", task_id).execute()

def get_tasks(wg_id: str) -> list[dict]:
    sb = get_supabase()
    result = sb.table("tasks").select("*").eq("wg_id", wg_id).eq("active", True).order("category").execute()
    return result.data or []

def delete_task(task_id: str):
    sb = get_supabase()
    sb.table("tasks").update({"active": False}).eq("id", task_id).execute()


# ──────────────────────────────────────────────────
# ROTATION ENGINE
# ──────────────────────────────────────────────────

def run_rotation(wg_id: str) -> int:
    """
    Rotation-Algorithmus – korrekt für daily / weekly / monthly:
    - Generiert 4 Perioden im Voraus ab der letzten bekannten Zuweisung
    - Fairness-basierte Zuteilung (wer am wenigsten hat, bekommt als nächstes)
    - Niemand bekommt dieselbe Aufgabe zweimal hintereinander (wenn >1 Mitglied)
    """
    sb = get_supabase()
    members = get_wg_members(wg_id)
    tasks = get_tasks(wg_id)
    if not members or not tasks:
        return 0

    member_ids = [m["id"] for m in members]
    n_members = len(member_ids)
    workload = get_workload_counts(wg_id)
    created = 0
    today = date.today()

    for task in tasks:
        days_ahead = RECURRENCE_DAYS.get(task["recurrence"], 7)
        n_periods = 4

        # Letzte bestehende Zuweisung für diesen Task
        last_res = sb.table("assignments") \
            .select("assigned_to, due_date") \
            .eq("task_id", task["id"]) \
            .order("due_date", desc=True) \
            .limit(1) \
            .execute()

        last_data = last_res.data[0] if last_res.data else None

        if last_data:
            last_person = last_data["assigned_to"]
            last_date = date.fromisoformat(last_data["due_date"])
            # Nächste Periode beginnt genau days_ahead nach dem letzten
            start_date = last_date + timedelta(days=days_ahead)
        else:
            last_person = None
            # Erste Zuweisung: für heute (oder nächsten passenden Tag)
            start_date = today

        # Lokale Kopie der Workload für faire Berechnung innerhalb der Schleife
        local_workload = dict(workload)

        for i in range(n_periods):
            due = start_date + timedelta(days=days_ahead * i)

            # Vergangene Termine überspringen
            if due < today:
                continue

            # Prüfen ob schon ein Assignment für Task+Datum existiert
            exists = sb.table("assignments") \
                .select("id") \
                .eq("task_id", task["id"]) \
                .eq("due_date", due.isoformat()) \
                .execute()
            if exists.data:
                continue

            # Fairste Person wählen (niedrigste Workload, nicht dieselbe wie zuletzt)
            sorted_members = sorted(member_ids, key=lambda uid: local_workload.get(uid, 0))
            next_person = None
            for candidate in sorted_members:
                if n_members == 1 or candidate != last_person:
                    next_person = candidate
                    break
            if not next_person:
                next_person = sorted_members[0]

            aid = new_id()
            sb.table("assignments").insert({
                "id": aid, "task_id": task["id"], "wg_id": wg_id,
                "assigned_to": next_person, "due_date": due.isoformat(),
                "status": "open", "comment": ""
            }).execute()
            created += 1
            last_person = next_person
            local_workload[next_person] = local_workload.get(next_person, 0) + 1

    return created

def get_workload_counts(wg_id: str) -> dict:
    sb = get_supabase()
    result = sb.table("assignments").select("assigned_to").eq("wg_id", wg_id).eq("status", "done").execute()
    counts: dict = {}
    for row in (result.data or []):
        uid = row["assigned_to"]
        counts[uid] = counts.get(uid, 0) + 1
    return counts

def get_assignments(wg_id: str, from_date: str = None, to_date: str = None,
                    user_id: str = None) -> list[dict]:
    sb = get_supabase()
    q = sb.table("assignments").select(
        "id, task_id, assigned_to, due_date, status, completed_at, comment, wg_id"
    ).eq("wg_id", wg_id)
    if from_date:
        q = q.gte("due_date", from_date)
    if to_date:
        q = q.lte("due_date", to_date)
    if user_id:
        q = q.eq("assigned_to", user_id)
    q = q.order("due_date")
    rows = q.execute().data or []

    # Enrich with task + user data
    task_ids = list({r["task_id"] for r in rows})
    user_ids = list({r["assigned_to"] for r in rows})
    tasks_map, users_map = {}, {}
    if task_ids:
        t_res = sb.table("tasks").select("id, title, category, effort_minutes, active").in_("id", task_ids).execute()
        tasks_map = {t["id"]: t for t in (t_res.data or [])}
    if user_ids:
        u_res = sb.table("users").select("id, name").in_("id", user_ids).execute()
        users_map = {u["id"]: u for u in (u_res.data or [])}

    result = []
    for r in rows:
        t = tasks_map.get(r["task_id"], {})
        if not t.get("active", True):
            continue
        u = users_map.get(r["assigned_to"], {})
        result.append({**r,
                        "title": t.get("title", "?"),
                        "category": t.get("category", ""),
                        "effort_minutes": t.get("effort_minutes", 0),
                        "person_name": u.get("name", "?")})
    return result

def complete_assignment(assignment_id: str, comment: str = ""):
    sb = get_supabase()
    sb.table("assignments").update({
        "status": "done",
        "completed_at": datetime.now().isoformat(),
        "comment": comment
    }).eq("id", assignment_id).execute()

def reopen_assignment(assignment_id: str):
    sb = get_supabase()
    sb.table("assignments").update({
        "status": "open", "completed_at": None
    }).eq("id", assignment_id).execute()

def get_fairness_data(wg_id: str) -> pd.DataFrame:
    rows = get_assignments(wg_id)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────
# UI SETUP
# ──────────────────────────────────────────────────

def setup_page():
    st.set_page_config(
        page_title="🏠 WG Task System",
        page_icon="🏠",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;600;700&family=DM+Mono:wght@400;500&display=swap');
        html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
        .stApp { background: #0f0f13; color: #e8e8f0; }
        .stSidebar { background: #16161d !important; border-right: 1px solid #2a2a3a; }
        .wg-card {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid #2a2a4a; border-radius: 16px;
            padding: 20px 24px; margin: 10px 0; transition: border-color 0.2s;
        }
        .wg-card:hover { border-color: #5a5af0; }
        .task-open {
            background: #1e2a1e; border-left: 4px solid #4caf50;
            border-radius: 8px; padding: 14px 18px; margin: 8px 0; font-size: 15px;
        }
        .task-done {
            background: #1a1a1a; border-left: 4px solid #555;
            border-radius: 8px; padding: 14px 18px; margin: 8px 0;
            font-size: 15px; opacity: 0.6;
        }
        .task-late {
            background: #2a1a1a; border-left: 4px solid #ef5350;
            border-radius: 8px; padding: 14px 18px; margin: 8px 0; font-size: 15px;
        }
        h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 700 !important; }
        .big-title {
            font-size: 2.4rem; font-weight: 700;
            background: linear-gradient(135deg, #a78bfa, #60a5fa);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            margin-bottom: 4px;
        }
        .subtitle { color: #888; font-size: 0.95rem; margin-bottom: 28px; }
        .metric-box {
            background: #1a1a2e; border: 1px solid #2a2a4a;
            border-radius: 12px; padding: 18px 20px; text-align: center;
        }
        .metric-val { font-size: 2rem; font-weight: 700; color: #a78bfa; font-family: 'DM Mono', monospace; }
        .metric-label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }
        .invite-code {
            background: #12122a; border: 2px dashed #5a5af0; border-radius: 10px;
            padding: 12px 20px; font-family: 'DM Mono', monospace;
            font-size: 1.3rem; color: #a78bfa; text-align: center; letter-spacing: 2px;
        }
        .stButton > button {
            background: linear-gradient(135deg, #7c3aed, #4f46e5);
            color: white; border: none; border-radius: 8px;
            font-family: 'Space Grotesk', sans-serif; font-weight: 600; transition: opacity 0.2s;
        }
        .stButton > button:hover { opacity: 0.85; }
        .stTextInput > div > div > input,
        .stSelectbox > div > div,
        .stTextArea > div > div > textarea {
            background: #1a1a2e !important; border: 1px solid #2a2a4a !important;
            color: #e8e8f0 !important; border-radius: 8px !important;
        }
        .stTabs [data-baseweb="tab-list"] {
            background: #16161d; border-radius: 10px; padding: 4px; gap: 4px;
        }
        .stTabs [data-baseweb="tab"] { border-radius: 8px; color: #888; font-weight: 600; }
        .stTabs [aria-selected="true"] { background: #2a2a4a !important; color: #a78bfa !important; }
        hr { border-color: #2a2a3a; }
        .role-badge {
            display: inline-block; padding: 2px 10px; border-radius: 20px;
            font-size: 0.75rem; font-weight: 600; margin-left: 6px;
        }
        .locked-banner {
            background: #2a1a1a; border: 1px solid #ef5350; border-radius: 8px;
            padding: 10px 16px; color: #ef5350; font-weight: 600; margin: 8px 0;
        }
    </style>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────
# AUTH SCREEN
# ──────────────────────────────────────────────────

def render_auth():
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown('<div class="big-title">🏠 WG System</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Smart Task & Fairness Tracker für deine WG</div>', unsafe_allow_html=True)

        tab_login, tab_register = st.tabs(["🔐 Login", "✨ Registrieren"])

        with tab_login:
            st.markdown("")
            email = st.text_input("E-Mail", key="li_email", placeholder="max@example.com")
            pw = st.text_input("Passwort", type="password", key="li_pw", placeholder="••••••••")
            if st.button("Einloggen", use_container_width=True, key="btn_login"):
                if not email or not pw:
                    st.error("Bitte alle Felder ausfüllen.")
                else:
                    user = login_user(email, pw)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("❌ Ungültige Zugangsdaten.")

        with tab_register:
            st.markdown("")
            name = st.text_input("Name", key="reg_name", placeholder="Max Mustermann")
            email2 = st.text_input("E-Mail", key="reg_email", placeholder="max@example.com")
            pw2 = st.text_input("Passwort", type="password", key="reg_pw", placeholder="min. 6 Zeichen")
            pw3 = st.text_input("Passwort wiederholen", type="password", key="reg_pw2", placeholder="••••••••")
            if st.button("Account erstellen", use_container_width=True, key="btn_reg"):
                if not all([name, email2, pw2, pw3]):
                    st.error("Bitte alle Felder ausfüllen.")
                elif pw2 != pw3:
                    st.error("Passwörter stimmen nicht überein.")
                elif len(pw2) < 6:
                    st.error("Passwort zu kurz (min. 6 Zeichen).")
                else:
                    ok, result = register_user(name, email2, pw2)
                    if ok:
                        user = login_user(email2, pw2)
                        st.session_state.user = user
                        st.success("✅ Account erstellt!")
                        st.rerun()
                    else:
                        st.error(f"❌ {result}")


# ──────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────

def render_sidebar(user: dict, wgs: list) -> Optional[str]:
    with st.sidebar:
        st.markdown(f"### 👤 {user['name']}")
        st.markdown(f"<small style='color:#888'>{user['email']}</small>", unsafe_allow_html=True)
        st.markdown("---")

        selected_wg_id = None
        if wgs:
            st.markdown("**Meine WGs**")
            wg_names = {w["id"]: w["name"] for w in wgs}
            if "active_wg" not in st.session_state or st.session_state.active_wg not in wg_names:
                st.session_state.active_wg = wgs[0]["id"]

            for wg in wgs:
                active = wg["id"] == st.session_state.active_wg
                locked_icon = "🔒" if wg.get("locked") else ""
                if st.button(
                    f"{'▶ ' if active else '   '}{wg['name']} {locked_icon}",
                    key=f"wg_btn_{wg['id']}", use_container_width=True
                ):
                    st.session_state.active_wg = wg["id"]
                    st.rerun()
            selected_wg_id = st.session_state.active_wg
        else:
            st.info("Du bist noch in keiner WG.")

        st.markdown("---")
        with st.expander("➕ WG erstellen"):
            wg_name = st.text_input("WG-Name", key="new_wg_name", placeholder="Musterstraße 7")
            if st.button("Erstellen", key="create_wg_btn"):
                if wg_name.strip():
                    result = create_wg(wg_name.strip(), user["id"])
                    st.session_state.active_wg = result["id"]
                    st.success("✅ WG erstellt! Du bist automatisch Moderator.")
                    st.rerun()

        with st.expander("🔗 WG beitreten"):
            code = st.text_input("Einladungscode", key="join_code", placeholder="abc12345")
            if st.button("Beitreten", key="join_wg_btn"):
                if code.strip():
                    ok, msg = join_wg(code.strip(), user["id"])
                    if ok:
                        st.success(f"✅ Beigetreten: {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

        st.markdown("---")
        if st.button("🚪 Ausloggen", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    return selected_wg_id


# ──────────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────────

def render_dashboard(wg: dict, user: dict, my_role: str):
    members = get_wg_members(wg["id"])
    tasks = get_tasks(wg["id"])
    today = today_str()

    run_rotation(wg["id"])

    ws = week_start()
    we = ws + timedelta(days=6)
    week_assignments = get_assignments(wg["id"], ws.isoformat(), we.isoformat())
    today_assignments = get_assignments(wg["id"], to_date=today, user_id=user["id"])
    my_open = [a for a in today_assignments if a["status"] == "open"]
    overdue = [a for a in my_open if a["due_date"] < today]
    due_today = [a for a in my_open if a["due_date"] == today]

    role_info = ROLES.get(my_role, ROLES["member"])
    st.markdown(
        f'<div class="big-title">🏠 {wg["name"]}</div>'
        f'<span class="role-badge" style="background:{role_info["color"]}22;color:{role_info["color"]};'
        f'border:1px solid {role_info["color"]}44">{role_info["label"]}</span>',
        unsafe_allow_html=True
    )
    st.markdown(f'<div class="subtitle">{len(members)} Mitglieder · {len(tasks)} Aufgaben aktiv</div>', unsafe_allow_html=True)

    if wg.get("locked"):
        st.markdown('<div class="locked-banner">🔒 Diese WG ist für neue Mitglieder gesperrt.</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    done_week = sum(1 for a in week_assignments if a["status"] == "done")
    total_week = len(week_assignments)
    pct = int(done_week / total_week * 100) if total_week > 0 else 0
    with c1:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{len(due_today)}</div><div class="metric-label">Heute fällig</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{len(overdue)}</div><div class="metric-label">Überfällig</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{pct}%</div><div class="metric-label">Woche erledigt</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div class="metric-box"><div class="metric-val">{len(members)}</div><div class="metric-label">Mitglieder</div></div>', unsafe_allow_html=True)

    st.markdown("")
    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.markdown("### 📋 Meine Aufgaben")
        if overdue:
            for a in overdue:
                delta = (date.today() - date.fromisoformat(a["due_date"])).days
                st.markdown(f"""
                <div class="task-late">
                    ⚠️ <b>{a['title']}</b> &nbsp;<span style='color:#ef5350'>{a['category']}</span>
                    <br><small style='color:#888'>Fällig war: {a['due_date']} · vor {delta} Tag{'en' if delta > 1 else ''}</small>
                </div>""", unsafe_allow_html=True)
                if st.button("✅ Erledigt", key=f"done_ov_{a['id']}"):
                    complete_assignment(a["id"])
                    st.rerun()

        if due_today:
            for a in due_today:
                st.markdown(f"""
                <div class="task-open">
                    📌 <b>{a['title']}</b> &nbsp;<span style='color:#4caf50'>{a['category']}</span>
                    <br><small style='color:#888'>Heute fällig · ~{a['effort_minutes']} Min</small>
                </div>""", unsafe_allow_html=True)
                if st.button("✅ Erledigt", key=f"done_td_{a['id']}"):
                    complete_assignment(a["id"])
                    st.rerun()

        if not overdue and not due_today:
            st.success("🎉 Keine offenen Aufgaben für dich! Gut gemacht.")

    with col_right:
        st.markdown("### 📅 Diese Woche")
        if week_assignments:
            for a in week_assignments[:8]:
                status_icon = "✅" if a["status"] == "done" else ("⚠️" if a["due_date"] < today else "⏳")
                cls = "task-done" if a["status"] == "done" else ("task-late" if a["due_date"] < today and a["status"] == "open" else "task-open")
                st.markdown(f"""
                <div class="{cls}" style="font-size:13px;">
                    {status_icon} <b>{a['title']}</b><br>
                    <small style='color:#888'>{a['person_name']} · {a['due_date']}</small>
                </div>""", unsafe_allow_html=True)
        else:
            st.info("Noch keine Aufgaben für diese Woche.")

    st.markdown("---")
    wg_data = next((w for w in get_user_wgs(user["id"]) if w["id"] == wg["id"]), None)
    if wg_data and not wg.get("locked"):
        st.markdown("### 🔗 Freunde einladen")
        st.markdown(f'<div class="invite-code">{wg_data["invite_code"]}</div>', unsafe_allow_html=True)
        st.caption("Teile diesen Code mit deinen Mitbewohnern")


# ──────────────────────────────────────────────────
# AUFGABEN (mit Bearbeiten)
# ──────────────────────────────────────────────────

def render_tasks(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">⚙️ Aufgaben</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Aufgaben erstellen, bearbeiten und Rotation steuern</div>', unsafe_allow_html=True)

    tabs = ["📋 Aufgabenliste"]
    if can(my_role, "create_task"):
        tabs.append("➕ Neue Aufgabe")
    if can(my_role, "run_rotation"):
        tabs.append("🔄 Rotation")

    tab_objs = st.tabs(tabs)
    tasks = get_tasks(wg["id"])

    # ── TAB: Liste ──
    with tab_objs[0]:
        if not tasks:
            st.info("Noch keine Aufgaben. Erstelle welche im Tab 'Neue Aufgabe'.")
        else:
            by_cat = {}
            for t in tasks:
                by_cat.setdefault(t["category"], []).append(t)

            for cat, cat_tasks in by_cat.items():
                st.markdown(f"**{cat}**")
                for t in cat_tasks:
                    col1, col2, col3, col4 = st.columns([3, 1.5, 0.5, 0.5])
                    with col1:
                        st.markdown(f"**{t['title']}**  \n<small style='color:#888'>{t['description'] or 'Keine Beschreibung'}</small>", unsafe_allow_html=True)
                    with col2:
                        freq_label = {v: k for k, v in RECURRENCES.items()}.get(t["recurrence"], t["recurrence"])
                        st.caption(f"🔄 {freq_label} · ⏱ {t['effort_minutes']} Min")
                    with col3:
                        if can(my_role, "edit_task"):
                            if st.button("✏️", key=f"edit_{t['id']}", help="Bearbeiten"):
                                st.session_state[f"editing_{t['id']}"] = True
                                st.rerun()
                    with col4:
                        if can(my_role, "delete_task"):
                            if st.button("🗑️", key=f"del_{t['id']}", help="Löschen"):
                                delete_task(t["id"])
                                st.rerun()

                    # Inline-Bearbeitungsformular
                    if st.session_state.get(f"editing_{t['id']}"):
                        with st.expander(f"✏️ '{t['title']}' bearbeiten", expanded=True):
                            e_title = st.text_input("Titel", value=t["title"], key=f"e_title_{t['id']}")
                            e_desc = st.text_area("Beschreibung", value=t["description"] or "", key=f"e_desc_{t['id']}", height=70)
                            ec1, ec2, ec3 = st.columns(3)
                            with ec1:
                                e_cat = st.selectbox("Kategorie", CATEGORIES,
                                    index=CATEGORIES.index(t["category"]) if t["category"] in CATEGORIES else 0,
                                    key=f"e_cat_{t['id']}")
                            with ec2:
                                rec_labels = list(RECURRENCES.keys())
                                rec_vals = list(RECURRENCES.values())
                                cur_rec_idx = rec_vals.index(t["recurrence"]) if t["recurrence"] in rec_vals else 1
                                e_rec_label = st.selectbox("Wiederholung", rec_labels, index=cur_rec_idx, key=f"e_rec_{t['id']}")
                                e_rec = RECURRENCES[e_rec_label]
                            with ec3:
                                e_effort = st.slider("Aufwand (Min)", 5, 120, t["effort_minutes"], 5, key=f"e_eff_{t['id']}")
                            bc1, bc2 = st.columns(2)
                            with bc1:
                                if st.button("💾 Speichern", key=f"save_{t['id']}", use_container_width=True):
                                    if e_title.strip():
                                        update_task(t["id"], e_title.strip(), e_desc.strip(), e_cat, e_rec, e_effort)
                                        del st.session_state[f"editing_{t['id']}"]
                                        st.success("✅ Gespeichert!")
                                        st.rerun()
                                    else:
                                        st.error("Titel darf nicht leer sein.")
                            with bc2:
                                if st.button("❌ Abbrechen", key=f"cancel_{t['id']}", use_container_width=True):
                                    del st.session_state[f"editing_{t['id']}"]
                                    st.rerun()
                st.markdown("")

    # ── TAB: Neue Aufgabe ──
    if can(my_role, "create_task") and "➕ Neue Aufgabe" in tabs:
        idx = tabs.index("➕ Neue Aufgabe")
        with tab_objs[idx]:
            st.markdown("#### Neue Aufgabe erstellen")
            title = st.text_input("Titel*", placeholder="z.B. Küche putzen", key="task_title")
            desc = st.text_area("Beschreibung", placeholder="Details...", key="task_desc", height=80)
            col1, col2, col3 = st.columns(3)
            with col1:
                cat = st.selectbox("Kategorie", CATEGORIES, key="task_cat")
            with col2:
                rec_label = st.selectbox("Wiederholung", list(RECURRENCES.keys()), key="task_rec")
                rec = RECURRENCES[rec_label]
            with col3:
                effort = st.slider("Aufwand (Min)", 5, 120, 30, 5, key="task_effort")
            if st.button("✅ Aufgabe erstellen", use_container_width=True, key="btn_create_task"):
                if not title.strip():
                    st.error("Titel ist Pflichtfeld.")
                else:
                    create_task(wg["id"], title.strip(), desc.strip(), cat, rec, effort)
                    run_rotation(wg["id"])
                    st.success(f"✅ Aufgabe '{title}' erstellt und Rotation gestartet!")
                    st.rerun()

    # ── TAB: Rotation ──
    if can(my_role, "run_rotation") and "🔄 Rotation" in tabs:
        idx = tabs.index("🔄 Rotation")
        with tab_objs[idx]:
            st.markdown("#### 🔄 Rotations-Engine")
            st.markdown("""
            Die Rotation verteilt Aufgaben **automatisch und fair**:
            - Tägliche, wöchentliche und monatliche Aufgaben werden korrekt berechnet
            - Niemand bekommt dieselbe Aufgabe zweimal hintereinander
            - Gleicht Workload über Zeit aus
            """)
            if st.button("🔄 Rotation jetzt ausführen", use_container_width=True):
                n = run_rotation(wg["id"])
                st.success(f"✅ {n} neue Assignments erstellt!")

            members = get_wg_members(wg["id"])
            if tasks and members:
                st.markdown("---")
                st.markdown("**Nächste Periode (Vorschau)**")
                ws = week_start(1)
                we = ws + timedelta(days=6)
                upcoming = get_assignments(wg["id"], ws.isoformat(), we.isoformat())
                if upcoming:
                    df_preview = pd.DataFrame([{
                        "Person": a["person_name"], "Aufgabe": a["title"],
                        "Kategorie": a["category"], "Fällig am": a["due_date"]
                    } for a in upcoming])
                    st.dataframe(df_preview, use_container_width=True, hide_index=True)
                else:
                    st.info("Noch keine Assignments für die nächste Periode.")


# ──────────────────────────────────────────────────
# KALENDER
# ──────────────────────────────────────────────────

def render_calendar(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">📅 Kalender</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Wer macht was wann</div>', unsafe_allow_html=True)

    if "cal_week_offset" not in st.session_state:
        st.session_state.cal_week_offset = 0

    col1, col2, col3 = st.columns([1, 3, 1])
    with col1:
        if st.button("◀ Vorherige"):
            st.session_state.cal_week_offset -= 1
    with col3:
        if st.button("Nächste ▶"):
            st.session_state.cal_week_offset += 1
    with col2:
        ws = week_start(st.session_state.cal_week_offset)
        we = ws + timedelta(days=6)
        st.markdown(f"<h4 style='text-align:center;color:#a78bfa'>KW {ws.isocalendar()[1]} · {ws.strftime('%d.%m')} – {we.strftime('%d.%m.%Y')}</h4>", unsafe_allow_html=True)

    if st.button("⬤ Heute", key="cal_today"):
        st.session_state.cal_week_offset = 0
        st.rerun()

    assignments = get_assignments(wg["id"], ws.isoformat(), we.isoformat())

    if not assignments:
        st.info("Keine Aufgaben in dieser Woche.")
        return

    days = [(ws + timedelta(days=i)) for i in range(7)]
    day_names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    cols = st.columns(7)

    for col, day, dname in zip(cols, days, day_names):
        day_str = day.isoformat()
        day_assignments = [a for a in assignments if a["due_date"] == day_str]
        is_today = day_str == today_str()
        with col:
            header_color = "#a78bfa" if is_today else "#888"
            bg = "#1a1a2e" if is_today else "#16161d"
            st.markdown(f"""
            <div style='background:{bg};border-radius:10px;padding:10px;min-height:120px;
                        border:{"2px solid #a78bfa" if is_today else "1px solid #2a2a3a"}'>
                <div style='color:{header_color};font-weight:700;margin-bottom:8px'>
                    {dname}<br><small>{day.strftime('%d.%m')}</small>
                </div>
            """, unsafe_allow_html=True)
            for a in day_assignments:
                color = "#4caf50" if a["status"] == "done" else ("#ef5350" if a["due_date"] < today_str() and a["status"] == "open" else "#60a5fa")
                st.markdown(f"""
                <div style='background:#0f0f1a;border-left:3px solid {color};border-radius:4px;
                            padding:6px 8px;margin:4px 0;font-size:12px;'>
                    <b>{a['title'][:16]}{'…' if len(a['title'])>16 else ''}</b><br>
                    <span style='color:#888'>{a['person_name'].split()[0]}</span>
                </div>
                """, unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### Details")
    today_s = today_str()
    for a in assignments:
        status_icon = "✅" if a["status"] == "done" else ("⚠️" if a["due_date"] < today_s and a["status"] == "open" else "⏳")
        col1, col2, col3, col4 = st.columns([2, 1.5, 1, 0.8])
        with col1:
            st.markdown(f"**{a['title']}**")
        with col2:
            st.markdown(f"👤 {a['person_name']}")
        with col3:
            st.caption(f"{a['due_date']}  {status_icon}")
        with col4:
            is_mine = a["assigned_to"] == user["id"]
            can_complete = (is_mine and can(my_role, "complete_own")) or can(my_role, "complete_all")
            if a["status"] == "open" and can_complete:
                if st.button("✅", key=f"cal_done_{a['id']}", help="Als erledigt markieren"):
                    complete_assignment(a["id"])
                    st.rerun()
            elif a["status"] == "done":
                st.caption("✅ done")


# ──────────────────────────────────────────────────
# FAIRNESS
# ──────────────────────────────────────────────────

def render_fairness(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">📊 Fairness</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Wer trägt wie viel bei</div>', unsafe_allow_html=True)

    df = get_fairness_data(wg["id"])
    if df.empty:
        st.info("Noch keine abgeschlossenen Aufgaben zum Anzeigen.")
        return

    members = get_wg_members(wg["id"])
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Erledigte Aufgaben pro Person")
        done_df = df[df["status"] == "done"].copy()
        if not done_df.empty:
            count_df = done_df.groupby("person_name").size().reset_index(name="Anzahl")
            fig = px.bar(count_df, x="person_name", y="Anzahl",
                color="Anzahl", color_continuous_scale=["#4f46e5", "#a78bfa", "#60a5fa"],
                template="plotly_dark")
            fig.update_layout(paper_bgcolor="#0f0f13", plot_bgcolor="#0f0f13",
                font=dict(family="Space Grotesk", color="#e8e8f0"),
                showlegend=False, coloraxis_showscale=False,
                margin=dict(l=10, r=10, t=10, b=10))
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Noch keine erledigten Aufgaben.")

    with col2:
        st.markdown("#### Aufwand-Verteilung (Minuten)")
        if not done_df.empty:
            effort_df = done_df.groupby("person_name")["effort_minutes"].sum().reset_index()
            effort_df.columns = ["Person", "Minuten"]
            fig2 = px.pie(effort_df, values="Minuten", names="Person",
                color_discrete_sequence=["#7c3aed", "#4f46e5", "#60a5fa", "#34d399", "#f59e0b"],
                template="plotly_dark", hole=0.4)
            fig2.update_layout(paper_bgcolor="#0f0f13",
                font=dict(family="Space Grotesk", color="#e8e8f0"),
                margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### 🏆 Fairness Score")
    done_counts = df[df["status"] == "done"].groupby("person_name").size().to_dict()
    total_done = sum(done_counts.values())
    ideal = total_done / len(members) if members else 1
    cols = st.columns(max(len(members), 1))
    for i, m in enumerate(members):
        n_done = done_counts.get(m["name"], 0)
        score = min(100, int((1 - abs(n_done - ideal) / max(ideal, 1)) * 100))
        color = "#4caf50" if score >= 75 else ("#ff9800" if score >= 50 else "#ef5350")
        with cols[i]:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val" style="color:{color}">{score}</div>
                <div class="metric-label">{m['name']}</div>
                <div style="color:#888;font-size:0.8rem;margin-top:4px">{n_done} erledigt</div>
            </div>""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────
# MITGLIEDER & MODERATOR-TOOLS
# ──────────────────────────────────────────────────

def render_members(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">👥 Mitglieder</div>', unsafe_allow_html=True)

    members = get_wg_members(wg["id"])
    workload = get_workload_counts(wg["id"])
    is_moderator = can(my_role, "manage_roles")

    for m in members:
        is_me = m["id"] == user["id"]
        done = workload.get(m["id"], 0)
        role_info = ROLES.get(m.get("role", "member"), ROLES["member"])

        col1, col2, col3, col4 = st.columns([2.5, 1, 1, 1.2])
        with col1:
            st.markdown(f"""
            <div class="wg-card" style="padding:12px 16px;margin:4px 0;">
                <b>{m['name']}</b>
                {"<span style='color:#a78bfa'> (du)</span>" if is_me else ""}
                <span class="role-badge" style="background:{role_info['color']}22;color:{role_info['color']};border:1px solid {role_info['color']}44">{role_info['label']}</span>
                <br><small style='color:#888'>{m['email']}</small>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<br><b>{done}</b> erledigt", unsafe_allow_html=True)
        with col3:
            joined = m["joined_at"][:10] if m.get("joined_at") else "–"
            st.markdown(f"<br><small style='color:#888'>seit {joined}</small>", unsafe_allow_html=True)
        with col4:
            if is_moderator and not is_me:
                # Rolle ändern
                role_options = list(ROLES.keys())
                cur_idx = role_options.index(m.get("role", "member")) if m.get("role") in role_options else 2
                new_role = st.selectbox("Rolle", role_options,
                    format_func=lambda r: ROLES[r]["label"],
                    index=cur_idx, key=f"role_sel_{m['id']}", label_visibility="collapsed")
                if new_role != m.get("role"):
                    if st.button("✔", key=f"role_save_{m['id']}", help="Rolle speichern"):
                        set_member_role(wg["id"], m["id"], new_role)
                        st.success(f"Rolle von {m['name']} geändert.")
                        st.rerun()

    # Moderator-Aktionen
    if is_moderator:
        st.markdown("---")
        st.markdown("### 👑 Moderator-Einstellungen")

        col_lock, col_kick = st.columns(2)

        with col_lock:
            st.markdown("#### 🔒 Beitritt sperren")
            locked = wg.get("locked", False)
            if locked:
                st.warning("🔒 Die WG ist aktuell gesperrt. Niemand kann beitreten.")
                if st.button("🔓 WG entsperren", use_container_width=True):
                    set_wg_locked(wg["id"], False)
                    st.success("WG ist wieder offen für neue Mitglieder.")
                    st.rerun()
            else:
                st.info("✅ Die WG ist offen für neue Mitglieder.")
                if st.button("🔒 WG sperren", use_container_width=True):
                    set_wg_locked(wg["id"], True)
                    st.warning("WG wurde gesperrt.")
                    st.rerun()

        with col_kick:
            st.markdown("#### 🚫 Mitglied entfernen")
            kickable = [m for m in members if m["id"] != user["id"]]
            if kickable:
                kick_options = {m["id"]: m["name"] for m in kickable}
                kick_id = st.selectbox("Mitglied auswählen",
                    options=list(kick_options.keys()),
                    format_func=lambda x: kick_options[x],
                    key="kick_select")
                if st.button("🚫 Mitglied entfernen", use_container_width=True):
                    kick_member(wg["id"], kick_id)
                    st.warning(f"✅ {kick_options[kick_id]} wurde entfernt.")
                    st.rerun()
            else:
                st.info("Keine weiteren Mitglieder zum Entfernen.")

    # Einladungscode (nur wenn nicht gesperrt oder Moderator)
    if not wg.get("locked") or is_moderator:
        wg_data = next((w for w in get_user_wgs(user["id"]) if w["id"] == wg["id"]), None)
        if wg_data:
            st.markdown("---")
            st.markdown("### 🔗 Mitglieder einladen")
            if wg.get("locked"):
                st.caption("⚠️ WG ist gesperrt – nur du als Moderator kannst den Code teilen.")
            st.markdown(f'<div class="invite-code">{wg_data["invite_code"]}</div>', unsafe_allow_html=True)
            st.code(f"Code: {wg_data['invite_code']}", language=None)


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    setup_page()

    if "user" not in st.session_state:
        render_auth()
        return

    user = st.session_state.user
    wgs = get_user_wgs(user["id"])
    selected_wg_id = render_sidebar(user, wgs)

    if not wgs:
        st.markdown('<div class="big-title">👋 Willkommen!</div>', unsafe_allow_html=True)
        st.markdown("Erstelle deine erste WG oder tritt einer bestehenden bei — über die **Sidebar links**.")
        st.balloons()
        return

    active_wg = next((w for w in wgs if w["id"] == selected_wg_id), wgs[0])
    my_role = get_member_role(active_wg["id"], user["id"])

    pages = {
        "🏠 Dashboard": render_dashboard,
        "⚙️ Aufgaben":  render_tasks,
        "📅 Kalender":  render_calendar,
        "📊 Fairness":  render_fairness,
        "👥 Mitglieder": render_members,
    }

    st.markdown("")
    page_name = st.radio(
        "Navigation", list(pages.keys()),
        horizontal=True, label_visibility="collapsed", key="main_nav"
    )
    st.markdown("---")
    pages[page_name](active_wg, user, my_role)


if __name__ == "__main__":
    main()
