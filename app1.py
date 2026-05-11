import streamlit as st
import hashlib
import uuid
import html as html_lib
import smtplib
import pandas as pd
import plotly.express as px
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
from typing import Optional
import os
import secrets
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ── Supabase client ────────────────────────────────

@st.cache_resource
def get_supabase() -> Client:
    url, key = "", ""
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
    except Exception:
        pass
    url = url or os.environ.get("SUPABASE_URL", "")
    key = key or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        st.error("❌ Supabase credentials missing!")
        st.info('Add to Streamlit Cloud Secrets:\n```\nSUPABASE_URL = "https://xxxx.supabase.co"\nSUPABASE_KEY = "eyJ..."\n```')
        st.stop()
    return create_client(url, key)


# ── Constants ──────────────────────────────────────

CATEGORIES = ["🧹 Cleaning", "🛒 Shopping", "🗑️ Trash", "🍳 Cooking",
              "🧺 Laundry", "🌿 Plants", "🔧 Repairs", "📦 Other"]
RECURRENCES = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
RECURRENCE_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
RECURRENCE_PERIODS = {"daily": 30, "weekly": 8, "monthly": 3}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# preferred_day: weekly → 0–6 (weekday Mon=0), monthly → 1–31 (day of month), daily → None

ROLES = {
    "moderator": {"label": "👑 Moderator", "color": "#f59e0b"},
    "editor":    {"label": "✏️ Editor",    "color": "#60a5fa"},
    "member":    {"label": "👤 Member",    "color": "#888"},
}

PERMISSIONS = {
    "moderator": {"create_task", "edit_task", "delete_task", "run_rotation",
                  "kick_member", "lock_wg", "manage_roles", "complete_own", "complete_all"},
    "editor":    {"create_task", "edit_task", "run_rotation", "complete_own", "complete_all"},
    "member":    {"complete_own"},
}

def can(role: str, action: str) -> bool:
    return action in PERMISSIONS.get(role, set())


# ── Helpers ────────────────────────────────────────

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def new_id() -> str:
    return str(uuid.uuid4())

def today_str() -> str:
    return date.today().isoformat()

def week_start(offset: int = 0) -> date:
    d = date.today()
    return d - timedelta(days=d.weekday()) + timedelta(weeks=offset)

def e(text) -> str:
    """Escape user content before embedding in HTML."""
    return html_lib.escape(str(text))


# ── Email verification ─────────────────────────────

def get_email_config() -> dict:
    cfg = {}
    try:
        cfg["host"]     = st.secrets.get("EMAIL_HOST", "")
        cfg["port"]     = int(st.secrets.get("EMAIL_PORT", 587))
        cfg["user"]     = st.secrets.get("EMAIL_USER", "")
        cfg["password"] = st.secrets.get("EMAIL_PASSWORD", "")
        cfg["from"]     = st.secrets.get("EMAIL_FROM", cfg.get("user", ""))
        cfg["app_url"]  = st.secrets.get("APP_URL", "")
    except Exception:
        pass
    cfg["host"]     = cfg.get("host") or os.environ.get("EMAIL_HOST", "")
    cfg["port"]     = cfg.get("port") or int(os.environ.get("EMAIL_PORT", 587))
    cfg["user"]     = cfg.get("user") or os.environ.get("EMAIL_USER", "")
    cfg["password"] = cfg.get("password") or os.environ.get("EMAIL_PASSWORD", "")
    cfg["from"]     = cfg.get("from") or os.environ.get("EMAIL_FROM", cfg.get("user", ""))
    cfg["app_url"]  = cfg.get("app_url") or os.environ.get("APP_URL", "")
    return cfg

