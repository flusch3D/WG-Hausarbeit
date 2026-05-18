# WG Task System v0.9 (Beta)
# TODO: Refactor this before finals...
# TODO: Fix timezone bug if someone uses this outside of Zurich

import streamlit as st
import hashlib
import uuid
import smtplib
import pandas as pd
import plotly.express as px
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
import os
import secrets
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# --- DB SETUP ---

@st.cache_resource
def get_db():
    # takes credentials from secrets
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except:
        # fallback for local testing
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
    
    if not url or not key:
        st.error("No Supabase keys found!!")
        st.stop()
    return create_client(url, key)


# --- CONSTANTS ---

KATEGORIEN = ["🧹 Cleaning", "🛒 Shopping", "🗑️ Trash", "🍳 Cooking",
              "🧺 Laundry", "🌿 Plants", "🔧 Repairs", "📦 Other"]

# mapping for recurrence stuff
REC_MAP = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
REC_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
REC_PERIODS = {"daily": 30, "weekly": 8, "monthly": 3}
WOCHENTAGE = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

ROLES = {
    "moderator": {"label": "👑 Admin", "color": "#f59e0b"},
    "editor":    {"label": "✏️ Editor", "color": "#60a5fa"},
    "member":    {"label": "👤 Pleb", "color": "#888"}, # normal user
}

# --- HELPERS ---

def hash_pw(pw):
    # TODO: implement bcrypt instead of sha256. whatever, good enough for now
    return hashlib.sha256(pw.encode()).hexdigest()

def get_uuid():
    return str(uuid.uuid4())

def get_week_start(offset=0):
    d = date.today()
    return d - timedelta(days=d.weekday()) + timedelta(weeks=offset)

def check_permission(role, action):
    # hardcoded permissions logic
    if role == "moderator": return True
    if role == "editor" and action in ["create_task", "edit_task", "run_rotation", "complete_own"]: return True
    if role == "member" and action == "complete_own": return True
    return False

# --- EMAIL STUFF ---

def send_verification(email, name, token):
    # Sometimes ETH network blocks this, use hotspot if testing locally
    host = os.environ.get("EMAIL_HOST") or st.secrets.get("EMAIL_HOST", "")
    user = os.environ.get("EMAIL_USER") or st.secrets.get("EMAIL_USER", "")
    pw = os.environ.get("EMAIL_PASSWORD") or st.secrets.get("EMAIL_PASSWORD", "")
    
    if not host or not user:
        print("No email config, skipping verification")
        return False
        
    app_url = st.secrets.get("APP_URL", "")
    verify_url = f"{app_url}?verify={token}" if app_url else f"Token: {token}"
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Verify your WG Account"
        msg["From"] = user
        msg["To"] = email
        
        # HTML template (inline bc I don't want to make a separate file)
        html = f"""
        <div style="font-family:sans-serif;padding:20px;background:#111;color:#eee;border-radius:8px;">
            <h2 style="color:#a78bfa">WG System</h2>
            <p>Hey {name},</p>
            <p>Click here to verify: <a href="{verify_url}" style="color:#60a5fa">Verify Email</a></p>
        </div>"""
        
        msg.attach(MIMEText(f"Verify here: {verify_url}", "plain"))
        msg.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP(host, 587) as server:
            server.starttls()
            server.login(user, pw)
            server.sendmail(user, email, msg.as_string())
        return True
    except Exception as e:
        print(f"SMTP error: {e}")
        return False

# --- AUTH ---

def register(name, email, password):
    sb = get_db()
    token = secrets.token_urlsafe(32)
    # dirty check if email config exists
    needs_verify = bool(st.secrets.get("EMAIL_HOST", "")) 
    
    try:
        res = sb.table("users").insert({
            "name": name,
            "email": email.lower(),
            "password": hash_pw(password),
            "verified": not needs_verify,
            "verification_token": token if needs_verify else None,
        }).execute()
        
        if res.data:
            if needs_verify:
                sent = send_verification(email, name, token)
                if not sent:
                    # fallback if email fails to send
                    sb.table("users").update({"verified": True}).eq("id", res.data[0]["id"]).execute()
                    return True, "ok"
                return True, "verify"
            return True, "ok"
        return False, "Failed"
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return False, "Email already exists."
        return False, str(e)

