import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AdminProvisioningTests(unittest.TestCase):
    def run_app_script(self, script, admin_username=None, admin_password=None):
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite://"
        environment["SECRET_KEY"] = "admin-provisioning-test-secret"
        environment.pop("DEMO_ADMIN_USERNAME", None)
        environment.pop("DEMO_ADMIN_PASSWORD", None)
        if admin_username is None:
            environment.pop("ADMIN_USERNAME", None)
        else:
            environment["ADMIN_USERNAME"] = admin_username
        if admin_password is None:
            environment.pop("ADMIN_PASSWORD", None)
        else:
            environment["ADMIN_PASSWORD"] = admin_password
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_environment_credentials_provision_admin_login(self):
        self.run_app_script(
            """
from app import app
client = app.test_client()
with client.session_transaction() as session:
    session["csrf_token"] = "test-csrf-token"
response = client.post("/login", data={
    "username": "hosted_admin",
    "password": "first-admin-password",
    "role": "admin",
    "csrf_token": "test-csrf-token",
})
assert response.status_code == 302
assert response.headers["Location"].endswith("/admin")
""",
            admin_username="hosted_admin",
            admin_password="first-admin-password",
        )

    def test_startup_rotates_existing_admin_password(self):
        self.run_app_script(
            """
import os
from app import app, get_db, initialize_database
from sqlalchemy import text
from werkzeug.security import check_password_hash
client = app.test_client()
with get_db() as database:
    old_hash = database.execute(text(
        "SELECT password_hash FROM users WHERE username = :username"
    ), {"username": "hosted_admin"}).scalar_one()
assert check_password_hash(old_hash, "first-admin-password")
os.environ["ADMIN_PASSWORD"] = "second-admin-password"
initialize_database()
with get_db() as database:
    new_hash = database.execute(text(
        "SELECT password_hash FROM users WHERE username = :username"
    ), {"username": "hosted_admin"}).scalar_one()
assert check_password_hash(new_hash, "second-admin-password")
assert not check_password_hash(new_hash, "first-admin-password")
""",
            admin_username="hosted_admin",
            admin_password="first-admin-password",
        )

    def test_no_admin_is_created_without_credentials(self):
        self.run_app_script(
            """
from app import get_db
from sqlalchemy import text
with get_db() as database:
    count = database.execute(text(
        "SELECT COUNT(*) FROM users WHERE role = 'admin'"
    )).scalar_one()
assert count == 0
"""
        )

    def test_admin_configuration_cannot_promote_a_customer(self):
        self.run_app_script(
            """
import os
from app import get_db, initialize_database, seed_user
from sqlalchemy import text
seed_user("existing_customer", "customer-password", "client")
os.environ["ADMIN_USERNAME"] = "existing_customer"
os.environ["ADMIN_PASSWORD"] = "new-admin-password"
try:
    initialize_database()
except RuntimeError as error:
    assert "already assigned to a different role" in str(error)
else:
    raise AssertionError("A customer account was incorrectly promoted to admin.")
with get_db() as database:
    role = database.execute(text(
        "SELECT role FROM users WHERE username = :username"
    ), {"username": "existing_customer"}).scalar_one()
assert role == "client"
"""
        )


if __name__ == "__main__":
    unittest.main()