def send_verification_email(to_email: str, name: str, token: str) -> bool:
    cfg = get_email_config()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return False
    app_url    = cfg["app_url"].rstrip("/") if cfg["app_url"] else ""
    verify_url = f"{app_url}?verify={token}" if app_url else f"Token: {token}"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Verify your WG System account"
        msg["From"]    = cfg["from"]
        msg["To"]      = to_email
        body_text = f"Hi {name},\n\nVerify your account:\n{verify_url}\n\nIf you didn't sign up, ignore this email."
        body_html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0f0f13;color:#e8e8f0;border-radius:12px">
            <h2 style="color:#a78bfa">🏠 WG System</h2>
            <p>Hi <b>{e(name)}</b>, welcome!</p>
            <p>Click the button below to verify your email address:</p>
            <a href="{verify_url}" style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#7c3aed,#4f46e5);color:white;border-radius:8px;text-decoration:none;font-weight:600">Verify Email</a>
            <p style="color:#888;margin-top:24px;font-size:13px">If the button doesn't work, copy this link:<br>{verify_url}</p>
        </div>"""
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], to_email, msg.as_string())
        return True
    except Exception:
        return False


# ── User auth ──────────────────────────────────────

def register_user(name: str, email: str, password: str) -> tuple[bool, str]:
    sb = get_supabase()
    token = secrets.token_urlsafe(32)
    email_cfg = get_email_config()
    needs_verification = bool(email_cfg.get("host") and email_cfg.get("user"))
    try:
        result = sb.table("users").insert({
            "name": name,
            "email": email.lower(),
            "password": hash_pw(password),
            "verified": not needs_verification,
            "verification_token": token if needs_verification else None,
        }).execute()
        if result.data:
            user_id = result.data[0].get("id", "")
            if needs_verification:
                sent = send_verification_email(email, name, token)
                if not sent:
                    sb.table("users").update({"verified": True, "verification_token": None}) \
                        .eq("id", user_id).execute()
                    return True, "ok"
                return True, "verify"
            return True, "ok"
        return False, "Registration failed."
    except Exception as ex:
        msg = str(ex)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            return False, "Email already registered."
        return False, msg

def verify_email_token(token: str) -> tuple[bool, str]:
    sb = get_supabase()
    res = sb.table("users").select("id, name").eq("verification_token", token).execute()
    if not res.data:
        return False, "Invalid or expired verification link."
    uid = res.data[0]["id"]
    sb.table("users").update({"verified": True, "verification_token": None}).eq("id", uid).execute()
    return True, res.data[0]["name"]

def login_user(email: str, password: str) -> tuple[Optional[dict], str]:
    sb = get_supabase()
    result = sb.table("users").select("*").eq("email", email.lower()).eq("password", hash_pw(password)).execute()
    if not result.data:
        return None, "Invalid email or password."
    user = result.data[0]
    if not user.get("verified", True):
        return None, "please_verify"
    return user, ""

def resend_verification(email: str) -> bool:
    sb = get_supabase()
    res = sb.table("users").select("*").eq("email", email.lower()).execute()
    if not res.data:
        return False
    user = res.data[0]
    if user.get("verified"):
        return False
    token = user.get("verification_token") or secrets.token_urlsafe(32)
    sb.table("users").update({"verification_token": token}).eq("id", user["id"]).execute()
    return send_verification_email(email, user["name"], token)


# ── WG management ──────────────────────────────────

def create_wg(name: str, user_id: str) -> dict:
    sb = get_supabase()
    wg_id, invite = new_id(), new_id()
    sb.table("wgs").insert({
        "id": wg_id, "name": name,
        "invite_code": invite, "created_by": user_id, "locked": False
    }).execute()
    sb.table("wg_members").insert({
        "wg_id": wg_id, "user_id": user_id, "role": "moderator"
    }).execute()
    return {"id": wg_id, "invite_code": invite}

def join_wg(invite_code: str, user_id: str) -> tuple[bool, str]:
    sb = get_supabase()
    wg_res = sb.table("wgs").select("*").eq("invite_code", invite_code).execute()
    if not wg_res.data:
        return False, "Invalid invite code."
    wg = wg_res.data[0]
    if wg.get("locked"):
        return False, "This flat is locked. New members cannot join."
    exists = sb.table("wg_members").select("wg_id").eq("wg_id", wg["id"]).eq("user_id", user_id).execute()
    if exists.data:
        return False, "You are already a member of this flat."
    sb.table("wg_members").insert({"wg_id": wg["id"], "user_id": user_id, "role": "member"}).execute()
    return True, wg["name"]

@st.cache_data(ttl=60)
def get_user_wgs(user_id: str) -> list[dict]:
    sb = get_supabase()
    mem = sb.table("wg_members").select("wg_id").eq("user_id", user_id).execute()
    wg_ids = [m["wg_id"] for m in mem.data]
    if not wg_ids:
        return []
    return sb.table("wgs").select("*").in_("id", wg_ids).execute().data or []

@st.cache_data(ttl=30)
def get_wg_members(wg_id: str) -> list[dict]:
    sb = get_supabase()
    mem = sb.table("wg_members").select("user_id, role, joined_at").eq("wg_id", wg_id).execute()
    if not mem.data:
        return []
    user_ids = [m["user_id"] for m in mem.data]
    users    = sb.table("users").select("id, name, email").in_("id", user_ids).execute()
    user_map = {u["id"]: u for u in (users.data or [])}
    result   = []
    for m in mem.data:
        u = user_map.get(m["user_id"], {})
        result.append({
            "id": m["user_id"], "name": u.get("name", "?"),
            "email": u.get("email", ""), "role": m.get("role", "member"),
            "joined_at": m.get("joined_at", ""),
        })
    return result

def get_member_role(wg_id: str, user_id: str) -> str:
    sb  = get_supabase()
    res = sb.table("wg_members").select("role").eq("wg_id", wg_id).eq("user_id", user_id).execute()
    return res.data[0].get("role", "member") if res.data else "member"

def set_member_role(wg_id: str, user_id: str, role: str):
    get_supabase().table("wg_members").update({"role": role}).eq("wg_id", wg_id).eq("user_id", user_id).execute()
    get_wg_members.clear()

def kick_member(wg_id: str, user_id: str):
    get_supabase().table("wg_members").delete().eq("wg_id", wg_id).eq("user_id", user_id).execute()
    get_wg_members.clear()

def set_wg_locked(wg_id: str, locked: bool):
    get_supabase().table("wgs").update({"locked": locked}).eq("id", wg_id).execute()
    get_user_wgs.clear()


# ── Task management ────────────────────────────────

@st.cache_data(ttl=30)
def get_tasks(wg_id: str) -> list[dict]:
    return get_supabase().table("tasks").select("*").eq("wg_id", wg_id).eq("active", True).order("category").execute().data or []

def create_task(wg_id: str, title: str, description: str, category: str,
                recurrence: str, effort: int, preferred_day: Optional[int] = None) -> str:
    tid = new_id()
    get_supabase().table("tasks").insert({
        "id": tid, "wg_id": wg_id, "title": title, "description": description,
        "category": category, "recurrence": recurrence, "effort_minutes": effort,
        "active": True, "preferred_day": preferred_day,
    }).execute()
    get_tasks.clear()
    return tid

def update_task(task_id: str, title: str, description: str, category: str,
                recurrence: str, effort: int, preferred_day: Optional[int] = None):
    get_supabase().table("tasks").update({
        "title": title, "description": description,
        "category": category, "recurrence": recurrence,
        "effort_minutes": effort, "preferred_day": preferred_day,
    }).eq("id", task_id).execute()
    get_tasks.clear()

def delete_task(task_id: str):
    get_supabase().table("tasks").update({"active": False}).eq("id", task_id).execute()
    get_tasks.clear()


# ── Rotation engine ────────────────────────────────

def run_rotation(wg_id: str) -> int:
    """
    Verteilt Aufgaben so, dass pro Woche eine maximale Gleichverteilung herrscht.
    Jeder Member bekommt erst eine zweite Aufgabe in einer Woche, wenn alle anderen 
    bereits eine haben. Zudem rotieren die Aufgabentypen.
    """
    sb = get_supabase()
    members, tasks = get_wg_members(wg_id), get_tasks(wg_id)
    if not members or not tasks:
        return 0

    member_ids = [m["id"] for m in members]
    n_members = len(member_ids)
    
    # 1. Alle fälligen Termine (Slots) sammeln
    all_slots = []
    today = date.today()

    for task in tasks:
        recurrence = task["recurrence"]
        days_ahead = RECURRENCE_DAYS.get(recurrence, 7)
        n_periods = RECURRENCE_PERIODS.get(recurrence, 4)
        preferred_day = task.get("preferred_day")

        # Letzten Termin suchen, um den Ankerpunkt zu finden
        last_res = sb.table("assignments") \
            .select("due_date") \
            .eq("task_id", task["id"]) \
            .order("due_date", desc=True).limit(1).execute()
        
        anchor = date.fromisoformat(last_res.data[0]["due_date"]) + timedelta(days=days_ahead) if last_res.data else today

        # Wochentag/Monatstag anpassen
        if preferred_day is not None:
            if recurrence == "weekly":
                diff = (preferred_day - anchor.weekday()) % 7
                anchor = anchor + timedelta(days=diff)
            elif recurrence == "monthly":
                import calendar
                day = min(preferred_day, calendar.monthrange(anchor.year, anchor.month)[1])
                anchor = anchor.replace(day=day)
                if anchor < today and not last_res.data:
                    if anchor.month == 12: anchor = anchor.replace(year=anchor.year + 1, month=1)
                    else: anchor = anchor.replace(month=anchor.month + 1, day=min(preferred_day, calendar.monthrange(anchor.year, anchor.month+1)[1]))

        for i in range(n_periods):
            if recurrence == "monthly" and preferred_day is not None:
                import calendar
                m_offset = anchor.month - 1 + i
                year, month = anchor.year + m_offset // 12, m_offset % 12 + 1
                due = date(year, month, min(preferred_day, calendar.monthrange(year, month)[1]))
            else:
                due = anchor + timedelta(days=days_ahead * i)

            if due < today: continue

            # Prüfen ob Slot existiert
            exists = sb.table("assignments").select("id").eq("task_id", task["id"]).eq("due_date", due.isoformat()).execute()
            if not exists.data:
                all_slots.append({"task": task, "due": due})

    if not all_slots:
        return 0

    # 2. Sortieren nach Datum (wichtig!)
    all_slots.sort(key=lambda x: x["due"])

    # 3. Workload-Tracking
    global_workload = get_workload_counts(wg_id) # Gesamtanzahl erledigter Tasks
    weekly_tracker = {}     # Key: (member_id, week_start_date), Value: Anzahl Tasks
    last_person_for_task = {} # Damit man nicht 2x hintereinander den gleichen Task macht

    created = 0
    for slot in all_slots:
        t_id = slot["task"]["id"]
        due_date = slot["due"]
        # Wochenstart berechnen (Montag dieser Woche)
        w_start = (due_date - timedelta(days=due_date.weekday())).isoformat()
        
        # Mitglieder sortieren nach:
        # 1. Wie viele Tasks hat die Person IN DIESER WOCHE schon? (Wichtigste Prio)
        # 2. Wie viel hat die Person INSGESAMT im Leben der WG getan? (Fairness)
        sorted_members = sorted(member_ids, key=lambda uid: (
            weekly_tracker.get((uid, w_start), 0),
            global_workload.get(uid, 0)
        ))

        # Person wählen (darf nicht dieselbe wie beim letzten Mal für diesen spezifischen Task sein)
        next_person = next(
            (m for m in sorted_members if n_members == 1 or m != last_person_for_task.get(t_id)),
            sorted_members[0]
        )

        # In DB speichern
        sb.table("assignments").insert({
            "id": new_id(), "task_id": t_id, "wg_id": wg_id,
            "assigned_to": next_person, "due_date": due_date.isoformat(),
            "status": "open", "comment": ""
        }).execute()

        # Tracker für diesen Durchlauf aktualisieren
        global_workload[next_person] = global_workload.get(next_person, 0) + 1
        weekly_tracker[(next_person, w_start)] = weekly_tracker.get((next_person, w_start), 0) + 1
        last_person_for_task[t_id] = next_person
        created += 1

    return created

@st.cache_data(ttl=60)
def get_workload_counts(wg_id: str) -> dict:
    rows = get_supabase().table("assignments").select("assigned_to").eq("wg_id", wg_id).eq("status", "done").execute().data or []
    counts: dict = {}
    for row in rows:
        uid = row["assigned_to"]
        counts[uid] = counts.get(uid, 0) + 1
    return counts

@st.cache_data(ttl=20)
def get_assignments(wg_id: str, from_date: str = None, to_date: str = None, user_id: str = None) -> list[dict]:
    sb = get_supabase()
    q  = sb.table("assignments").select("id, task_id, assigned_to, due_date, status, completed_at, comment, wg_id").eq("wg_id", wg_id)
    if from_date: q = q.gte("due_date", from_date)
    if to_date:   q = q.lte("due_date", to_date)
    if user_id:   q = q.eq("assigned_to", user_id)
    rows = q.order("due_date").execute().data or []

    task_ids = list({r["task_id"] for r in rows})
    user_ids = list({r["assigned_to"] for r in rows})
    tasks_map, users_map = {}, {}
    if task_ids:
        t_res     = sb.table("tasks").select("id, title, category, effort_minutes, active").in_("id", task_ids).execute()
        tasks_map = {t["id"]: t for t in (t_res.data or [])}
    if user_ids:
        u_res     = sb.table("users").select("id, name").in_("id", user_ids).execute()
        users_map = {u["id"]: u for u in (u_res.data or [])}

    result = []
    for r in rows:
        t = tasks_map.get(r["task_id"], {})
        if not t.get("active", True):
            continue
        u = users_map.get(r["assigned_to"], {})
        result.append({**r,
            "title": t.get("title", "?"), "category": t.get("category", ""),
            "effort_minutes": t.get("effort_minutes", 0), "person_name": u.get("name", "?")
        })
    return result

def complete_assignment(assignment_id: str, comment: str = ""):
    get_supabase().table("assignments").update({
        "status": "done", "completed_at": datetime.now().isoformat(), "comment": comment
    }).eq("id", assignment_id).execute()
    get_assignments.clear()
    get_workload_counts.clear()

def reopen_assignment(assignment_id: str):
    get_supabase().table("assignments").update({"status": "open", "completed_at": None}).eq("id", assignment_id).execute()
    get_assignments.clear()

def get_fairness_data(wg_id: str) -> pd.DataFrame:
    rows = get_assignments(wg_id)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Page setup ─────────────────────────────────────

def setup_page():
    st.set_page_config(page_title="🏠 WG Task System", page_icon="🏠", layout="wide", initial_sidebar_state="expanded")
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;600;700&family=DM+Mono:wght@400;500&display=swap');
        html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
        .stApp { background: #0f0f13; color: #e8e8f0; }
        .stSidebar { background: #16161d !important; border-right: 1px solid #2a2a3a; }
        .wg-card { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border: 1px solid #2a2a4a; border-radius: 16px; padding: 20px 24px; margin: 10px 0; transition: border-color 0.2s; }
        .wg-card:hover { border-color: #5a5af0; }
        .task-open  { background: #1e2a1e; border-left: 4px solid #4caf50; border-radius: 8px; padding: 14px 18px; margin: 8px 0; font-size: 15px; }
        .task-done  { background: #1a1a1a; border-left: 4px solid #555; border-radius: 8px; padding: 14px 18px; margin: 8px 0; font-size: 15px; opacity: 0.6; }
        .task-late  { background: #2a1a1a; border-left: 4px solid #ef5350; border-radius: 8px; padding: 14px 18px; margin: 8px 0; font-size: 15px; }
        h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 700 !important; }
        .big-title  { font-size: 2.4rem; font-weight: 700; background: linear-gradient(135deg, #a78bfa, #60a5fa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 4px; }
        .subtitle   { color: #888; font-size: 0.95rem; margin-bottom: 28px; }
        .metric-box { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 18px 20px; text-align: center; }
        .metric-val { font-size: 2rem; font-weight: 700; color: #a78bfa; font-family: 'DM Mono', monospace; }
        .metric-label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }
        .invite-code { background: #12122a; border: 2px dashed #5a5af0; border-radius: 10px; padding: 12px 20px; font-family: 'DM Mono', monospace; font-size: 1.3rem; color: #a78bfa; text-align: center; letter-spacing: 2px; }
        .stButton > button { background: linear-gradient(135deg, #7c3aed, #4f46e5); color: white; border: none; border-radius: 8px; font-family: 'Space Grotesk', sans-serif; font-weight: 600; transition: opacity 0.2s; }
        .stButton > button:hover { opacity: 0.85; }
        .stTextInput > div > div > input, .stSelectbox > div > div, .stTextArea > div > div > textarea { background: #1a1a2e !important; border: 1px solid #2a2a4a !important; color: #e8e8f0 !important; border-radius: 8px !important; }
        .stTabs [data-baseweb="tab-list"] { background: #16161d; border-radius: 10px; padding: 4px; gap: 4px; }
        .stTabs [data-baseweb="tab"] { border-radius: 8px; color: #888; font-weight: 600; }
        .stTabs [aria-selected="true"] { background: #2a2a4a !important; color: #a78bfa !important; }
        hr { border-color: #2a2a3a; }
        .locked-banner { background: #2a1a1a; border: 1px solid #ef5350; border-radius: 8px; padding: 10px 16px; color: #ef5350; font-weight: 600; margin: 8px 0; }
    </style>
    """, unsafe_allow_html=True)