def login(email, password):
    res = get_db().table("users").select("*").eq("email", email.lower()).eq("password", hash_pw(password)).execute()
    if not res.data: return None, "Wrong email or password"
    u = res.data[0]
    if not u.get("verified", True): return None, "please_verify"
    return u, ""

# --- WG LOGIC ---

def make_wg(name, user_id):
    wgId = get_uuid()
    inv = get_uuid()[:8] # shorter invite code
    sb = get_db()
    sb.table("wgs").insert({"id": wgId, "name": name, "invite_code": inv, "created_by": user_id}).execute()
    sb.table("wg_members").insert({"wg_id": wgId, "user_id": user_id, "role": "moderator"}).execute()
    return wgId

@st.cache_data(ttl=60)
def get_my_wgs(user_id):
    sb = get_db()
    mem = sb.table("wg_members").select("wg_id").eq("user_id", user_id).execute()
    ids = [m["wg_id"] for m in mem.data]
    if not ids: return []
    return sb.table("wgs").select("*").in_("id", ids).execute().data

@st.cache_data(ttl=30)
def get_mitglieder(wgId):
    # Returns members of a WG
    sb = get_db()
    mem = sb.table("wg_members").select("user_id, role, joined_at").eq("wg_id", wgId).execute()
    if not mem.data: return []
    
    u_ids = [m["user_id"] for m in mem.data]
    users = sb.table("users").select("id, name, email").in_("id", u_ids).execute()
    user_dict = {u["id"]: u for u in users.data}
    
    res = []
    for m in mem.data:
        u = user_dict.get(m["user_id"], {})
        res.append({
            "id": m["user_id"], "name": u.get("name", "Unknown"), 
            "email": u.get("email", ""), "role": m.get("role", "member"),
            "joined_at": m.get("joined_at", "")
        })
    return res

# --- TASKS ---

@st.cache_data(ttl=30)
def get_tasks(wgId):
    return get_db().table("tasks").select("*").eq("wg_id", wgId).eq("active", True).order("category").execute().data

def run_rotation(wgId):
    # Algorithm to distribute tasks evenly.
    # Time complexity is probably O(n^2) but N is small so whatever lol.
    sb = get_db()
    members = get_mitglieder(wgId)
    tasks = get_tasks(wgId)
    if not members or not tasks: return 0

    m_ids = [m["id"] for m in members]
    global_load = get_workload(wgId)
    today = date.today()
    all_slots = []
    
    for t in tasks:
        rec = t["recurrence"]
        d_ahead = REC_DAYS.get(rec, 7)
        
        last = sb.table("assignments").select("due_date").eq("task_id", t["id"]).order("due_date", desc=True).limit(1).execute()
        anchor = date.fromisoformat(last.data[0]["due_date"]) + timedelta(days=d_ahead) if last.data else today

        if t.get("preferred_day") is not None:
            if rec == "weekly":
                diff = (t["preferred_day"] - anchor.weekday()) % 7
                anchor += timedelta(days=diff)
            elif rec == "monthly":
                try: anchor = anchor.replace(day=t["preferred_day"])
                except: pass # ignore if day is 31 and month has 30 days (cba to import calendar rn)

        for i in range(REC_PERIODS.get(rec, 4)):
            due = anchor + timedelta(days=d_ahead * i)
            if due < today: continue
            
            exists = sb.table("assignments").select("id").eq("task_id", t["id"]).eq("due_date", due.isoformat()).execute()
            if not exists.data:
                all_slots.append({"task": t, "due": due})

    all_slots.sort(key=lambda x: x["due"])
    created = 0
    last_person = {} 
    daily_load = {} 

    for slot in all_slots:
        tid = slot["task"]["id"]
        due_s = slot["due"].isoformat()
        
        # Sort members by daily load, then global load to keep it fair
        sorted_m = sorted(m_ids, key=lambda u: (
            daily_load.get((u, due_s), 0),
            global_load.get(u, 0)
        ))

        nxt = sorted_m[0]
        # try not to assign to the same person twice in a row for the same task
        if len(sorted_m) > 1 and nxt == last_person.get(tid):
            nxt = sorted_m[1]

        sb.table("assignments").insert({
            "id": get_uuid(), "task_id": tid, "wg_id": wgId,
            "assigned_to": nxt, "due_date": due_s,
            "status": "open", "comment": ""
        }).execute()

        global_load[nxt] = global_load.get(nxt, 0) + 1
        daily_load[(nxt, due_s)] = daily_load.get((nxt, due_s), 0) + 1
        last_person[tid] = nxt
        created += 1

    return created

