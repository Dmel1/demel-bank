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

Visitors can create a personal account from the sign-in page. Usernames must be 3–32 letters, numbers, dots, dashes, or underscores. Passwords must contain at least 8 characters. New accounts are saved to the configured database and start with a zero balance.

## Administrator access

No public administrator credentials are created by default. To provision an administrator on first startup, set both `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the environment before launching the app. Existing administrator records are not overwritten on later starts. The legacy `DEMO_ADMIN_USERNAME` and `DEMO_ADMIN_PASSWORD` variables are also recognized.

## Deploy on Render

Render's web-service filesystem is temporary, so local SQLite is for development only. For persistent hosted records:

1. Push this project to GitHub and create the web service using **New +** → **Blueprint**. Render reads `render.yaml`.
2. Create a PostgreSQL database in the **Oregon** region and copy its internal database URL.
3. Set `DATABASE_URL` in the web service's environment settings and redeploy.
4. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before the first startup if administrator access is needed.
5. Visitors can register their personal accounts from the sign-in page.

The app uses SQLite when `DATABASE_URL` is unset and PostgreSQL when it is set. A local SQLite database is not automatically copied to PostgreSQL. Existing records in a database are preserved.

## Notes

- Balances use integer cents to avoid floating-point arithmetic errors.
- SQLAlchemy supports both SQLite locally and PostgreSQL when deployed.
- Sessions use `SECRET_KEY`; Render generates one through `render.yaml`, and local runs generate a random key when it is unset.
- Set `DATABASE_PATH` to change the local SQLite file location.
- This is a banking interface prototype, not a financial service. Do not use real funds or sensitive personal information.