# ── Auth screen ────────────────────────────────────

def render_auth():
    params = st.query_params
    if "verify" in params:
        ok, name_or_msg = verify_email_token(params["verify"])
        if ok:
            st.success(f"✅ Email verified! Welcome, **{name_or_msg}**. You can now log in.")
        else:
            st.error(f"❌ {name_or_msg}")
        st.query_params.clear()

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown('<div class="big-title">🏠 WG System</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Smart task & fairness tracker for your flat</div>', unsafe_allow_html=True)

        tab_login, tab_register = st.tabs(["🔐 Login", "✨ Sign up"])

        with tab_login:
            st.markdown("")
            email = st.text_input("Email", key="li_email", placeholder="max@example.com")
            pw    = st.text_input("Password", type="password", key="li_pw", placeholder="••••••••")
            if st.button("Log in", use_container_width=True, key="btn_login"):
                if not email or not pw:
                    st.error("Please fill in all fields.")
                else:
                    user, err = login_user(email, pw)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    elif err == "please_verify":
                        st.warning("⚠️ Please verify your email first. Check your inbox.")
                        if st.button("Resend verification email", key="btn_resend"):
                            if resend_verification(email):
                                st.success("Verification email sent!")
                            else:
                                st.error("Could not send email.")
                    else:
                        st.error(f"❌ {err}")

        with tab_register:
            st.markdown("")
            name   = st.text_input("Name", key="reg_name", placeholder="Max Muster")
            email2 = st.text_input("Email", key="reg_email", placeholder="max@example.com")
            pw2    = st.text_input("Password", type="password", key="reg_pw", placeholder="min. 6 characters")
            pw3    = st.text_input("Repeat password", type="password", key="reg_pw2", placeholder="••••••••")
            if st.button("Create account", use_container_width=True, key="btn_reg"):
                if not all([name, email2, pw2, pw3]):
                    st.error("Please fill in all fields.")
                elif pw2 != pw3:
                    st.error("Passwords do not match.")
                elif len(pw2) < 6:
                    st.error("Password too short (min. 6 characters).")
                else:
                    ok, result = register_user(name, email2, pw2)
                    if ok:
                        if result == "verify":
                            st.success("✅ Account created! Check your inbox to verify your email before logging in.")
                        else:
                            user, _ = login_user(email2, pw2)
                            st.session_state.user = user
                            st.success("✅ Account created!")
                            st.rerun()
                    else:
                        st.error(f"❌ {result}")