@st.cache_data(ttl=60)
def get_workload(wgId):
    rows = get_db().table("assignments").select("assigned_to").eq("wg_id", wgId).eq("status", "done").execute().data
    counts = {}
    for r in rows:
        uid = r["assigned_to"]
        counts[uid] = counts.get(uid, 0) + 1
    return counts

@st.cache_data(ttl=15) # fast cache for UI responsiveness
def get_assignments(wgId, from_d=None, to_d=None, user_id=None):
    q = get_db().table("assignments").select("*").eq("wg_id", wgId)
    if from_d: q = q.gte("due_date", from_d)
    if to_d: q = q.lte("due_date", to_d)
    if user_id: q = q.eq("assigned_to", user_id)
    
    rows = q.order("due_date").execute().data
    if not rows: return []
    
    t_ids = list({r["task_id"] for r in rows})
    u_ids = list({r["assigned_to"] for r in rows})
    
    t_data = get_db().table("tasks").select("id, title, category, effort_minutes, active").in_("id", t_ids).execute().data
    u_data = get_db().table("users").select("id, name").in_("id", u_ids).execute().data
    
    t_map = {t["id"]: t for t in t_data}
    u_map = {u["id"]: u for u in u_data}

    res = []
    for r in rows:
        t = t_map.get(r["task_id"], {})
        if not t.get("active", True): continue # skip deleted tasks
        u = u_map.get(r["assigned_to"], {})
        r["title"] = t.get("title", "???")
        r["category"] = t.get("category", "")
        r["effort_minutes"] = t.get("effort_minutes", 0)
        r["person_name"] = u.get("name", "???")
        res.append(r)
    return res

def complete_task(aid):
    get_db().table("assignments").update({
        "status": "done", "completed_at": datetime.now().isoformat()
    }).eq("id", aid).execute()
    get_assignments.clear()
    get_workload.clear()

# --- UI STUFF ---

