import os, sqlite3
from werkzeug.security import generate_password_hash

DB = os.path.join("instance", "site.db")
os.makedirs("instance", exist_ok=True)

conn = sqlite3.connect(DB)
cur = conn.cursor()

# สร้างตาราง user
cur.execute("""
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

# เพิ่มแอดมินเริ่มต้น
cur.execute("""
INSERT OR IGNORE INTO user (first_name, last_name, email, password_hash, is_admin)
VALUES (?, ?, ?, ?, ?)
""", ("Admin", "User", "admin@example.com", generate_password_hash("123456"), 1))

conn.commit()
conn.close()

print("✅ site.db ถูกสร้างแล้ว และ admin@example.com (123456) ถูกเพิ่ม")
