"""Authentication and ownership boundaries for the first application role."""
import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError as exc:
    raise unittest.SkipTest("Install the web extra to run web tests: uv sync --extra web") from exc

from survey_coder.web import LOGIN_COOKIE, SESSION_COOKIE, create_app, main
from survey_coder.web_store import LOGIN_ATTEMPTS, password_matches, token_hash


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "web.sqlite3"
        self.app = create_app(self.database, origin="http://testserver", secure_cookie=False)
        self.store = self.app.state.store
        self.alice = self.store.add_user("Alice", "alice-password-123")
        self.bob = self.store.add_user("bob", "bob-password-456")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        with self.store.connect() as db:
            db.executemany("INSERT INTO runs VALUES (?, ?, ?, ?)", [
                ("alice-run", self.alice, "Alice's private run", "completed"),
                ("bob-run", self.bob, "Bob's private run", "completed"),
            ])
            db.executemany("INSERT INTO files VALUES (?, ?, ?, ?)", [
                ("alice-file", "alice-run", "results.csv", b"alice data"),
                ("bob-file", "bob-run", "results.csv", b"bob data"),
            ])

    def login(self, username="alice", password="alice-password-123", client=None):
        client = client or self.client
        page = client.get("/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        return client.post("/login", data={"username": username, "password": password, "csrf": csrf},
                           follow_redirects=False)

    def test_sign_in_single_role_and_sign_out(self):
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)
        response = self.login(username=" ALICE ")
        self.assertEqual(response.status_code, 303)
        me = self.client.get("/api/me").json()
        self.assertEqual(me, {"id": self.alice, "username": "alice", "role": "survey_user"})
        home = self.client.get("/")
        self.assertIn("Survey user", home.text)
        self.assertNotIn("Bob's private", home.text)
        csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
        old_cookie = self.client.cookies.get(SESSION_COOKIE)
        self.assertEqual(self.client.post("/logout", data={"csrf": csrf}, follow_redirects=False).status_code, 303)
        self.client.cookies.set(SESSION_COOKIE, old_cookie)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_anonymous_requests_cannot_read_resources(self):
        for url in ("/api/me", "/api/runs", "/api/runs/alice-run", "/api/files/alice-file"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_user_sees_only_own_runs_and_downloads(self):
        self.login()
        # Query parameters cannot replace the authenticated owner.
        self.assertEqual([r["id"] for r in self.client.get(f"/api/runs?owner_id={self.bob}").json()], ["alice-run"])
        self.assertEqual(self.client.get("/api/runs/alice-run").status_code, 200)
        download = self.client.get("/api/files/alice-file")
        self.assertEqual(download.content, b"alice data")
        self.assertIn("attachment", download.headers["content-disposition"])
        for path in ("/api/runs/bob-run", "/api/files/bob-file", "/api/runs/missing", "/api/files/missing"):
            self.assertEqual(self.client.get(path).status_code, 404)
        with TestClient(self.app) as bob:
            self.login("bob", "bob-password-456", bob)
            self.assertEqual(bob.get("/api/runs/alice-run").status_code, 404)
            self.assertEqual(bob.get("/api/files/alice-file").status_code, 404)
            self.assertEqual(bob.get("/api/files/bob-file").content, b"bob data")

    def test_unknown_and_incorrect_credentials_have_same_error(self):
        wrong = self.login(password="wrong")
        unknown = self.login(username="unknown", password="wrong")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertIn("Account name or password is incorrect.", wrong.text)
        self.assertIn("Account name or password is incorrect.", unknown.text)
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE))

    def test_login_and_logout_require_csrf_and_same_origin(self):
        self.assertEqual(self.client.post("/login", data={"username": "alice"}).status_code, 403)
        self.client.get("/login")
        csrf = self.client.cookies.get(LOGIN_COOKIE)
        self.assertEqual(self.client.post("/login", data={"csrf": csrf}, headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/login", data={"csrf": "☃"}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post("/logout", data={"csrf": "wrong"}).status_code, 403)
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_expired_forged_and_disabled_sessions_are_rejected(self):
        self.login()
        token = self.client.cookies.get(SESSION_COOKIE)
        with self.store.connect() as db:
            db.execute("UPDATE sessions SET expires = ?", (time.time() - 1,))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token + "forged")
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.login()
        self.assertTrue(self.store.disable_user("alice"))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.login().status_code, 401)

    def test_new_login_rotates_session_and_restart_retains_it(self):
        self.login()
        old_token = self.client.cookies.get(SESSION_COOKIE)
        self.login()
        token = self.client.cookies.get(SESSION_COOKIE)
        self.assertNotEqual(old_token, token)
        self.assertIsNone(self.store.session(old_token))
        restarted = create_app(self.database, origin="http://testserver", secure_cookie=False)
        with TestClient(restarted) as client:
            client.cookies.set(SESSION_COOKIE, token)
            self.assertEqual(client.get("/api/me").json()["id"], self.alice)

    def test_passwords_and_session_tokens_not_stored_in_plaintext(self):
        self.login()
        token = self.client.cookies.get(SESSION_COOKIE)
        with self.store.connect() as db:
            user = db.execute("SELECT password_hash FROM users WHERE id = ?", (self.alice,)).fetchone()
            session = db.execute("SELECT digest FROM sessions").fetchone()
        self.assertNotEqual(user[0], "alice-password-123")
        self.assertTrue(password_matches("alice-password-123", user[0]))
        self.assertEqual(session[0], token_hash(token))
        self.assertNotEqual(session[0], token)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_attempt_limit_survives_application_restart(self):
        for _ in range(LOGIN_ATTEMPTS):
            self.assertTrue(self.store.allow_login("blocked-client"))
        restarted = create_app(self.database, origin="http://testserver", secure_cookie=False)
        self.assertFalse(restarted.state.store.allow_login("blocked-client"))
        with self.store.connect() as db:
            db.execute("UPDATE login_attempts SET attempted = ?", (time.time() - 61,))
        self.assertTrue(self.store.allow_login("blocked-client"))
        # Exercise the HTTP response too.
        for _ in range(LOGIN_ATTEMPTS):
            self.store.allow_login("testclient")
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_secure_cookie_and_response_headers(self):
        secure_app = create_app(self.database, origin="https://testserver")
        with TestClient(secure_app, base_url="https://testserver") as client:
            response = self.login(client=client)
            cookie = response.headers.get("set-cookie")
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=strict", cookie)
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(response.headers["referrer-policy"], "same-origin")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertEqual(client.get("/api/me").status_code, 200)
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/", headers={"host": "evil.example"}).status_code, 400)
        with self.assertRaises(ValueError):
            create_app(self.database, origin="http://public.example", secure_cookie=False)

    def test_account_validation_and_html_escaping(self):
        with self.assertRaises(ValueError):
            self.store.add_user("short", "short")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_user("ALICE", "another-password")
        self.login()
        with self.store.connect() as db:
            db.execute("UPDATE runs SET name = ? WHERE id = ?", ("<script>bad()</script>", "alice-run"))
        html = self.client.get("/").text
        self.assertNotIn("<script>bad()", html)
        self.assertIn("&lt;script&gt;", html)

    def test_command_line_provisions_and_disables_single_role(self):
        argv = ["survey-coder-web", "--database", str(self.database)]
        with patch("sys.argv", argv + ["create-user", "charlie"]), patch(
            "survey_coder.web.getpass.getpass", return_value="charlie-password-123"
        ), patch("builtins.print") as output:
            main()
        user = self.store.authenticate("charlie", "charlie-password-123")
        self.assertIsNotNone(user)
        output.assert_called_once_with("Survey user created.")
        token, _ = self.store.new_session(user["id"])
        with patch("sys.argv", argv + ["disable-user", "charlie"]), patch("builtins.print"):
            main()
        self.assertIsNone(self.store.session(token))

    def test_password_reset_revokes_only_target_sessions_and_preserves_resources(self):
        self.login()
        bob_token, _ = self.store.new_session(self.bob)
        self.assertTrue(self.store.reset_password(" ALICE ", "new-alice-password"))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertIsNotNone(self.store.session(bob_token))
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(password="new-alice-password").status_code, 303)
        self.assertEqual(self.client.get("/api/me").json()["id"], self.alice)
        self.assertEqual(self.client.get("/api/files/alice-file").content, b"alice data")
        self.assertEqual(self.client.get("/api/runs/alice-run").status_code, 200)

    def test_reset_validation_and_disabled_account_require_explicit_enable(self):
        self.login()
        with self.assertRaises(ValueError):
            self.store.reset_password("alice", "short")
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.assertFalse(self.store.reset_password("missing", "new-valid-password"))
        self.store.disable_user("alice")
        self.store.reset_password("alice", "new-valid-password")
        self.assertIsNone(self.store.authenticate("alice", "new-valid-password"))
        argv = ["survey-coder-web", "--database", str(self.database), "reset-password", "alice", "--enable"]
        with patch("sys.argv", argv), patch("survey_coder.web.getpass.getpass", return_value="enabled-account-password"), patch("builtins.print") as output:
            main()
        output.assert_called_once_with("Password reset; existing sessions revoked.")
        self.assertEqual(self.login(password="enabled-account-password").status_code, 303)

    def test_reset_cli_checks_confirmation_before_changing_password(self):
        argv = ["survey-coder-web", "--database", str(self.database), "reset-password", "alice"]
        with patch("sys.argv", argv), patch("survey_coder.web.getpass.getpass", side_effect=["new-valid-password", "different-password"]), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                main()
        self.assertIsNotNone(self.store.authenticate("alice", "alice-password-123"))

    def test_disabled_reset_warns_and_enable_command_preserves_password_and_data(self):
        self.store.disable_user("alice")
        argv = ["survey-coder-web", "--database", str(self.database)]
        with patch("sys.argv", argv + ["reset-password", "alice"]), patch(
            "survey_coder.web.getpass.getpass", return_value="new-valid-password"
        ), patch("builtins.print") as output:
            main()
        self.assertIn("still disabled", output.call_args.args[0])
        self.assertFalse(self.store.account_enabled("alice"))
        with patch("sys.argv", argv + ["enable-user", "ALICE"]), patch("builtins.print"):
            main()
        self.assertTrue(self.store.account_enabled("alice"))
        self.assertEqual(self.login(password="new-valid-password").status_code, 303)
        self.assertEqual(self.client.get("/api/files/alice-file").content, b"alice data")
        self.assertFalse(self.store.enable_user("missing"))

    def test_local_login_and_logout_work_on_both_loopback_hostnames(self):
        app = create_app(self.database, origin="http://127.0.0.1:8000", secure_cookie=False)
        for host in ("localhost", "127.0.0.1"):
            origin = f"http://{host}:8000"
            with self.subTest(host=host), TestClient(app, base_url=origin) as client:
                login = client.get("/login")
                self.assertEqual(login.status_code, 200)
                csrf = client.cookies.get(LOGIN_COOKIE)
                data = {"username": "alice", "password": "alice-password-123", "csrf": csrf}
                for other in ("http://evil.example:8000", "http://localhost:9000",
                              "http://127.0.0.1:8000" if host == "localhost" else "http://localhost:8000"):
                    rejected = client.post("/login", data=data, headers={"origin": other})
                    self.assertEqual(rejected.status_code, 403)
                signed_in = client.post("/login", data=data, headers={"origin": origin}, follow_redirects=False)
                self.assertEqual(signed_in.status_code, 303)
                home = client.get("/")
                self.assertIn("Survey user", home.text)
                csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
                signed_out = client.post("/logout", data={"csrf": csrf}, headers={"origin": origin}, follow_redirects=False)
                self.assertEqual(signed_out.status_code, 303)
                self.assertEqual(client.get("/api/me").status_code, 401)
