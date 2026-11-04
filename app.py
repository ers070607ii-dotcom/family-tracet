"""Family Budget Tracker - Python (Flask) backend with SQLite."""
import csv
import io
import os
import re
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "budget.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")
PAGES_DIR = os.path.join(BASE, "templates")

CATEGORIES = ["Groceries", "Transport", "Utilities", "Health", "Other"]
DEFAULT_LIMITS = {"Groceries": 40000, "Transport": 15000, "Utilities": 30000, "Health": 20000, "Other": 10000}
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-real-use")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB per upload
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(os.path.join(BASE, "schema.sql"), encoding="utf-8") as f:
        db.executescript(f.read())
    db.commit()
    db.close()


# -------------------------------------------------------------------- auth
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute(
        "SELECT u.*, h.invite_code FROM users u JOIN households h ON h.id = u.household_id WHERE u.id = ?",
        (uid,),
    ).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify(errors={"auth": "auth"}), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def valid_month(value):
    """Return YYYY-MM from the query string, or the current month."""
    if value and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        return value
    return date.today().strftime("%Y-%m")


def user_json(u):
    return {"name": u["name"], "email": u["email"], "role": u["role"], "invite_code": u["invite_code"]}


@app.post("/api/register")
def register():
    d = request.get_json(force=True, silent=True) or {}
    name = str(d.get("name", "")).strip()
    email = str(d.get("email", "")).strip().lower()
    password = str(d.get("password", ""))
    role = d.get("role")
    code = str(d.get("household_code", "")).strip().upper()

    errors = {}
    if len(name) < 2:
        errors["name"] = "name"
    if not EMAIL_RE.match(email):
        errors["email"] = "email"
    if len(password) < 8:
        errors["password"] = "password"
    if role not in ("admin", "member"):
        errors["role"] = "role"
    if errors:
        return jsonify(errors=errors), 400

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return jsonify(errors={"email": "email_taken"}), 409

    if role == "admin":
        invite = secrets.token_hex(3).upper()
        hid = db.execute(
            "INSERT INTO households (name, invite_code) VALUES (?, ?)", (f"{name} household", invite)
        ).lastrowid
        db.executemany(
            "INSERT INTO limits (household_id, category, monthly_limit) VALUES (?, ?, ?)",
            [(hid, c, DEFAULT_LIMITS[c]) for c in CATEGORIES],
        )
    else:
        row = db.execute("SELECT id FROM households WHERE invite_code = ?", (code,)).fetchone()
        if not row:
            return jsonify(errors={"household_code": "household_code"}), 400
        hid = row["id"]

    uid = db.execute(
        "INSERT INTO users (name, email, password_hash, role, household_id) VALUES (?, ?, ?, ?, ?)",
        (name, email, generate_password_hash(password), role, hid),
    ).lastrowid
    db.commit()
    session["uid"] = uid
    return jsonify(ok=True), 201


@app.post("/api/login")
def login():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], str(d.get("password", ""))):
        return jsonify(errors={"login": "bad_login"}), 401
    session["uid"] = user["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def me():
    return jsonify(user_json(g.user))


# ---------------------------------------------------------------- expenses
def expense_json(r):
    return {
        "id": f"E-{1000 + r['id']}",
        "pk": r["id"],
        "date": r["purchase_date"],
        "amount": r["amount"],
        "category": r["category"],
        "store": r["store"] or "",
        "note": r["note"] or "",
        "photo": f"/uploads/{r['receipt_path']}" if r["receipt_path"] else "",
        "by": r["user_name"],
    }


def month_spent(db, hid, category, month):
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses "
        "WHERE household_id = ? AND category = ? AND strftime('%Y-%m', purchase_date) = ?",
        (hid, category, month),
    ).fetchone()
    return row["s"]


@app.get("/api/expenses")
@login_required
def list_expenses():
    month = valid_month(request.args.get("month"))
    rows = get_db().execute(
        "SELECT e.*, u.name AS user_name FROM expenses e JOIN users u ON u.id = e.user_id "
        "WHERE e.household_id = ? AND strftime('%Y-%m', e.purchase_date) = ? "
        "ORDER BY e.purchase_date DESC, e.id DESC LIMIT 200",
        (g.user["household_id"], month),
    ).fetchall()
    return jsonify([expense_json(r) for r in rows])


