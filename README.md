# Demel Bank

A Flask-based banking interface prototype with personal account registration, sign-in, a simulated balance, transaction history, and an administrator dashboard.

> **Prototype notice:** This project does not connect to a bank, process payments, or hold real money. Balances and transactions are simulated. Do not enter real passwords or sensitive personal information; this project is not designed or audited for production banking.

## Run locally

Use Python 3.10 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser. The local database is `instance/bank_demo.sqlite3`; registered accounts and transaction records persist across restarts. Passwords are stored as hashes.

Visitors can create a personal account from the sign-in page. Usernames must be 3–32 letters, numbers, dots, dashes, or underscores. Passwords must contain at least 8 characters. New accounts are saved to the configured database and start with a zero balance. Each account is assigned a unique 12-digit account number that customers can share to receive an internal prototype transfer.

Signed-in customers can transfer simulated balances to another Demel Bank customer using that customer's 12-digit account number. Each transfer atomically debits and credits the two prototype accounts, and records the matching sender and recipient entries in their activity histories. Transfers cannot be made to the sender's own account, an administrator, or an account that does not exist. This feature does not connect to other banks or move real money.

Existing SQLite and PostgreSQL databases are upgraded on app startup: customers receive account numbers, and transaction records gain counterparty metadata. Account numbers are generated once per existing user and retained on future restarts.

## Tests

Run the focused account-number, transfer, migration, and admin-provisioning tests:

```powershell
python -m unittest discover -s tests -v
```

## Administrator access

No public administrator credentials are created by default. To provision an administrator, set both `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the environment before launching the app. On startup, the configured password is hashed and applied to that administrator, so changing the environment password also rotates the existing administrator's password. The legacy `DEMO_ADMIN_USERNAME` and `DEMO_ADMIN_PASSWORD` variables are also recognized. If the configured username already belongs to a personal account, startup fails clearly instead of changing that account's role.

## Deploy on Render

Render's web-service filesystem is temporary, so local SQLite is for development only. For persistent hosted records:

1. Push this project to GitHub and create the web service using **New +** → **Blueprint**. Render reads `render.yaml`.
2. Create a PostgreSQL database in the **Oregon** region and copy its internal database URL.
3. Set `DATABASE_URL` in the web service's environment settings and redeploy.
4. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the web service's environment before startup if administrator access is needed. The included Blueprint declares both variables as required manual values. If an existing service was created before these settings were added, set both variables in its Render **Environment** settings and redeploy. The configured credentials apply to the hosted database only; a local SQLite admin account is not copied to Render.
5. Visitors can register their personal accounts from the sign-in page.

The app uses SQLite when `DATABASE_URL` is unset and PostgreSQL when it is set. A local SQLite database is not automatically copied to PostgreSQL. Existing records in a database are preserved.

## Notes

- Balances use integer cents to avoid floating-point arithmetic errors.
- SQLAlchemy supports both SQLite locally and PostgreSQL when deployed.
- Sessions use `SECRET_KEY`; Render generates one through `render.yaml`, and local runs generate a random key when it is unset.
- Set `DATABASE_PATH` to change the local SQLite file location.
- This is a banking interface prototype, not a financial service. Do not use real funds or sensitive personal information.
