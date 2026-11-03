import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true",
)

database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
elif database_url and database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")

if not database_url:
    database_path = Path(os.environ.get("DATABASE_PATH", "instance/bank_demo.sqlite3"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"

url = make_url(database_url)
engine = create_engine(
    url,
    connect_args={"check_same_thread": False} if url.get_backend_name() == "sqlite" else {},
)
metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(32), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(10), nullable=False),
    Column("balance_cents", BigInteger, nullable=False, server_default="0"),
    Column("account_number", String(12), nullable=True),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("role IN ('client', 'admin')"),
    CheckConstraint("balance_cents >= 0"),
)
Index("uq_users_account_number", users.c.account_number, unique=True)
transactions_table = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("kind", String(10), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("balance_after_cents", BigInteger, nullable=False),
    Column("counterparty_account", String(12), nullable=True),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("kind IN ('deposit', 'withdrawal')"),
    CheckConstraint("amount_cents > 0"),
    CheckConstraint("balance_after_cents >= 0"),
)


@contextmanager
def get_db():
    with engine.begin() as connection:
        yield connection


def initialize_database():
    metadata.create_all(engine)
    migrate_schema()
    admin_username = os.environ.get("ADMIN_USERNAME") or os.environ.get(
        "DEMO_ADMIN_USERNAME"
    )
    admin_password = os.environ.get("ADMIN_PASSWORD") or os.environ.get(
        "DEMO_ADMIN_PASSWORD"
    )
    if bool(admin_username) != bool(admin_password):
        raise RuntimeError(
            "Set both ADMIN_USERNAME and ADMIN_PASSWORD to provision an administrator."
        )
    if admin_username and admin_password:
        seed_user(admin_username, admin_password, "admin")


def generate_account_number():
    return f"{secrets.randbelow(10**12):012d}"


def migrate_schema():
    existing_users = {column["name"] for column in inspect(engine).get_columns("users")}
    existing_transactions = {
        column["name"] for column in inspect(engine).get_columns("transactions")
    }

    with engine.begin() as db:
        if "account_number" not in existing_users:
            db.exec_driver_sql("ALTER TABLE users ADD COLUMN account_number VARCHAR(12)")
        if "counterparty_account" not in existing_transactions:
            db.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN counterparty_account VARCHAR(12)"
            )
        db.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_account_number "
            "ON users (account_number)"
        )
        existing_ids = db.execute(
            text("SELECT id FROM users WHERE account_number IS NULL ORDER BY id")
        ).scalars().all()
        for user_id in existing_ids:
            account_number = generate_account_number()
            db.execute(
                text("UPDATE users SET account_number = :number WHERE id = :user_id"),
                {"number": account_number, "user_id": user_id},
            )


def seed_user(username, password, role):
    if not username or not password:
        raise RuntimeError(f"Initial {role} username and password must not be empty.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise RuntimeError(
            f"Initial {role} username must be 3–32 letters, numbers, dots, dashes, or underscores."
        )
    with get_db() as db:
        result = db.execute(
            text(
                """INSERT INTO users
                   (username, password_hash, role, account_number, created_at)
                   VALUES (:username, :password_hash, :role, :account_number, :created_at)
                   ON CONFLICT (username) DO UPDATE
                   SET password_hash = excluded.password_hash
                   WHERE users.role = excluded.role"""
            ),
            {
                "username": username,
                "password_hash": generate_password_hash(password),
                "role": role,
                "account_number": generate_account_number(),
                "created_at": now(),
            },
        )
        if result.rowcount == 0:
            raise RuntimeError(
                f"The configured {role} username is already assigned to a different role."
            )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("home"))
            if role and session.get("role") != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def validate_csrf():
    if not secrets.compare_digest(
        str(request.form.get("csrf_token", "")),
        str(session.get("csrf_token", "")),
    ):
        abort(400, description="Your form expired. Refresh the page and try again.")


def parse_amount(value):
    try:
        amount = Decimal(value.strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, AttributeError):
        return None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000"):
        return None
    return int(amount * 100)


@app.context_processor
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"]}


@app.template_filter("php")
def format_php(cents):
    pesos, centavos = divmod(int(cents), 100)
    return f"₱{pesos:,}.{centavos:02d}"