@app.post("/api/expenses")
@login_required
def add_expense():
    f = request.form
    errors = {}

    d = f.get("date", "").strip()
    try:
        purchase = datetime.strptime(d, "%Y-%m-%d").date()
        if purchase > date.today():
            errors["date"] = "date"
    except ValueError:
        errors["date"] = "date"

    try:
        amount = int(round(float(f.get("amount", ""))))
        if amount <= 0:
            errors["amount"] = "amount"
    except ValueError:
        errors["amount"] = "amount"

    category = f.get("category", "")
    if category not in CATEGORIES:
        errors["category"] = "category"

    photo = request.files.get("photo")
    ext = ""
    if photo and photo.filename:
        ext = photo.filename.rsplit(".", 1)[-1].lower() if "." in photo.filename else ""
        if ext not in ALLOWED_EXT:
            errors["photo"] = "photo"

    if errors:
        return jsonify(errors=errors), 400

    db = get_db()
    hid = g.user["household_id"]

    # Duplicate check from the Task 7 governance rule (same date + amount + category)
    if f.get("force") != "1":
        dup = db.execute(
            "SELECT id FROM expenses WHERE household_id = ? AND purchase_date = ? AND amount = ? AND category = ?",
            (hid, d, amount, category),
        ).fetchone()
        if dup:
            return jsonify(duplicate=True, existing=f"E-{1000 + dup['id']}"), 409

    filename = None
    if photo and photo.filename:
        filename = f"{secrets.token_hex(8)}.{ext}"
        photo.save(os.path.join(UPLOAD_DIR, filename))

    new_id = db.execute(
        "INSERT INTO expenses (household_id, user_id, purchase_date, amount, category, store, note, receipt_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (hid, g.user["id"], d, amount, category, f.get("store", "").strip()[:80],
         f.get("note", "").strip()[:200], filename),
    ).lastrowid
    db.commit()

    limit = db.execute(
        "SELECT monthly_limit FROM limits WHERE household_id = ? AND category = ?", (hid, category)
    ).fetchone()["monthly_limit"]
    percent = round(month_spent(db, hid, category, d[:7]) / limit * 100)
    row = db.execute(
        "SELECT e.*, u.name AS user_name FROM expenses e JOIN users u ON u.id = e.user_id WHERE e.id = ?",
        (new_id,),
    ).fetchone()
    return jsonify(expense=expense_json(row), percent=percent, category=category), 201


@app.delete("/api/expenses/<int:pk>")
@login_required
def delete_expense(pk):
    if g.user["role"] != "admin":
        return jsonify(errors={"auth": "forbidden"}), 403
    db = get_db()
    row = db.execute(
        "SELECT receipt_path FROM expenses WHERE id = ? AND household_id = ?", (pk, g.user["household_id"])
    ).fetchone()
    if not row:
        return jsonify(errors={"auth": "forbidden"}), 404
    db.execute("DELETE FROM expenses WHERE id = ?", (pk,))
    db.commit()
    if row["receipt_path"]:
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["receipt_path"]))
        except OSError:
            pass
    return jsonify(ok=True)


# ------------------------------------------------------- summary and limits
@app.get("/api/summary")
@login_required
def summary():
    db = get_db()
    month = valid_month(request.args.get("month"))
    rows = db.execute(
        "SELECT l.category, l.monthly_limit, COALESCE(SUM(e.amount), 0) AS spent "
        "FROM limits l LEFT JOIN expenses e ON e.household_id = l.household_id AND e.category = l.category "
        "AND strftime('%Y-%m', e.purchase_date) = ? WHERE l.household_id = ? GROUP BY l.category",
        (month, g.user["household_id"]),
    ).fetchall()
    by_cat = {r["category"]: r for r in rows}
    return jsonify([
        {"category": c, "limit": by_cat[c]["monthly_limit"], "spent": by_cat[c]["spent"]}
        for c in CATEGORIES if c in by_cat
    ])


@app.get("/api/members")
@login_required
def members():
    rows = get_db().execute(
        "SELECT name, role FROM users WHERE household_id = ? ORDER BY role, name", (g.user["household_id"],)
    ).fetchall()
    return jsonify([{"name": r["name"], "role": r["role"]} for r in rows])


@app.get("/api/export.csv")
@login_required
def export_csv():
    month = valid_month(request.args.get("month"))
    rows = get_db().execute(
        "SELECT e.*, u.name AS user_name FROM expenses e JOIN users u ON u.id = e.user_id "
        "WHERE e.household_id = ? AND strftime('%Y-%m', e.purchase_date) = ? ORDER BY e.purchase_date, e.id",
        (g.user["household_id"], month),
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["ExpenseID", "PurchaseDate", "Amount", "Category", "Store", "Note", "AddedBy"])
    for r in rows:
        w.writerow([f"E-{1000 + r['id']}", r["purchase_date"], r["amount"], r["category"],
                    r["store"] or "", r["note"] or "", r["user_name"]])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=expenses-{month}.csv"})


@app.put("/api/limits")
@login_required
def set_limits():
    if g.user["role"] != "admin":
        return jsonify(errors={"auth": "forbidden"}), 403
    d = request.get_json(force=True, silent=True) or {}
    cat, value = d.get("category"), d.get("limit")
    if cat not in CATEGORIES or not isinstance(value, int) or value <= 0:
        return jsonify(errors={"amount": "amount"}), 400
    db = get_db()
    db.execute(
        "UPDATE limits SET monthly_limit = ? WHERE household_id = ? AND category = ?",
        (value, g.user["household_id"], cat),
    )
    db.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------ files / pages
@app.get("/uploads/<name>")
@login_required
def uploaded(name):
    row = get_db().execute(
        "SELECT 1 FROM expenses WHERE receipt_path = ? AND household_id = ?", (name, g.user["household_id"])
    ).fetchone()
    if not row:
        return jsonify(errors={"auth": "forbidden"}), 404
    return send_from_directory(UPLOAD_DIR, name)


@app.get("/")
def page_index():
    return send_from_directory(PAGES_DIR, "index.html")


@app.get("/scan")
def page_scan():
    return send_from_directory(PAGES_DIR, "scan.html")


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
