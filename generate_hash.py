"""
Generate a password hash for the doctor account.

    python generate_hash.py

Paste the printed value into the DOCTOR_PASSWORD_HASH environment variable on
your host (Render dashboard / .env). The plaintext password is never stored.
"""
import getpass
from werkzeug.security import generate_password_hash

pw = getpass.getpass("Doctor password: ")
if pw != getpass.getpass("Confirm: "):
    raise SystemExit("Passwords do not match")
print("\nDOCTOR_PASSWORD_HASH=" + generate_password_hash(pw))
