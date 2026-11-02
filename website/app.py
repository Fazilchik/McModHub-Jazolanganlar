
import sqlite3
from pathlib import Path
from flask import Flask, render_template, request

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "moderation.db"

app = Flask(__name__)


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS punishment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                punishment_type TEXT NOT NULL,
                reason TEXT,
                duration TEXT,
                admin_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


@app.route("/")
def index():
    search = request.args.get("q", "").strip()
    punishment_filter = request.args.get("type", "").strip().upper()

    with connect_db() as db:
        stats = db.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN UPPER(punishment_type) = 'WARN' THEN 1 ELSE 0 END) AS warns,
                SUM(CASE WHEN UPPER(punishment_type) = 'MUTE' THEN 1 ELSE 0 END) AS mutes,
                SUM(CASE WHEN UPPER(punishment_type) = 'BAN' THEN 1 ELSE 0 END) AS bans
            FROM punishment_history
        """).fetchone()

        query = "SELECT * FROM punishment_history WHERE 1=1"
        params = []

        if search:
            query += """
                AND (
                    username LIKE ?
                    OR full_name LIKE ?
                    OR CAST(user_id AS TEXT) LIKE ?
                )
            """
            term = f"%{search}%"
            params.extend([term, term, term])

        if punishment_filter in ("WARN", "MUTE", "BAN", "UNBAN", "UNMUTE", "UNWARN"):
            query += " AND UPPER(punishment_type) = ?"
            params.append(punishment_filter)

        query += " ORDER BY id DESC LIMIT 200"
        records = db.execute(query, params).fetchall()

    return render_template(
        "index.html",
        stats=stats,
        records=records,
        search=search,
        selected_type=punishment_filter
    )


@app.route("/health")
def health():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
