import atexit
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="demel-bank-tests-")
TEST_DATABASE = Path(TEST_DIRECTORY.name) / "tests.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ.pop("ADMIN_USERNAME", None)
os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("DEMO_ADMIN_USERNAME", None)
os.environ.pop("DEMO_ADMIN_PASSWORD", None)

from app import app, get_db  # noqa: E402


def cleanup_test_directory():
    from app import engine

    engine.dispose()
    shutil.rmtree(TEST_DIRECTORY.name, ignore_errors=True)


atexit.register(cleanup_test_directory)


PASSWORD = "customer-password"
SENDER_ACCOUNT = "111111111111"
RECIPIENT_ACCOUNT = "222222222222"
ADMIN_ACCOUNT = "333333333333"


class AccountTransferTests(unittest.TestCase):
    def setUp(self):
        with get_db() as database:
            database.execute(text("DELETE FROM transactions"))
            database.execute(text("DELETE FROM users"))
            database.execute(
                text(
                    """INSERT INTO users
                       (username, password_hash, role, balance_cents, account_number, created_at)
                       VALUES
                       ('sender', :password_hash, 'client', 50000, :sender_account, :created_at),
                       ('recipient', :password_hash, 'client', 10000, :recipient_account, :created_at),
                       ('admin_user', :password_hash, 'admin', 0, :admin_account, :created_at)"""
                ),
                {
                    "password_hash": generate_password_hash(PASSWORD),
                    "sender_account": SENDER_ACCOUNT,
                    "recipient_account": RECIPIENT_ACCOUNT,
                    "admin_account": ADMIN_ACCOUNT,
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            )
        self.client = app.test_client()

    def csrf_token(self):
        self.client.get("/")
        with self.client.session_transaction() as browser_session:
            return browser_session["csrf_token"]

    def sign_in(self, username="sender", role="client"):
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(),
                "username": username,
                "password": PASSWORD,
                "role": role,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Demel Bank", response.data)
        return response

    def transfer(self, account, amount, csrf_token=None):
        return self.client.post(
            "/transfer",
            data={
                "csrf_token": csrf_token if csrf_token is not None else self.csrf_token(),
                "recipient_account": account,
                "amount": amount,
            },
            follow_redirects=True,
        )

    def user_row(self, username):
        with get_db() as database:
            return database.execute(
                text("SELECT id, balance_cents, account_number FROM users WHERE username = :username"),
                {"username": username},
            ).mappings().one()

    def test_existing_users_have_unique_account_numbers_and_dashboard_shows_them(self):
        self.sign_in()
        response = self.client.get("/client")

        self.assertIn(SENDER_ACCOUNT.encode(), response.data)
        with get_db() as database:
            accounts = database.execute(
                text("SELECT account_number FROM users ORDER BY id")
            ).scalars().all()
        self.assertEqual(len(accounts), len(set(accounts)))
        self.assertTrue(all(re.fullmatch(r"\d{12}", account) for account in accounts))

    def test_internal_transfer_updates_both_balances_and_both_ledgers(self):
        self.sign_in()
        response = self.transfer(RECIPIENT_ACCOUNT, "125.50")

        self.assertIn(b"Simulated transfer of", response.data)
        self.assertIn(b"Transfer sent", response.data)
        self.assertEqual(self.user_row("sender")["balance_cents"], 37450)
        self.assertEqual(self.user_row("recipient")["balance_cents"], 22550)
        with get_db() as database:
            entries = database.execute(
                text(
                    """SELECT users.username, transactions.kind, transactions.amount_cents,
                              transactions.balance_after_cents,
                              transactions.counterparty_account
                       FROM transactions JOIN users ON users.id = transactions.user_id
                       ORDER BY transactions.id"""
                )
            ).mappings().all()
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [(row["username"], row["kind"], row["balance_after_cents"]) for row in entries],
            [("sender", "withdrawal", 37450), ("recipient", "deposit", 22550)],
        )
        self.assertEqual(
            [row["counterparty_account"] for row in entries],
            [RECIPIENT_ACCOUNT, SENDER_ACCOUNT],
        )

    def test_transfer_cannot_overdraw_and_creates_no_partial_ledger(self):
        self.sign_in()
        response = self.transfer(RECIPIENT_ACCOUNT, "500.01")

        self.assertIn(b"not enough money", response.data)
        self.assertEqual(self.user_row("sender")["balance_cents"], 50000)
        self.assertEqual(self.user_row("recipient")["balance_cents"], 10000)
        with get_db() as database:
            self.assertEqual(
                database.execute(text("SELECT COUNT(*) FROM transactions")).scalar_one(), 0
            )

    def test_transfer_rejects_self_admin_unknown_and_malformed_recipients(self):
        self.sign_in()
        for account, message in (
            (SENDER_ACCOUNT, b"can&#39;t transfer to yourself"),
            (ADMIN_ACCOUNT, b"couldn&#39;t find a Demel Bank customer"),
            ("999999999999", b"couldn&#39;t find a Demel Bank customer"),
            ("not-an-account", b"valid 12-digit recipient account number"),
        ):
            with self.subTest(account=account):
                response = self.transfer(account, "10.00")
                self.assertIn(message, response.data)
        self.assertEqual(self.user_row("sender")["balance_cents"], 50000)
        self.assertEqual(self.user_row("recipient")["balance_cents"], 10000)

    def test_transfer_requires_customer_sign_in_and_csrf(self):
        response = self.client.post(
            "/transfer",
            data={"recipient_account": RECIPIENT_ACCOUNT, "amount": "10.00"},
        )
        self.assertEqual(response.status_code, 302)

        self.sign_in()
        rejected = self.transfer(RECIPIENT_ACCOUNT, "10.00", csrf_token="invalid")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.user_row("sender")["balance_cents"], 50000)

    def test_registration_and_admin_created_accounts_get_unique_numbers(self):
        self.sign_in()
        self.client.post("/logout", data={"csrf_token": self.csrf_token()})
        register_token = self.csrf_token()
        registration = self.client.post(
            "/register",
            data={
                "csrf_token": register_token,
                "username": "registered_customer",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Your account has been created", registration.data)
        registered_account = self.user_row("registered_customer")["account_number"]
        self.assertRegex(registered_account, r"^\d{12}$")

        self.client.post("/logout", data={"csrf_token": self.csrf_token()})
        self.sign_in("admin_user", role="admin")
        self.client.post(
            "/admin/clients",
            data={
                "csrf_token": self.csrf_token(),
                "username": "admin_created",
                "password": PASSWORD,
            },
        )
        with get_db() as database:
            accounts = database.execute(
                text("SELECT account_number FROM users ORDER BY id")
            ).scalars().all()
        self.assertEqual(len(accounts), len(set(accounts)))
        self.assertRegex(self.user_row("admin_created")["account_number"], r"^\d{12}$")


class ExistingDatabaseMigrationTests(unittest.TestCase):
    def test_existing_database_gets_account_numbers_and_transfer_metadata(self):
        legacy_path = Path(TEST_DIRECTORY.name) / "legacy.sqlite"
        database = sqlite3.connect(legacy_path)
        try:
            database.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR(32) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(10) NOT NULL,
                    balance_cents BIGINT NOT NULL DEFAULT 0,
                    created_at VARCHAR(40) NOT NULL
                );
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    kind VARCHAR(10) NOT NULL,
                    amount_cents BIGINT NOT NULL,
                    balance_after_cents BIGINT NOT NULL,
                    created_at VARCHAR(40) NOT NULL
                );
                INSERT INTO users
                    (id, username, password_hash, role, balance_cents, created_at)
                    VALUES
                    (1, 'legacy_customer', 'hash', 'client', 500, '2026-01-01'),
                    (2, 'second_legacy', 'hash', 'client', 0, '2026-01-02');
                INSERT INTO transactions
                    (id, user_id, kind, amount_cents, balance_after_cents, created_at)
                    VALUES (1, 1, 'deposit', 500, 500, '2026-01-01');
                """
            )
        finally:
            database.close()

        environment = os.environ.copy()
        environment["DATABASE_URL"] = f"sqlite:///{legacy_path.as_posix()}"
        for name in (
            "ADMIN_USERNAME",
            "ADMIN_PASSWORD",
            "DEMO_ADMIN_USERNAME",
            "DEMO_ADMIN_PASSWORD",
        ):
            environment.pop(name, None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import re
from sqlalchemy import text
from app import get_db
with get_db() as database:
    account = database.execute(text(
        "SELECT account_number FROM users WHERE id = 1"
    )).scalar_one()
    accounts = database.execute(text(
        "SELECT account_number FROM users ORDER BY id"
    )).scalars().all()
    columns = {row[1] for row in database.exec_driver_sql(
        "PRAGMA table_info(transactions)"
    )}
assert re.fullmatch(r"\\d{12}", account)
assert len(accounts) == len(set(accounts))
assert "counterparty_account" in columns
""",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
