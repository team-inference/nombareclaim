"""
Ensures required env vars are set BEFORE any test module imports
anything from `app` (and therefore `app.config`).

Why this file exists: app.config.Settings is instantiated once, at
import time, as a module-level singleton. pytest collects test files
alphabetically, and a test file that imports `app.*` without first
setting these env vars will "poison" that singleton for the rest of
the test session — later test files' os.environ.setdefault() calls
have no effect, since Settings.__init__ already ran and cached values.
conftest.py is loaded by pytest before any test module in this
directory is collected/imported, so setting them here guarantees
correct values regardless of which test file happens to import `app`
first.
"""
import os

os.environ.setdefault("NOMBA_WEBHOOK_SIGNATURE_KEY", "test_webhook_secret_123")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_nombareclaim.db")
os.environ.setdefault("NOMBA_ACCOUNT_ID", "test-account")
os.environ.setdefault("NOMBA_SUBACCOUNT_ID", "test-subaccount")