@app.get("/")
def home():
    if session.get("role") == "client":
        return redirect(url_for("client_dashboard"))
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return home()
    if request.method == "GET":
        return render_template("register.html")

    validate_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    password_confirmation = request.form.get("password_confirmation", "")

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        flash(
            "Username must be 3–32 characters: letters, numbers, dots, dashes, or underscores.",
            "error",
        )
        return render_template("register.html")
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("register.html")
    if password != password_confirmation:
        flash("Passwords do not match.", "error")
        return render_template("register.html")

    try:
        with get_db() as db:
            db.execute(
                text(
                    """INSERT INTO users
                       (username, password_hash, role, account_number, created_at)
                       VALUES (:username, :password_hash, 'client', :account_number, :created_at)"""
                ),
                {
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "account_number": generate_account_number(),
                    "created_at": now(),
                },
            )
            user_id = db.execute(
                text("SELECT id FROM users WHERE username = :username"),
                {"username": username},
            ).scalar_one()
    except IntegrityError:
        flash("That username is already in use. Choose another one.", "error")
        return render_template("register.html")

    session.clear()
    session["user_id"] = user_id
    session["username"] = username
    session["role"] = "client"
    session["csrf_token"] = secrets.token_urlsafe(32)
    flash("Your account has been created.", "success")
    return redirect(url_for("client_dashboard"))


@app.post("/login")
def login():
    validate_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "")
    if role not in ("client", "admin"):
        flash("Choose an account type to continue.", "error")
        return redirect(url_for("home"))

    with get_db() as db:
        user = db.execute(
            text(
                "SELECT id, username, password_hash, role FROM users "
                "WHERE username = :username"
            ),
            {"username": username},
        ).mappings().first()

    if user is None or user["role"] != role or not check_password_hash(
        user["password_hash"], password
    ):
        flash("The username, password, or account type is incorrect.", "error")
        return redirect(url_for("home"))

    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return redirect(
        url_for("admin_dashboard" if role == "admin" else "client_dashboard")
    )


@app.post("/logout")
@login_required()
def logout():
    validate_csrf()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


@app.get("/client")
@login_required("client")
def client_dashboard():
    with get_db() as db:
        user = db.execute(
            text(
                "SELECT username, balance_cents, account_number "
                "FROM users WHERE id = :user_id"
            ),
            {"user_id": session["user_id"]},
        ).mappings().first()
        transactions = db.execute(
            text(
                """SELECT kind, amount_cents, balance_after_cents,
                          counterparty_account, created_at
                   FROM transactions WHERE user_id = :user_id
                   ORDER BY id DESC LIMIT 20"""
            ),
            {"user_id": session["user_id"]},
        ).mappings().all()
    return render_template("client.html", user=user, transactions=transactions)


@app.post("/transaction")
@login_required("client")
def transact():
    validate_csrf()
    kind = request.form.get("kind")
    amount_cents = parse_amount(request.form.get("amount", ""))
    if kind not in ("deposit", "withdrawal") or amount_cents is None:
        flash("Enter a valid amount greater than ₱0.00.", "error")
        return redirect(url_for("client_dashboard"))

    with get_db() as db:
        balance_update = (
            "balance_cents = balance_cents + :amount_cents"
            if kind == "deposit"
            else "balance_cents = balance_cents - :amount_cents"
        )
        withdrawal_guard = (
            " AND balance_cents >= :amount_cents" if kind == "withdrawal" else ""
        )
        updated = db.execute(
            text(
                f"""UPDATE users SET {balance_update}
                    WHERE id = :user_id{withdrawal_guard}
                    RETURNING balance_cents"""
            ),
            {"amount_cents": amount_cents, "user_id": session["user_id"]},
        ).mappings().first()
        if updated is None:
            user_exists = db.execute(
                text("SELECT 1 FROM users WHERE id = :user_id"),
                {"user_id": session["user_id"]},
            ).first()
            if not user_exists:
                session.clear()
                return redirect(url_for("home"))
            if kind == "withdrawal":
                flash("There is not enough money in your available balance.", "error")
                return redirect(url_for("client_dashboard"))
            abort(500, description="The transaction could not be completed.")

        new_balance = updated["balance_cents"]
        db.execute(
            text(
                """INSERT INTO transactions
                   (user_id, kind, amount_cents, balance_after_cents, created_at)
                   VALUES (:user_id, :kind, :amount_cents, :balance_after_cents, :created_at)"""
            ),
            {
                "user_id": session["user_id"],
                "kind": kind,
                "amount_cents": amount_cents,
                "balance_after_cents": new_balance,
                "created_at": now(),
            },
        )

    label = "Deposited" if kind == "deposit" else "Withdrew"
    flash(f"{label} {format_php(amount_cents)} successfully.", "success")
    return redirect(url_for("client_dashboard"))


