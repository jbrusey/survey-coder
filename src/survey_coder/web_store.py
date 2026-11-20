"""Persistent identities, sessions and owner-scoped web resources."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 60
LOGIN_ATTEMPTS = 10


def validate_password(password: str):
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Use a password between 12 and 1024 characters.")


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt${salt}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, salt, _ = stored.split("$")
        return algorithm == "scrypt" and hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (client TEXT NOT NULL, attempted REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_client ON login_attempts(client, attempted);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner_id);
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    filename TEXT NOT NULL, content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coding_jobs (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), workbook_id TEXT NOT NULL,
                    codebook_id TEXT NOT NULL, spec TEXT, counts TEXT NOT NULL DEFAULT '{}',
                    error TEXT, lease_owner TEXT, lease_until REAL,
                    result_id TEXT, manifest_id TEXT, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coding_rows (
                    run_id TEXT NOT NULL REFERENCES runs(id), row_index INTEGER NOT NULL,
                    state TEXT NOT NULL, labels TEXT, error TEXT,
                    PRIMARY KEY (run_id, row_index)
                );
            """)
        path.chmod(0o600)
        # Equal password work for unknown and known users.
        self.dummy_password = password_hash(secrets.token_urlsafe(32))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_user(self, username: str, password: str) -> str:
        username = username.strip().casefold()
        if not username or len(username) > 254:
            raise ValueError("Use an account name between 1 and 254 characters.")
        validate_password(password)
        user_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO users(id, username, password_hash) VALUES (?, ?, ?)",
                       (user_id, username, password_hash(password)))
        return user_id

    def reset_password(self, username: str, password: str, *, enable: bool = False) -> bool:
        """Replace credentials and revoke sessions without changing resource ownership."""
        validate_password(password)
        hashed = password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT id FROM users WHERE username = ?",
                              (username.strip().casefold(),)).fetchone()
            if user is None:
                return False
            db.execute("UPDATE users SET password_hash = ?, active = CASE WHEN ? THEN 1 ELSE active END WHERE id = ?",
                       (hashed, enable, user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        return True

    def disable_user(self, username: str) -> bool:
        with self.connect() as db:
            user = db.execute("SELECT id FROM users WHERE username = ?",
                              (username.strip().casefold(),)).fetchone()
            if user is None:
                return False
            db.execute("UPDATE users SET active = 0 WHERE id = ?", (user["id"],))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        return True

    def account_enabled(self, username: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT active FROM users WHERE username = ?",
                             (username.strip().casefold(),)).fetchone()
        return bool(row and row["active"])

    def enable_user(self, username: str) -> bool:
        with self.connect() as db:
            return db.execute("UPDATE users SET active = 1 WHERE username = ?",
                              (username.strip().casefold(),)).rowcount == 1

    def allow_login(self, client: str) -> bool:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE attempted < ?", (now - LOGIN_WINDOW_SECONDS,))
            count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE client = ?", (client,)).fetchone()[0]
            if count >= LOGIN_ATTEMPTS:
                return False
            db.execute("INSERT INTO login_attempts VALUES (?, ?)", (client, now))
        return True

    def authenticate(self, username: str, password: str):
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?",
                              (username.strip().casefold(),)).fetchone()
        matches = password_matches(password, user["password_hash"] if user else self.dummy_password)
        return user if user and user["active"] and matches else None

    def new_session(self, user_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (token_hash(token), user_id, csrf, time.time() + SESSION_SECONDS))
        return token, csrf

    def session(self, token: str):
        with self.connect() as db:
            return db.execute("""SELECT users.id, users.username, sessions.csrf FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.digest = ? AND sessions.expires > ? AND users.active = 1""",
                              (token_hash(token), time.time())).fetchone()

    def revoke_session(self, token: str):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest = ?", (token_hash(token),))

    def runs(self, user_id: str):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, name, status FROM runs WHERE owner_id = ? ORDER BY rowid DESC", (user_id,))]

    def run(self, user_id: str, run_id: str):
        with self.connect() as db:
            row = db.execute("SELECT id, name, status FROM runs WHERE id = ? AND owner_id = ?",
                             (run_id, user_id)).fetchone()
        return dict(row) if row else None

    def file(self, user_id: str, file_id: str):
        with self.connect() as db:
            return db.execute("""SELECT files.filename, files.content FROM files
                JOIN runs ON runs.id = files.run_id WHERE files.id = ? AND runs.owner_id = ?""",
                              (file_id, user_id)).fetchone()

    def draft(self, owner: str, name: str, workbook: bytes, codebook: bytes) -> str:
        run_id, workbook_id, codebook_id = (str(uuid.uuid4()) for _ in range(3))
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, 'draft')", (run_id, owner, name))
            db.executemany("INSERT INTO files VALUES (?, ?, ?, ?)", [
                (workbook_id, run_id, "source.xlsx", workbook),
                (codebook_id, run_id, "codebook.md", codebook),
            ])
            db.execute("""INSERT INTO coding_jobs(run_id, workbook_id, codebook_id, created)
                VALUES (?, ?, ?, ?)""", (run_id, workbook_id, codebook_id, time.time()))
        return run_id

    def job(self, owner: str, run_id: str):
        with self.connect() as db:
            row = db.execute("""SELECT coding_jobs.*, runs.status, runs.name FROM coding_jobs
                JOIN runs ON runs.id = coding_jobs.run_id WHERE runs.id = ? AND runs.owner_id = ?""",
                             (run_id, owner)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["counts"] = json.loads(result["counts"])
        result["spec"] = json.loads(result["spec"]) if result["spec"] else None
        return result

    def submit(self, owner: str, run_id: str, spec: dict) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner)).fetchone()
            if row is None:
                return False
            if row["status"] != "draft":
                return True  # A repeated form submission returns the existing run.
            if not db.execute("SELECT 1 FROM coding_jobs WHERE run_id = ?", (run_id,)).fetchone():
                return False
            eligible = sum(spec["eligible"])
            # Older persisted specifications only distinguish eligible and blank.
            row_states = spec.get("row_states", ["pending" if value else "blank" for value in spec["eligible"]])
            counts = {"total": len(spec["data"]), "eligible": eligible, "completed": 0,
                      "failed": 0, "blank": row_states.count("blank"),
                      "non_response": row_states.count("non_response"), "remaining": eligible}
            db.execute("UPDATE coding_jobs SET spec = ?, counts = ? WHERE run_id = ?",
                       (json.dumps(spec), json.dumps(counts), run_id))
            db.executemany("INSERT INTO coding_rows(run_id, row_index, state) VALUES (?, ?, ?)",
                           [(run_id, index, state) for index, state in enumerate(row_states)])
            db.execute("UPDATE runs SET status = 'queued' WHERE id = ?", (run_id,))
        return True

    def retry_failed(self, owner: str, run_id: str) -> bool:
        """Atomically queue only unsuccessful rows and fence the previous worker."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("""SELECT coding_jobs.*, runs.status FROM coding_jobs
                JOIN runs ON runs.id = coding_jobs.run_id
                WHERE run_id = ? AND owner_id = ?""", (run_id, owner)).fetchone()
            if job is None or job["status"] not in ("failed", "completed_with_errors") or not job["spec"]:
                return False
            remaining = db.execute("""SELECT COUNT(*) FROM coding_rows
                WHERE run_id = ? AND state IN ('failed', 'pending')""", (run_id,)).fetchone()[0]
            if not remaining:
                return False
            spec = json.loads(job["spec"])
            spec["retry_count"] = spec.get("retry_count", 0) + 1
            counts = json.loads(job["counts"])
            counts.update(failed=0, remaining=remaining)
            db.execute("""UPDATE coding_rows SET state = 'pending', labels = NULL, error = NULL
                WHERE run_id = ? AND state = 'failed'""", (run_id,))
            db.execute("""UPDATE coding_jobs SET spec = ?, counts = ?, error = NULL,
                lease_owner = NULL, lease_until = NULL, result_id = NULL, manifest_id = NULL
                WHERE run_id = ?""", (json.dumps(spec), json.dumps(counts), run_id))
            db.execute("UPDATE runs SET status = 'queued' WHERE id = ?", (run_id,))
        return True

    def claim(self, worker: str):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT coding_jobs.*, runs.owner_id, runs.name FROM coding_jobs
                JOIN runs ON runs.id = coding_jobs.run_id JOIN users ON users.id = runs.owner_id
                WHERE users.active = 1 AND (runs.status = 'queued' OR
                    (runs.status = 'running' AND coding_jobs.lease_until < ?))
                ORDER BY coding_jobs.created LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE coding_jobs SET lease_owner = ?, lease_until = ? WHERE run_id = ?",
                       (worker, now + 60, row["run_id"]))
            db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (row["run_id"],))
        return {**dict(row), "spec": json.loads(row["spec"])}

    def renew(self, run_id: str, worker: str) -> bool:
        with self.connect() as db:
            return db.execute("""UPDATE coding_jobs SET lease_until = ? WHERE run_id = ? AND lease_owner = ?
                AND EXISTS (SELECT 1 FROM runs JOIN users ON users.id = runs.owner_id
                            WHERE runs.id = ? AND users.active = 1)""",
                              (time.time() + 60, run_id, worker, run_id)).rowcount == 1

    def pending(self, run_id: str) -> list[int]:
        with self.connect() as db:
            return [row[0] for row in db.execute(
                "SELECT row_index FROM coding_rows WHERE run_id = ? AND state = 'pending' ORDER BY row_index", (run_id,))]

    def checkpoint(self, run_id: str, worker: str, results: list[tuple]) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM coding_jobs WHERE run_id = ? AND lease_owner = ? AND lease_until > ?",
                              (run_id, worker, time.time())).fetchone():
                return False
            db.executemany("""UPDATE coding_rows SET state = ?, labels = ?, error = ?
                WHERE run_id = ? AND row_index = ? AND state = 'pending'""",
                           [(state, json.dumps(labels) if labels is not None else None, error, run_id, index)
                            for index, state, labels, error in results])
            counts = json.loads(db.execute("SELECT counts FROM coding_jobs WHERE run_id = ?", (run_id,)).fetchone()[0])
            statuses = dict(db.execute("SELECT state, COUNT(*) FROM coding_rows WHERE run_id = ? GROUP BY state", (run_id,)))
            counts.update(completed=statuses.get("coded", 0), failed=statuses.get("failed", 0), remaining=statuses.get("pending", 0))
            db.execute("UPDATE coding_jobs SET counts = ? WHERE run_id = ?", (json.dumps(counts), run_id))
        return True

    def row_results(self, run_id: str):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM coding_rows WHERE run_id = ? ORDER BY row_index", (run_id,))]

    def finish(self, run_id: str, worker: str, csv: bytes, manifest: bytes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT counts FROM coding_jobs WHERE run_id = ? AND lease_owner = ? AND lease_until > ?",
                             (run_id, worker, time.time())).fetchone()
            if job is None:
                return False
            counts = json.loads(job["counts"])
            if counts["remaining"]:
                raise ValueError("Cannot finish a job with unprocessed rows.")
            result_id, manifest_id = str(uuid.uuid4()), str(uuid.uuid4())
            db.executemany("INSERT INTO files VALUES (?, ?, ?, ?)", [
                (result_id, run_id, "coded_results.csv", csv), (manifest_id, run_id, "run_manifest.json", manifest)])
            state = "completed" if not counts["failed"] else ("completed_with_errors" if counts["completed"] else "failed")
            db.execute("UPDATE runs SET status = ? WHERE id = ?", (state, run_id))
            db.execute("UPDATE coding_jobs SET result_id = ?, manifest_id = ?, lease_until = NULL WHERE run_id = ?",
                       (result_id, manifest_id, run_id))
        return True

    def fail(self, run_id: str, worker: str, error: str):
        with self.connect() as db:
            if db.execute("UPDATE coding_jobs SET error = ?, lease_until = NULL WHERE run_id = ? AND lease_owner = ?",
                          (error, run_id, worker)).rowcount:
                db.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (run_id,))