# ── Sidebar ────────────────────────────────────────

def render_sidebar(user: dict, wgs: list) -> Optional[str]:
    with st.sidebar:
        st.markdown(f"### 👤 {e(user['name'])}")
        st.markdown(f"<small style='color:#888'>{e(user['email'])}</small>", unsafe_allow_html=True)
        st.markdown("---")

        selected_wg_id = None
        if wgs:
            if "active_wg" not in st.session_state:
                st.session_state.active_wg = wgs[0]["id"]
            st.markdown("**Your flats**")
            for wg in wgs:
                active = wg["id"] == st.session_state.active_wg
                if st.button(
                    f"{'▶ ' if active else '   '}{wg['name']} {'🔒' if wg.get('locked') else ''}",
                    key=f"wg_btn_{wg['id']}", use_container_width=True
                ):
                    st.session_state.active_wg = wg["id"]
                    st.rerun()
            selected_wg_id = st.session_state.active_wg
        else:
            st.info("You're not in any flat yet.")

        st.markdown("---")
        with st.expander("➕ Create flat"):
            wg_name = st.text_input("Flat name", key="new_wg_name", placeholder="Baker Street 221B")
            if st.button("Create", key="create_wg_btn"):
                if wg_name.strip():
                    result = create_wg(wg_name.strip(), user["id"])
                    st.session_state.active_wg = result["id"]
                    get_user_wgs.clear()
                    st.success("✅ Flat created! You are the moderator.")
                    st.rerun()

        with st.expander("🔗 Join flat"):
            code = st.text_input("Invite code", key="join_code", placeholder="abc12345")
            if st.button("Join", key="join_wg_btn"):
                if code.strip():
                    ok, msg = join_wg(code.strip(), user["id"])
                    if ok:
                        get_user_wgs.clear()
                        st.success(f"✅ Joined: {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

        st.markdown("---")
        if st.button("🚪 Log out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    return selected_wg_id


# ── Dashboard ──────────────────────────────────────

def render_dashboard(wg: dict, user: dict, my_role: str):
    members = get_wg_members(wg["id"])
    tasks   = get_tasks(wg["id"])
    today   = today_str()

    # Only trigger rotation when there are no future assignments scheduled
    if tasks:
        future = get_assignments(wg["id"], from_date=week_start(1).isoformat())
        if not future:
            run_rotation(wg["id"])
            get_assignments.clear()

    ws = week_start()
    we = ws + timedelta(days=6)
    week_assignments  = get_assignments(wg["id"], ws.isoformat(), we.isoformat())
    today_assignments = get_assignments(wg["id"], to_date=today, user_id=user["id"])
    my_open   = [a for a in today_assignments if a["status"] == "open"]
    overdue   = [a for a in my_open if a["due_date"] < today]
    due_today = [a for a in my_open if a["due_date"] == today]

    role_info = ROLES.get(my_role, ROLES["member"])
    st.markdown(
        f'<div class="big-title">🏠 {e(wg["name"])}</div>'
        f'<span style="display:inline-block;padding:2px 10px;border-radius:20px;font-size:0.75rem;font-weight:600;'
        f'background:{role_info["color"]}22;color:{role_info["color"]};border:1px solid {role_info["color"]}44">'
        f'{role_info["label"]}</span>',
        unsafe_allow_html=True
    )
    st.markdown(f'<div class="subtitle">{len(members)} members · {len(tasks)} active tasks</div>', unsafe_allow_html=True)

    if wg.get("locked"):
        st.markdown('<div class="locked-banner">🔒 This flat is closed to new members.</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    done_week  = sum(1 for a in week_assignments if a["status"] == "done")
    total_week = len(week_assignments)
    pct = int(done_week / total_week * 100) if total_week else 0
    with c1: st.markdown(f'<div class="metric-box"><div class="metric-val">{len(due_today)}</div><div class="metric-label">Due today</div></div>', unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="metric-box"><div class="metric-val">{len(overdue)}</div><div class="metric-label">Overdue</div></div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="metric-box"><div class="metric-val">{pct}%</div><div class="metric-label">Week done</div></div>', unsafe_allow_html=True)
    with c4: st.markdown(f'<div class="metric-box"><div class="metric-val">{len(members)}</div><div class="metric-label">Members</div></div>', unsafe_allow_html=True)

    st.markdown("")
    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.markdown("### 📋 My Tasks")
        for a in overdue:
            delta = (date.today() - date.fromisoformat(a["due_date"])).days
            st.markdown(f'<div class="task-late">⚠️ <b>{e(a["title"])}</b> &nbsp;<span style="color:#ef5350">{e(a["category"])}</span><br><small style="color:#888">Was due: {a["due_date"]} · {delta} day{"s" if delta > 1 else ""} ago</small></div>', unsafe_allow_html=True)
            if st.button("✅ Done", key=f"done_ov_{a['id']}"):
                complete_assignment(a["id"]); st.rerun()
        for a in due_today:
            st.markdown(f'<div class="task-open">📌 <b>{e(a["title"])}</b> &nbsp;<span style="color:#4caf50">{e(a["category"])}</span><br><small style="color:#888">Due today · ~{a["effort_minutes"]} min</small></div>', unsafe_allow_html=True)
            if st.button("✅ Done", key=f"done_td_{a['id']}"):
                complete_assignment(a["id"]); st.rerun()
        if not overdue and not due_today:
            st.success("🎉 No open tasks for you! Nice work.")

    with col_right:
        st.markdown("### 📅 This week")
        if week_assignments:
            for a in week_assignments[:8]:
                si  = "✅" if a["status"] == "done" else ("⚠️" if a["due_date"] < today else "⏳")
                cls = "task-done" if a["status"] == "done" else ("task-late" if a["due_date"] < today and a["status"] == "open" else "task-open")
                st.markdown(f'<div class="{cls}" style="font-size:13px;">{si} <b>{e(a["title"])}</b><br><small style="color:#888">{e(a["person_name"])} · {a["due_date"]}</small></div>', unsafe_allow_html=True)
        else:
            st.info("No assignments for this week yet.")

    st.markdown("---")
    wg_data = next((w for w in get_user_wgs(user["id"]) if w["id"] == wg["id"]), None)
    if wg_data and not wg.get("locked"):
        st.markdown("### 🔗 Invite flatmates")
        st.markdown(f'<div class="invite-code">{wg_data["invite_code"]}</div>', unsafe_allow_html=True)
        st.caption("Share this code with your flatmates")


# ── Tasks page ─────────────────────────────────────

def render_tasks(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">⚙️ Tasks</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Create, edit and manage task rotation</div>', unsafe_allow_html=True)

    tabs = ["📋 Task list"]
    if can(my_role, "create_task"): tabs.append("➕ New task")
    if can(my_role, "run_rotation"): tabs.append("🔄 Rotation")
    tab_objs = st.tabs(tabs)
    tasks    = get_tasks(wg["id"])

    with tab_objs[0]:
        if not tasks:
            st.info("No tasks yet. Create one in the 'New task' tab.")
        else:
            by_cat = {}
            for t in tasks:
                by_cat.setdefault(t["category"], []).append(t)
            for cat, cat_tasks in by_cat.items():
                st.markdown(f"**{cat}**")
                for t in cat_tasks:
                    col1, col2, col3, col4 = st.columns([3, 1.5, 0.5, 0.5])
                    with col1:
                        st.markdown(f"**{e(t['title'])}**  \n<small style='color:#888'>{e(t['description'] or 'No description')}</small>", unsafe_allow_html=True)
                    with col2:
                        freq_label = t["recurrence"]
                        pday = t.get("preferred_day")
                        if t["recurrence"] == "weekly" and pday is not None:
                            freq_label += f" · {WEEKDAYS[pday]}"
                        elif t["recurrence"] == "monthly" and pday is not None:
                            freq_label += f" · day {pday}"
                        st.caption(f"🔄 {freq_label} · ⏱ {t['effort_minutes']} min")
                    with col3:
                        if can(my_role, "edit_task") and st.button("✏️", key=f"edit_{t['id']}", help="Edit"):
                            st.session_state[f"editing_{t['id']}"] = True; st.rerun()
                    with col4:
                        if can(my_role, "delete_task") and st.button("🗑️", key=f"del_{t['id']}", help="Delete"):
                            delete_task(t["id"]); st.rerun()

                    if st.session_state.get(f"editing_{t['id']}"):
                        with st.expander(f"✏️ Edit '{t['title']}'", expanded=True):
                            e_title = st.text_input("Title", value=t["title"], key=f"e_title_{t['id']}")
                            e_desc  = st.text_area("Description", value=t["description"] or "", key=f"e_desc_{t['id']}", height=70)
                            ec1, ec2, ec3 = st.columns(3)
                            with ec1:
                                e_cat = st.selectbox("Category", CATEGORIES, index=CATEGORIES.index(t["category"]) if t["category"] in CATEGORIES else 0, key=f"e_cat_{t['id']}")
                            with ec2:
                                rl, rv = list(RECURRENCES.keys()), list(RECURRENCES.values())
                                e_rec_label = st.selectbox("Recurrence", rl, index=rv.index(t["recurrence"]) if t["recurrence"] in rv else 1, key=f"e_rec_{t['id']}")
                                e_rec = RECURRENCES[e_rec_label]
                            with ec3:
                                e_effort = st.slider("Effort (min)", 5, 120, t["effort_minutes"], 5, key=f"e_eff_{t['id']}")

                            e_preferred_day = None
                            cur_pday = t.get("preferred_day")
                            if e_rec == "weekly":
                                cur_wd = WEEKDAYS[cur_pday] if (cur_pday is not None and e_rec == t["recurrence"]) else WEEKDAYS[0]
                                wd_sel = st.selectbox("📅 Day of week", WEEKDAYS,
                                    index=WEEKDAYS.index(cur_wd), key=f"e_wd_{t['id']}")
                                e_preferred_day = WEEKDAYS.index(wd_sel)
                            elif e_rec == "monthly":
                                cur_md = cur_pday if (cur_pday is not None and e_rec == t["recurrence"]) else 1
                                e_preferred_day = st.number_input("📅 Day of month (1–31)",
                                    min_value=1, max_value=31, value=int(cur_md), key=f"e_md_{t['id']}")
                            bc1, bc2 = st.columns(2)
                            with bc1:
                                if st.button("💾 Save", key=f"save_{t['id']}", use_container_width=True):
                                    if e_title.strip():
                                        update_task(t["id"], e_title.strip(), e_desc.strip(), e_cat, e_rec, e_effort, e_preferred_day)
                                        del st.session_state[f"editing_{t['id']}"]
                                        st.success("✅ Saved!"); st.rerun()
                                    else:
                                        st.error("Title cannot be empty.")
                            with bc2:
                                if st.button("❌ Cancel", key=f"cancel_{t['id']}", use_container_width=True):
                                    del st.session_state[f"editing_{t['id']}"]; st.rerun()
                st.markdown("")

    if can(my_role, "create_task") and "➕ New task" in tabs:
        with tab_objs[tabs.index("➕ New task")]:
            st.markdown("#### Create new task")
            title = st.text_input("Title*", placeholder="e.g. Clean kitchen", key="task_title")
            desc  = st.text_area("Description", placeholder="Details...", key="task_desc", height=80)
            col1, col2, col3 = st.columns(3)
            with col1: cat = st.selectbox("Category", CATEGORIES, key="task_cat")
            with col2:
                rec_label = st.selectbox("Recurrence", list(RECURRENCES.keys()), key="task_rec")
                rec = RECURRENCES[rec_label]
            with col3: effort = st.slider("Effort (min)", 5, 120, 30, 5, key="task_effort")

            preferred_day = None
            if rec == "weekly":
                wd = st.selectbox("📅 Which day of the week?", WEEKDAYS, key="task_pref_weekday")
                preferred_day = WEEKDAYS.index(wd)
            elif rec == "monthly":
                preferred_day = st.number_input("📅 Which day of the month? (1–31)", min_value=1, max_value=31, value=1, key="task_pref_monthday")

            if st.button("✅ Create task", use_container_width=True, key="btn_create_task"):
                if not title.strip():
                    st.error("Title is required.")
                else:
                    create_task(wg["id"], title.strip(), desc.strip(), cat, rec, effort, preferred_day)
                    run_rotation(wg["id"]); get_assignments.clear()
                    st.success(f"✅ Task '{title}' created and rotation updated!"); st.rerun()

    if can(my_role, "run_rotation") and "🔄 Rotation" in tabs:
        with tab_objs[tabs.index("🔄 Rotation")]:
            st.markdown("#### 🔄 Rotation engine")
            st.markdown("""
            Rotation distributes tasks **automatically and fairly**:
            - Daily (30 days ahead), weekly (8 weeks), monthly (3 months)
            - No one gets the same task twice in a row
            - Balances workload across all members
            """)
            if st.button("🔄 Run rotation now", use_container_width=True):
                n = run_rotation(wg["id"]); get_assignments.clear()
                st.success(f"✅ {n} new assignments created!")
            members = get_wg_members(wg["id"])
            if tasks and members:
                st.markdown("---")
                st.markdown("**Next week preview**")
                ws = week_start(1)
                upcoming = get_assignments(wg["id"], ws.isoformat(), (ws + timedelta(days=6)).isoformat())
                if upcoming:
                    st.dataframe(pd.DataFrame([{"Person": a["person_name"], "Task": a["title"], "Category": a["category"], "Due": a["due_date"]} for a in upcoming]), use_container_width=True, hide_index=True)
                else:
                    st.info("No assignments for next week yet.")


# ── Calendar ───────────────────────────────────────

def render_calendar(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">📅 Calendar</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Who does what and when</div>', unsafe_allow_html=True)

    if "cal_week_offset" not in st.session_state:
        st.session_state.cal_week_offset = 0

    col1, col2, col3 = st.columns([1, 3, 1])
    with col1:
        if st.button("◀ Previous"): st.session_state.cal_week_offset -= 1
    with col3:
        if st.button("Next ▶"): st.session_state.cal_week_offset += 1
    with col2:
        ws = week_start(st.session_state.cal_week_offset)
        we = ws + timedelta(days=6)
        st.markdown(f"<h4 style='text-align:center;color:#a78bfa'>Week {ws.isocalendar()[1]} · {ws.strftime('%d.%m')} – {we.strftime('%d.%m.%Y')}</h4>", unsafe_allow_html=True)

    if st.button("⬤ Today", key="cal_today"):
        st.session_state.cal_week_offset = 0; st.rerun()

    assignments = get_assignments(wg["id"], ws.isoformat(), we.isoformat())
    if not assignments:
        st.info("No tasks in this week.")
        return

    days      = [ws + timedelta(days=i) for i in range(7)]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for col, day, dname in zip(st.columns(7), days, day_names):
        day_str          = day.isoformat()
        day_assignments  = [a for a in assignments if a["due_date"] == day_str]
        is_today         = day_str == today_str()
        with col:
            bg     = "#1a1a2e" if is_today else "#16161d"
            border = "2px solid #a78bfa" if is_today else "1px solid #2a2a3a"
            hc     = "#a78bfa" if is_today else "#888"
            st.markdown(f"<div style='background:{bg};border-radius:10px;padding:10px;min-height:120px;border:{border}'><div style='color:{hc};font-weight:700;margin-bottom:8px'>{dname}<br><small>{day.strftime('%d.%m')}</small></div>", unsafe_allow_html=True)
            for a in day_assignments:
                color = "#4caf50" if a["status"] == "done" else ("#ef5350" if a["due_date"] < today_str() and a["status"] == "open" else "#60a5fa")
                st.markdown(f"<div style='background:#0f0f1a;border-left:3px solid {color};border-radius:4px;padding:6px 8px;margin:4px 0;font-size:12px;'><b>{e(a['title'][:16])}{'…' if len(a['title'])>16 else ''}</b><br><span style='color:#888'>{e(a['person_name'].split()[0])}</span></div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### Details")
    today_s = today_str()
    for a in assignments:
        si = "✅" if a["status"] == "done" else ("⚠️" if a["due_date"] < today_s and a["status"] == "open" else "⏳")
        col1, col2, col3, col4 = st.columns([2, 1.5, 1, 0.8])
        with col1: st.markdown(f"**{e(a['title'])}**")
        with col2: st.markdown(f"👤 {e(a['person_name'])}")
        with col3: st.caption(f"{a['due_date']}  {si}")
        with col4:
            is_mine     = a["assigned_to"] == user["id"]
            can_complete = (is_mine and can(my_role, "complete_own")) or can(my_role, "complete_all")
            if a["status"] == "open" and can_complete:
                if st.button("✅", key=f"cal_done_{a['id']}", help="Mark as done"):
                    complete_assignment(a["id"]); st.rerun()
            elif a["status"] == "done":
                st.caption("✅ done")


# ── Fairness ───────────────────────────────────────

def render_fairness(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">📊 Fairness</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Who contributes how much</div>', unsafe_allow_html=True)

    df = get_fairness_data(wg["id"])
    if df.empty:
        st.info("No completed tasks to show yet.")
        return

    members = get_wg_members(wg["id"])
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Tasks completed per person")
        done_df = df[df["status"] == "done"].copy()
        if not done_df.empty:
            count_df = done_df.groupby("person_name").size().reset_index(name="Count")
            fig = px.bar(count_df, x="person_name", y="Count", color="Count",
                color_continuous_scale=["#4f46e5", "#a78bfa", "#60a5fa"], template="plotly_dark")
            fig.update_layout(paper_bgcolor="#0f0f13", plot_bgcolor="#0f0f13", font=dict(family="Space Grotesk", color="#e8e8f0"), showlegend=False, coloraxis_showscale=False, margin=dict(l=10, r=10, t=10, b=10))
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No completed tasks yet.")

    with col2:
        st.markdown("#### Effort distribution (minutes)")
        if not done_df.empty:
            effort_df = done_df.groupby("person_name")["effort_minutes"].sum().reset_index()
            effort_df.columns = ["Person", "Minutes"]
            fig2 = px.pie(effort_df, values="Minutes", names="Person",
                color_discrete_sequence=["#7c3aed", "#4f46e5", "#60a5fa", "#34d399", "#f59e0b"],
                template="plotly_dark", hole=0.4)
            fig2.update_layout(paper_bgcolor="#0f0f13", font=dict(family="Space Grotesk", color="#e8e8f0"), margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig2, use_container_width=True)

    st.markdown("#### 🏆 Fairness score")
    done_counts = df[df["status"] == "done"].groupby("person_name").size().to_dict()
    total_done  = sum(done_counts.values())
    ideal = total_done / len(members) if members else 1
    cols  = st.columns(max(len(members), 1))
    for i, m in enumerate(members):
        n_done = done_counts.get(m["name"], 0)
        score  = min(100, int((1 - abs(n_done - ideal) / max(ideal, 1)) * 100))
        color  = "#4caf50" if score >= 75 else ("#ff9800" if score >= 50 else "#ef5350")
        with cols[i]:
            st.markdown(f'<div class="metric-box"><div class="metric-val" style="color:{color}">{score}</div><div class="metric-label">{e(m["name"])}</div><div style="color:#888;font-size:0.8rem;margin-top:4px">{n_done} done</div></div>', unsafe_allow_html=True)


# ── Members page ───────────────────────────────────

def render_members(wg: dict, user: dict, my_role: str):
    st.markdown('<div class="big-title">👥 Members</div>', unsafe_allow_html=True)

    members      = get_wg_members(wg["id"])
    workload     = get_workload_counts(wg["id"])
    is_moderator = can(my_role, "manage_roles")

    for m in members:
        is_me     = m["id"] == user["id"]
        done      = workload.get(m["id"], 0)
        ri        = ROLES.get(m.get("role", "member"), ROLES["member"])
        col1, col2, col3, col4 = st.columns([2.5, 1, 1, 1.2])
        with col1:
            me_tag = " <span style='color:#a78bfa'>(you)</span>" if is_me else ""
            badge  = f"<span style='display:inline-block;padding:2px 10px;border-radius:20px;font-size:0.75rem;font-weight:600;margin-left:6px;background:{ri['color']}22;color:{ri['color']};border:1px solid {ri['color']}44'>{ri['label']}</span>"
            st.markdown(
                f"<div style='padding:12px 16px;margin:4px 0;background:linear-gradient(135deg,#1a1a2e,#16213e);border:1px solid #2a2a4a;border-radius:16px'>"
                f"<b>{e(m['name'])}</b>{me_tag}{badge}"
                f"<br><small style='color:#888'>{e(m['email'])}</small></div>",
                unsafe_allow_html=True
            )
        with col2: st.markdown(f"<br><b>{done}</b> done", unsafe_allow_html=True)
        with col3:
            joined = m["joined_at"][:10] if m.get("joined_at") else "–"
            st.markdown(f"<br><small style='color:#888'>since {joined}</small>", unsafe_allow_html=True)
        with col4:
            if is_moderator and not is_me:
                role_options = list(ROLES.keys())
                cur_idx  = role_options.index(m.get("role", "member")) if m.get("role") in role_options else 2
                new_role = st.selectbox("Role", role_options, format_func=lambda r: ROLES[r]["label"], index=cur_idx, key=f"role_sel_{m['id']}", label_visibility="collapsed")
                if new_role != m.get("role") and st.button("✔", key=f"role_save_{m['id']}", help="Save role"):
                    set_member_role(wg["id"], m["id"], new_role)
                    st.success(f"Role of {e(m['name'])} updated."); st.rerun()

    if is_moderator:
        st.markdown("---")
        st.markdown("### 👑 Moderator settings")
        col_lock, col_kick = st.columns(2)
        with col_lock:
            st.markdown("#### 🔒 Lock joining")
            locked = wg.get("locked", False)
            if locked:
                st.warning("🔒 The flat is currently locked.")
                if st.button("🔓 Unlock flat", use_container_width=True):
                    set_wg_locked(wg["id"], False); st.success("Flat is open again."); st.rerun()
            else:
                st.info("✅ The flat is open for new members.")
                if st.button("🔒 Lock flat", use_container_width=True):
                    set_wg_locked(wg["id"], True); st.warning("Flat has been locked."); st.rerun()
        with col_kick:
            st.markdown("#### 🚫 Remove member")
            kickable = [m for m in members if m["id"] != user["id"]]
            if kickable:
                kick_opts = {m["id"]: m["name"] for m in kickable}
                kick_id   = st.selectbox("Select member", options=list(kick_opts.keys()), format_func=lambda x: kick_opts[x], key="kick_select")
                if st.button("🚫 Remove member", use_container_width=True):
                    kick_member(wg["id"], kick_id)
                    st.warning(f"✅ {e(kick_opts[kick_id])} has been removed."); st.rerun()
            else:
                st.info("No other members to remove.")

    if not wg.get("locked") or is_moderator:
        wg_data = next((w for w in get_user_wgs(user["id"]) if w["id"] == wg["id"]), None)
        if wg_data:
            st.markdown("---")
            st.markdown("### 🔗 Invite members")
            if wg.get("locked"):
                st.caption("⚠️ Flat is locked – only you as moderator can share this code.")
            st.markdown(f'<div class="invite-code">{wg_data["invite_code"]}</div>', unsafe_allow_html=True)
            st.code(f"Code: {wg_data['invite_code']}", language=None)


# ── Main ───────────────────────────────────────────

def main():
    setup_page()

    if "user" not in st.session_state:
        render_auth()
        return

    user = st.session_state.user
    wgs  = get_user_wgs(user["id"])
    selected_wg_id = render_sidebar(user, wgs)

    if not wgs:
        st.markdown('<div class="big-title">👋 Welcome!</div>', unsafe_allow_html=True)
        st.markdown("Create your first flat or join an existing one — via the **sidebar on the left**.")
        st.balloons()
        return

    active_wg = next((w for w in wgs if w["id"] == selected_wg_id), wgs[0])
    my_role   = get_member_role(active_wg["id"], user["id"])

    pages = {
        "🏠 Dashboard": render_dashboard,
        "⚙️ Tasks":     render_tasks,
        "📅 Calendar":  render_calendar,
        "📊 Fairness":  render_fairness,
        "👥 Members":   render_members,
    }

    st.markdown("")
    page_name = st.radio("Navigation", list(pages.keys()), horizontal=True, label_visibility="collapsed", key="main_nav")
    st.markdown("---")
    pages[page_name](active_wg, user, my_role)


if __name__ == "__main__":
    main()