def setup_page():
    st.set_page_config(page_title="WG Tool", page_icon="🍻", layout="wide")
    
    # Giant dump of CSS
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap');
        html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; background: #0f0f13; color: #eee; }
        .sidebar .sidebar-content { background: #16161d !important; }
        .task-open { background: #1e2a1e; border-left: 4px solid #4caf50; padding: 12px; margin: 8px 0; border-radius: 4px; }
        .task-done { background: #1a1a1a; border-left: 4px solid #555; padding: 12px; margin: 8px 0; opacity: 0.5; }
        .task-late { background: #2a1a1a; border-left: 4px solid #ef5350; padding: 12px; margin: 8px 0; border-radius: 4px; }
        .big-title { font-size: 2.2rem; font-weight: bold; background: linear-gradient(90deg, #a78bfa, #60a5fa); -webkit-background-clip: text; color: transparent; }
        .metric { background: #1a1a2e; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #333; }
        .invite-box { font-family: monospace; background: #222; padding: 10px; text-align: center; font-size: 1.5rem; color: #a78bfa; border: 1px dashed #555; }
    </style>
    """, unsafe_allow_html=True)


def login_page():
    params = st.query_params
    if "verify" in params:
        # DB logic for verify link
        res = get_db().table("users").select("id").eq("verification_token", params["verify"]).execute()
        if res.data:
            get_db().table("users").update({"verified": True, "verification_token": None}).eq("id", res.data[0]["id"]).execute()
            st.success("Verified! You can log in now.")
        else:
            st.error("Invalid link.")
        st.query_params.clear()

    st.markdown('<div class="big-title" style="text-align:center;">🏠 WG Task Tracker</div>', unsafe_allow_html=True)
    st.write("")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        
        with tab1:
            em = st.text_input("Email", key="l_em")
            pw = st.text_input("Password", type="password", key="l_pw")
            if st.button("Log In", use_container_width=True):
                user, err = login(em, pw)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error(err)
                    
        with tab2:
            n = st.text_input("Name")
            e = st.text_input("Email")
            p1 = st.text_input("Password", type="password")
            p2 = st.text_input("Repeat", type="password")
            
            if st.button("Sign Up", use_container_width=True):
                if p1 != p2: st.error("Passwords don't match")
                elif len(p1) < 6: st.error("Password too short")
                else:
                    ok, msg = register(n, e, p1)
                    if ok and msg == "verify":
                        st.success("Check your email to verify!")
                    elif ok:
                        u, _ = login(e, p1)
                        st.session_state.user = u
                        st.rerun()
                    else:
                        st.error(msg)

# --- VIEWS ---

def render_dash(wg, user, role):
    st.markdown(f'<div class="big-title">{wg["name"]} Dashboard</div>', unsafe_allow_html=True)
    
    tasks = get_tasks(wg["id"])
    if tasks:
        # only run rotation if week is empty
        future = get_assignments(wg["id"], from_d=get_week_start(1).isoformat())
        if not future:
            print("Running auto-rotation...")
            run_rotation(wg["id"])
            get_assignments.clear()

    today_str = date.today().isoformat()
    my_tasks = get_assignments(wg["id"], to_d=today_str, user_id=user["id"])
    open_tasks = [t for t in my_tasks if t["status"] == "open"]
    
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.subheader("📋 My To-Do's")
        if not open_tasks:
            st.success("Nothing to do. Crack open a beer! 🍻")
        else:
            for t in open_tasks:
                is_late = t["due_date"] < today_str
                cls = "task-late" if is_late else "task-open"
                icon = "⚠️" if is_late else "📌"
                # using raw strings for html, hope nobody does XSS lol
                st.markdown(f'<div class="{cls}">{icon} <b>{t["title"]}</b> ({t["category"]})<br><small>Due: {t["due_date"]}</small></div>', unsafe_allow_html=True)
                if st.button("Done", key=f"d_{t['id']}"):
                    complete_task(t["id"])
                    st.rerun()

    with c2:
        st.subheader("Stats")
        mems = get_mitglieder(wg["id"])
        st.markdown(f'<div class="metric">👥 {len(mems)} Members</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric">📝 {len(tasks)} Active Tasks</div>', unsafe_allow_html=True)
        
        st.write("")
        st.write("Invite Code:")
        st.markdown(f'<div class="invite-box">{wg["invite_code"]}</div>', unsafe_allow_html=True)


def render_tasks(wg, user, role):
    st.markdown('<div class="big-title">⚙️ Manage Tasks</div>', unsafe_allow_html=True)
    tasks = get_tasks(wg["id"])
    
    if check_permission(role, "create_task"):
        with st.expander("➕ Add New Task"):
            t_title = st.text_input("Title")
            t_desc = st.text_area("Description")
            col1, col2, col3 = st.columns(3)
            cat = col1.selectbox("Category", KATEGORIEN)
            rec = col2.selectbox("Recurrence", list(REC_MAP.keys()))
            eff = col3.slider("Effort (min)", 5, 120, 15)
            
            p_day = None
            if rec == "weekly": p_day = WOCHENTAGE.index(st.selectbox("Day", WOCHENTAGE))
            elif rec == "monthly": p_day = st.number_input("Day of month", 1, 31, 1)

            if st.button("Create Task"):
                tid = get_uuid()
                get_db().table("tasks").insert({
                    "id": tid, "wg_id": wg["id"], "title": t_title, "description": t_desc,
                    "category": cat, "recurrence": REC_MAP[rec], "effort_minutes": eff,
                    "active": True, "preferred_day": p_day
                }).execute()
                get_tasks.clear()
                run_rotation(wg["id"])
                st.rerun()

    st.write("### Current Tasks")
    for t in tasks:
        c1, c2 = st.columns([3, 1])
        c1.write(f"**{t['title']}** - {t['recurrence']} ({t['effort_minutes']}min)")
        if check_permission(role, "edit_task"):
            if c2.button("Delete", key=f"del_{t['id']}"):
                get_db().table("tasks").update({"active": False}).eq("id", t["id"]).execute()
                get_tasks.clear()
                st.rerun()
    
    if check_permission(role, "run_rotation"):
        st.write("---")
        if st.button("🔄 Force Re-run Rotation Algorithm"):
            n = run_rotation(wg["id"])
            get_assignments.clear()
            st.success(f"Generated {n} new tasks")

def render_cal(wg, user, role):
    st.markdown('<div class="big-title">📅 WG Calendar</div>', unsafe_allow_html=True)
    
    if "cal_off" not in st.session_state: st.session_state.cal_off = 0
    
    c1, c2, c3 = st.columns([1,2,1])
    if c1.button("< Prev Week"): st.session_state.cal_off -= 1
    if c3.button("Next Week >"): st.session_state.cal_off += 1
    
    ws = get_week_start(st.session_state.cal_off)
    we = ws + timedelta(days=6)
    c2.markdown(f"<h3 style='text-align:center;'>{ws.strftime('%d.%m')} - {we.strftime('%d.%m')}</h3>", unsafe_allow_html=True)

    assigns = get_assignments(wg["id"], ws.isoformat(), we.isoformat())
    
    cols = st.columns(7)
    days = [ws + timedelta(days=i) for i in range(7)]
    
    for i, col in enumerate(cols):
        d_str = days[i].isoformat()
        col.write(f"**{WOCHENTAGE[i][:3]}** {days[i].strftime('%d.')}")
        
        day_tasks = [a for a in assigns if a["due_date"] == d_str]
        for t in day_tasks:
            color = "green" if t["status"] == "done" else "orange" if t["due_date"] < date.today().isoformat() else "blue"
            col.markdown(f"<div style='border-left: 3px solid {color}; padding: 4px; background: #222; margin-bottom: 4px; font-size: 12px;'>{t['title']}<br><small>{t['person_name']}</small></div>", unsafe_allow_html=True)

def render_fairness(wg, user, role):
    st.markdown('<div class="big-title">📊 Fairness Dashboard</div>', unsafe_allow_html=True)
    st.write("Who's actually doing the work?")
    
    df = pd.DataFrame(get_assignments(wg["id"]))
    if df.empty or len(df[df["status"] == "done"]) == 0:
        st.info("No data yet. Do some cleaning first!")
        return
        
    done_df = df[df["status"] == "done"]
    
    c1, c2 = st.columns(2)
    with c1:
        st.write("Tasks Done")
        fig = px.bar(done_df.groupby("person_name").size().reset_index(name="count"), x="person_name", y="count", template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)
        
    with c2:
        st.write("Time Spent (Minutes)")
        eff = done_df.groupby("person_name")["effort_minutes"].sum().reset_index()
        fig2 = px.pie(eff, values="effort_minutes", names="person_name", template="plotly_dark", hole=0.3)
        st.plotly_chart(fig2, use_container_width=True)

def render_members(wg, user, role):
    st.markdown('<div class="big-title">👥 WG Members</div>', unsafe_allow_html=True)
    
    mems = get_mitglieder(wg["id"])
    load = get_workload(wg["id"])
    
    for m in mems:
        c1, c2 = st.columns([3, 1])
        role_label = ROLES.get(m["role"], ROLES["member"])["label"]
        c1.write(f"**{m['name']}** - {m['email']} ({role_label})")
        c2.write(f"Done: {load.get(m['id'], 0)} tasks")
        
        # Admin stuff
        if role == "moderator" and m["id"] != user["id"]:
            new_role = st.selectbox("Role", list(ROLES.keys()), index=list(ROLES.keys()).index(m["role"]), key=f"r_{m['id']}")
            if new_role != m["role"]:
                get_db().table("wg_members").update({"role": new_role}).eq("wg_id", wg["id"]).eq("user_id", m["id"]).execute()
                get_mitglieder.clear()
                st.rerun()

# --- MAIN APP ---

def main():
    setup_page()
    
    if "user" not in st.session_state:
        login_page()
        return

    user = st.session_state.user
    
    # Sidebar
    with st.sidebar:
        st.write(f"### Sup, {user['name']}")
        st.write("---")
        
        wgs = get_my_wgs(user["id"])
        selected_wg = None
        
        if wgs:
            if "active_wg" not in st.session_state: st.session_state.active_wg = wgs[0]["id"]
            for w in wgs:
                if st.button(f"{'👉 ' if w['id'] == st.session_state.active_wg else ''}{w['name']}", use_container_width=True):
                    st.session_state.active_wg = w["id"]
                    st.rerun()
            selected_wg = next(w for w in wgs if w["id"] == st.session_state.active_wg)
        
        st.write("---")
        with st.expander("Create new WG"):
            n_wg = st.text_input("Name")
            if st.button("Create"):
                nid = make_wg(n_wg, user["id"])
                get_my_wgs.clear()
                st.session_state.active_wg = nid
                st.rerun()
                
        with st.expander("Join WG"):
            code = st.text_input("Invite Code")
            if st.button("Join"):
                w_res = get_db().table("wgs").select("id").eq("invite_code", code).execute()
                if w_res.data:
                    get_db().table("wg_members").insert({"wg_id": w_res.data[0]["id"], "user_id": user["id"], "role": "member"}).execute()
                    get_my_wgs.clear()
                    st.session_state.active_wg = w_res.data[0]["id"]
                    st.rerun()
                else:
                    st.error("Invalid Code")
                    
        st.write("---")
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

    # Main view routing (classic if/elif bc lazy)
    if not wgs:
        st.write("# Welcome! Create or join a WG on the left.")
        st.balloons()
        return

    # get role for current wg
    mem_record = get_db().table("wg_members").select("role").eq("wg_id", selected_wg["id"]).eq("user_id", user["id"]).execute()
    my_role = mem_record.data[0]["role"] if mem_record.data else "member"

    # Navigation
    nav = st.radio("Go to", ["Dashboard", "Tasks", "Calendar", "Fairness", "Members"], horizontal=True, label_visibility="collapsed")
    st.write("---")
    
    if nav == "Dashboard": render_dash(selected_wg, user, my_role)
    elif nav == "Tasks": render_tasks(selected_wg, user, my_role)
    elif nav == "Calendar": render_cal(selected_wg, user, my_role)
    elif nav == "Fairness": render_fairness(selected_wg, user, my_role)
    elif nav == "Members": render_members(selected_wg, user, my_role)

if __name__ == "__main__":
    main()