@app.post("/transfer")
@login_required("client")
def transfer():
    validate_csrf()
    recipient_account = request.form.get("recipient_account", "").strip()
    amount_cents = parse_amount(request.form.get("amount", ""))
    if not re.fullmatch(r"\d{12}", recipient_account) or amount_cents is None:
        flash("Enter a valid 12-digit recipient account number and transfer amount.", "error")
        return redirect(url_for("client_dashboard"))

    sender_id = session["user_id"]
    with get_db() as db:
        sender = db.execute(
            text(
                "SELECT account_number FROM users "
                "WHERE id = :user_id AND role = 'client'"
            ),
            {"user_id": sender_id},
        ).mappings().first()
        recipient = db.execute(
            text(
                "SELECT id, username FROM users "
                "WHERE account_number = :account_number AND role = 'client'"
            ),
            {"account_number": recipient_account},
        ).mappings().first()

        if sender is None:
            session.clear()
            return redirect(url_for("home"))
        if recipient is None:
            flash("We couldn't find a Demel Bank customer with that account number.", "error")
            return redirect(url_for("client_dashboard"))
        if recipient["id"] == sender_id:
            flash("Choose another customer's account; you can't transfer to yourself.", "error")
            return redirect(url_for("client_dashboard"))

        sender_balance = db.execute(
            text(
                """UPDATE users SET balance_cents = balance_cents - :amount_cents
                   WHERE id = :user_id AND balance_cents >= :amount_cents
                   RETURNING balance_cents"""
            ),
            {"amount_cents": amount_cents, "user_id": sender_id},
        ).mappings().first()
        if sender_balance is None:
            flash("There is not enough money in your available balance.", "error")
            return redirect(url_for("client_dashboard"))

        recipient_balance = db.execute(
            text(
                """UPDATE users SET balance_cents = balance_cents + :amount_cents
                   WHERE id = :user_id
                   RETURNING balance_cents"""
            ),
            {"amount_cents": amount_cents, "user_id": recipient["id"]},
        ).mappings().first()
        if recipient_balance is None:
            abort(500, description="The transfer could not be completed.")

        timestamp = now()
        db.execute(
            text(
                """INSERT INTO transactions
                   (user_id, kind, amount_cents, balance_after_cents,
                    counterparty_account, created_at)
                   VALUES (:user_id, 'withdrawal', :amount_cents, :balance_after_cents,
                           :counterparty_account, :created_at)"""
            ),
            {
                "user_id": sender_id,
                "amount_cents": amount_cents,
                "balance_after_cents": sender_balance["balance_cents"],
                "counterparty_account": recipient_account,
                "created_at": timestamp,
            },
        )
        db.execute(
            text(
                """INSERT INTO transactions
                   (user_id, kind, amount_cents, balance_after_cents,
                    counterparty_account, created_at)
                   VALUES (:user_id, 'deposit', :amount_cents, :balance_after_cents,
                           :counterparty_account, :created_at)"""
            ),
            {
                "user_id": recipient["id"],
                "amount_cents": amount_cents,
                "balance_after_cents": recipient_balance["balance_cents"],
                "counterparty_account": sender["account_number"],
                "created_at": timestamp,
            },
        )

    flash(
        f"Simulated transfer of {format_php(amount_cents)} sent to "
        f"{recipient['username']}. No real funds were moved.",
        "success",
    )
    return redirect(url_for("client_dashboard"))


@app.get("/admin")
@login_required("admin")
def admin_dashboard():
    with get_db() as db:
        clients = db.execute(
            text(
                """SELECT id, username, balance_cents, account_number, created_at
                   FROM users WHERE role = 'client' ORDER BY username"""
            )
        ).mappings().all()
        transactions = db.execute(
            text(
                """SELECT transactions.kind, transactions.amount_cents,
                          transactions.balance_after_cents, transactions.created_at,
                          transactions.counterparty_account,
                          users.username
                   FROM transactions JOIN users ON users.id = transactions.user_id
                   ORDER BY transactions.id DESC LIMIT 50"""
            )
        ).mappings().all()
    return render_template("admin.html", clients=clients, transactions=transactions)


@app.post("/admin/clients")
@login_required("admin")
def create_client():
    validate_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        flash(
            "Username must be 3–32 characters: letters, numbers, dots, dashes, or underscores.",
            "error",
        )
        return redirect(url_for("admin_dashboard", _anchor="clients"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("admin_dashboard", _anchor="clients"))

    try:
        with get_db() as db:
            db.execute(
                text(
                    """INSERT INTO users
                       (username, password_hash, role, account_number, created_at)
                       VALUES (:username, :password_hash, 'client', :account_number, :created_at)"""
                ),
                {
                    "username": username,
                    "password_hash": generate_password_hash(password),
                    "account_number": generate_account_number(),
                    "created_at": now(),
                },
            )
    except IntegrityError:
        flash("That username is already in use. Choose another one.", "error")
        return redirect(url_for("admin_dashboard", _anchor="clients"))

    flash(f"Client account “{username}” created successfully.", "success")
    return redirect(url_for("admin_dashboard", _anchor="clients"))


@app.errorhandler(400)
@app.errorhandler(403)
def handle_http_error(error):
    return render_template("error.html", error=error), error.code


initialize_database()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
