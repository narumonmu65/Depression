from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import check_password_hash
import sqlite3, os, re

app = Flask(__name__)
app.secret_key = "dev-key"

# ====================== SQLite helpers ======================
DB_PATH = os.path.join("instance", "site.db")
os.makedirs("instance", exist_ok=True)

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(
            DB_PATH,
            timeout=10,
            check_same_thread=False
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute("PRAGMA busy_timeout=5000;")
        db.execute("PRAGMA foreign_keys=ON;")
    return db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("_db", None)
    if db is not None:
        db.close()

def strip_tags(text: str) -> str:
    return re.sub(r'<[^>]*>', '', text or '')

def column_exists(table: str, column: str) -> bool:
    cur = get_db().execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return column in cols

# ---------- สร้าง/อัปเดตตาราง (ตอนสตาร์ตแอพ) ----------
with app.app_context():
    conn = get_db()
    # ค่า setting ปัจจุบัน
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_setting(
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # ประวัติการแก้ไขคำแนะนำ (รวมทุกโหมด)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommend_history(
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT NOT NULL,
            author     TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # MIGRATION: ถ้าไม่มีคอลัมน์ key ให้เพิ่ม และเติมค่าเริ่มต้นเป็น recommend_text
    if not column_exists("recommend_history", "key"):
        conn.execute("ALTER TABLE recommend_history ADD COLUMN key TEXT;")
        conn.execute("""
            UPDATE recommend_history
               SET key = COALESCE(key, 'recommend_text')
             WHERE key IS NULL;
        """)
    conn.commit()

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM site_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    conn = get_db()
    conn.execute("""
        INSERT INTO site_setting(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()

# ====================== Routes ======================

# หน้าแรก
@app.route("/")
def home():
    return render_template("index.html")

# หน้าแสดงเนื้อหา/บทความ (ทั่วไป)
@app.route("/info")
def info():
    raw = get_setting("recommend_text", "")
    content = raw if "<" in raw else raw.replace("\n", "<br>")
    return render_template("info.html", content=content)

# หน้าแบบประเมิน (ส่งคำแนะนำเสี่ยง/ไม่เสี่ยงให้เทมเพลตใช้)
@app.route("/assess")
def assess():
    risk_text = get_setting(
        "recommend_text_risk",
        "<ul><li>นอนหลับพักผ่อนให้เพียงพอ</li><li>ออกกำลังกายสม่ำเสมอ</li><li>พูดคุยกับคนไว้ใจได้</li></ul>"
    )
    safe_text = get_setting(
        "recommend_text_safe",
        "<p>คุณไม่มีความเสี่ยงต่อภาวะซึมเศร้า ดูแลสุขภาพกาย-ใจให้ดีเหมือนเดิมนะครับ</p>"
    )
    return render_template("assess.html", risk_text=risk_text, safe_text=safe_text)

# -------------------- Auth --------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","")

        with get_db() as conn:
            row = conn.execute("SELECT * FROM user WHERE email=?", (email,)).fetchone()

        if row and check_password_hash(row["password_hash"], password):
            session["user_id"] = row["id"]
            session["is_admin"] = bool(row["is_admin"])
            session["email"] = row["email"]
            flash("เข้าสู่ระบบสำเร็จ", "success")
            return redirect(url_for("admin")) if session["is_admin"] else redirect(url_for("home"))
        else:
            flash("อีเมลหรือรหัสผ่านไม่ถูกต้อง", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("ออกจากระบบแล้ว", "success")
    return redirect(url_for("home"))

# -------------------- Admin dashboard --------------------
@app.route("/admin")
def admin():
    if not session.get("is_admin"):
        flash("ไม่อนุญาตให้เข้าถึง", "danger")
        return redirect(url_for("login"))
    return render_template("admin.html")

# -------------------- Editor (generic) --------------------
def load_history_rows(key: str, limit: int = 50):
    rows = get_db().execute("""
        SELECT id, key, author, created_at, content, length(content) AS length
        FROM recommend_history
        WHERE key=?
        ORDER BY id DESC
        LIMIT ?
    """, (key, limit)).fetchall()

    history = []
    for r in rows:
        preview = strip_tags(r["content"])
        if len(preview) > 120:
            preview = preview[:120] + "…"
        history.append({
            "id": r["id"],
            "author": r["author"],
            "created_at": r["created_at"],
            "preview": preview,
            "length": r["length"],
        })
    return history

def render_editor(key: str, title: str):
    current_text = get_setting(key, "")
    history = load_history_rows(key)
    return render_template(
        "admin_recommend.html",
        recommend_text=current_text,
        history=history,
        editor_title=title,
        editor_key=key
    )

def save_editor_post(key: str):
    text = (request.form.get("recommend_text") or "").strip()
    author = session.get("email") or "admin"
    conn = get_db()
    conn.execute(
        "INSERT INTO recommend_history(key, content, author) VALUES(?,?,?)",
        (key, text, author)
    )
    conn.commit()
    set_setting(key, text)

# ------------ หน้าแก้ไข: บทความ/ความรู้ (ทั่วไป) ------------
@app.route("/admin/recommend", methods=["GET", "POST"])
def admin_recommend():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    key = "recommend_text"
    if request.method == "POST":
        save_editor_post(key)
        flash("บันทึกบทความ/ความรู้เรียบร้อยแล้ว", "success")
        return redirect(url_for("admin_recommend"))
    return render_editor(key, "เพิ่มความรู้เกี่ยวกับโรคซึมเศร้า")

# ------------ หน้าแก้ไข: คำแนะนำกรณีเสี่ยง ------------
@app.route("/admin/recommend/risk", methods=["GET", "POST"])
def admin_recommend_risk():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    key = "recommend_text_risk"
    if request.method == "POST":
        save_editor_post(key)
        flash("บันทึกคำแนะนำ (กรณีเสี่ยง) เรียบร้อยแล้ว", "success")
        return redirect(url_for("admin_recommend_risk"))
    return render_editor(key, "แก้ไขคำแนะนำ (กรณีเสี่ยง)")

# ------------ หน้าแก้ไข: คำแนะนำกรณีไม่เสี่ยง ------------
@app.route("/admin/recommend/safe", methods=["GET", "POST"])
def admin_recommend_safe():
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    key = "recommend_text_safe"
    if request.method == "POST":
        save_editor_post(key)
        flash("บันทึกคำแนะนำ (กรณีไม่เสี่ยง) เรียบร้อยแล้ว", "success")
        return redirect(url_for("admin_recommend_safe"))
    return render_editor(key, "แก้ไขคำแนะนำ (กรณีไม่เสี่ยง)")

# โหลดเวอร์ชัน (inject เข้า editor)
@app.route("/admin/recommend/load/<editor_key>/<int:hid>")
def admin_recommend_load(editor_key, hid):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    row = get_db().execute(
        "SELECT content FROM recommend_history WHERE id=? AND key=?",
        (hid, editor_key)
    ).fetchone()
    if not row:
        flash("ไม่พบเวอร์ชันที่เลือก", "danger")
        return redirect(url_for("admin"))

    history = load_history_rows(editor_key)
    title = "แก้ไขคำแนะนำ (กรณีเสี่ยง)" if editor_key.endswith("risk") \
        else "แก้ไขคำแนะนำ (กรณีไม่เสี่ยง)" if editor_key.endswith("safe") \
        else "เพิ่มความรู้เกี่ยวกับโรคซึมเศร้า"

    return render_template(
        "admin_recommend.html",
        recommend_text=row["content"],
        history=history,
        loaded_id=hid,
        editor_title=title,
        editor_key=editor_key
    )

# กู้คืนเวอร์ชัน (ตั้งเป็นค่า setting ปัจจุบันด้วย)
@app.route("/admin/recommend/restore/<editor_key>/<int:hid>", methods=["POST"])
def admin_recommend_restore(editor_key, hid):
    if not session.get("is_admin"):
        return redirect(url_for("login"))

    row = get_db().execute(
        "SELECT content FROM recommend_history WHERE id=? AND key=?",
        (hid, editor_key)
    ).fetchone()
    if not row:
        flash("ไม่พบเวอร์ชันที่ต้องการกู้คืน", "danger")
        return redirect(url_for("admin"))

    content = row["content"]
    author = session.get("email") or "admin"
    conn = get_db()
    conn.execute(
        "INSERT INTO recommend_history(key, content, author) VALUES(?,?,?)",
        (editor_key, content, author)
    )
    conn.commit()
    set_setting(editor_key, content)

    flash("กู้คืนเวอร์ชันเรียบร้อยแล้ว", "success")
    if editor_key == "recommend_text_risk":
        return redirect(url_for("admin_recommend_risk"))
    elif editor_key == "recommend_text_safe":
        return redirect(url_for("admin_recommend_safe"))
    else:
        return redirect(url_for("admin_recommend"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))   # ดึงค่า PORT จาก Render หรือใช้ 5000 ตอนรันในเครื่อง
    app.run(host="0.0.0.0", port=port, debug=True)

