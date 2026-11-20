"""Single-role browser workflow for spreadsheet coding."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import getpass
from html import escape
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .web_store import SESSION_SECONDS, Store

SESSION_COOKIE = "survey_session"
LOGIN_COOKIE = "survey_login_csrf"

STYLE = """
:root{font:16px/1.6 system-ui,sans-serif;color:#243247;background:#f3f6fb}
*{box-sizing:border-box}body{margin:0}header{background:#153858;color:white;padding:1rem 5vw}
main{max-width:900px;margin:3rem auto;padding:0 1.3rem}.card{background:white;border:1px solid #dce4ef;border-radius:12px;padding:2rem}
h1{font-size:1.8rem;line-height:1.2;margin-top:0}label{display:block;font-weight:600;margin:1rem 0 .35rem}
input{width:100%;padding:.7rem;border:1px solid #8796a8;border-radius:5px;font:inherit}
select,textarea{width:100%;padding:.7rem;border:1px solid #8796a8;border-radius:5px;font:inherit;background:white}
.table-scroll{overflow:auto;max-height:300px;margin:1.5rem 0}td{max-width:360px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fb;padding:1rem}
h2{font-size:1.2rem;margin-top:1.8rem}.counts{display:flex;flex-wrap:wrap;gap:1.5rem}.counts dt{color:#52637a}.counts dd{font-size:1.6rem;margin:0}
.button{display:inline-block;background:#1759b5;color:white;padding:.65rem 1rem;border-radius:5px;text-decoration:none}button:disabled{opacity:.6;cursor:not-allowed}
button{background:#1759b5;color:white;border:0;border-radius:5px;padding:.65rem 1.1rem;font:600 1rem system-ui;cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #dc8b16;outline-offset:3px}
a{color:#1759b5}.muted{color:#52637a}.error{padding:.8rem;border-left:4px solid #a52834;background:#fff0f1}
.topline{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}.login{max-width:480px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.8rem;border-bottom:1px solid #dce4ef}
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · Survey Coder</title>
<style>{STYLE}</style></head><body><header>Survey Coder</header><main id="main">{body}</main></body></html>""")


def create_app(database: Path, *, origin: str, secure_cookie: bool = True,
               config_path: str | None = None, start_worker: bool = False) -> FastAPI:
    from .web_coding import configuration_problem, server_config
    config = server_config(config_path)
    parsed = urlsplit(origin)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Provide a public origin such as https://survey.example.org.")
    if not secure_cookie and (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "testserver")):
        raise ValueError("HTTP session cookies are restricted to local development.")
    if secure_cookie and parsed.scheme != "https":
        raise ValueError("Secure cookies require an HTTPS public origin.")
    origin = origin.rstrip("/")
    # Local browsers commonly use either spelling of the loopback address.
    # Accept both hosts, but require forms to come from the host serving them.
    local_origins = {origin}
    allowed_hosts = [parsed.hostname]
    if not secure_cookie and parsed.hostname in ("127.0.0.1", "localhost"):
        port = f":{parsed.port}" if parsed.port is not None else ""
        local_origins = {f"http://{host}{port}" for host in ("127.0.0.1", "localhost")}
        allowed_hosts = ["127.0.0.1", "localhost"]
    store = Store(Path(database))
    @asynccontextmanager
    async def lifespan(app):
        process = None
        if start_worker:
            process = subprocess.Popen([sys.executable, "-m", "survey_coder.web_worker",
                                        "--database", str(Path(database).resolve())])
        app.state.worker_process = process
        try:
            yield
        finally:
            if process is not None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)

    app = FastAPI(title="Survey Coder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # no-referrer makes browsers send Origin: null for HTML form POSTs,
        # causing our own sign-in and sign-out forms to fail the origin check.
        # same-origin retains the origin locally without leaking it off-site.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
        if secure_cookie:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def set_cookie(response: Response, name: str, value: str, age: int):
        response.set_cookie(name, value, max_age=age, httponly=True, secure=secure_cookie,
                            samesite="strict", path="/")

    def current_user(request: Request):
        user = store.session(request.cookies.get(SESSION_COOKIE, ""))
        if user is None:
            raise HTTPException(401, "Sign in to continue.")
        return user

    def check_origin(request: Request):
        expected_origin = origin
        if len(local_origins) > 1:
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (expected_origin not in local_origins
                or request.headers.get("origin", expected_origin) != expected_origin
                or request.headers.get("sec-fetch-site") == "cross-site"):
            raise HTTPException(403, "Cross-site request rejected.")

    async def form(request: Request, max_fields: int = 5) -> dict[str, str]:
        check_origin(request)
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "Submit the sign-in form.")
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(413, "Form is too large.")
        try:
            values = parse_qs(body.decode("utf-8"), max_num_fields=max_fields)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Invalid form.") from None
        if any(len(value) != 1 for value in values.values()):
            raise HTTPException(400, "Duplicate form fields.")
        return {key: value[0] for key, value in values.items()}

    def check_csrf(expected: str, received: str):
        if not expected or not hmac.compare_digest(expected.encode(), received.encode()):
            raise HTTPException(403, "Form expired. Reload the page and try again.")

    def login_page(error: str = "", status: int = 200):
        csrf = secrets.token_urlsafe(32)
        warning = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
        response = page("Sign in", f"""<section class="card login"><h1>Sign in</h1>
<p class="muted">Use the account supplied by your service maintainer.</p>{warning}
<form action="/login" method="post"><input type="hidden" name="csrf" value="{csrf}">
<label for="username">Account name</label><input id="username" name="username" autocomplete="username" maxlength="254" required>
<label for="password">Password</label><input type="password" id="password" name="password" autocomplete="current-password" maxlength="1024" required>
<p><button type="submit">Sign in</button></p></form></section>""")
        response.status_code = status
        set_cookie(response, LOGIN_COOKIE, csrf, 600)
        return response

    @app.get("/health/live")
    def health():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        process = getattr(app.state, "worker_process", None)
        if configuration_problem(config) or process is None or process.poll() is not None:
            raise HTTPException(503, "Coding worker or model configuration is unavailable.")
        return {"status": "ready", "provider_connectivity": "Verified when a coding request succeeds."}

    @app.get("/login")
    def login_get():
        return login_page()

    @app.post("/login")
    async def login_post(request: Request):
        values = await form(request)
        check_csrf(request.cookies.get(LOGIN_COOKIE, ""), values.get("csrf", ""))
        client = request.client.host if request.client else "unknown"
        if not store.allow_login(client):
            response = login_page("Too many sign-in attempts. Try again in a minute.", 429)
            response.headers["Retry-After"] = "60"
            return response
        username, password = values.get("username", ""), values.get("password", "")
        if len(username) > 254 or len(password) > 1024:
            return login_page("Account name or password is incorrect.", 401)
        user = store.authenticate(username, password)
        if user is None:
            return login_page("Account name or password is incorrect.", 401)
        # Rotate existing credentials when signing in again.
        store.revoke_session(request.cookies.get(SESSION_COOKIE, ""))
        token, _ = store.new_session(user["id"])
        response = RedirectResponse("/", status_code=303)
        set_cookie(response, SESSION_COOKIE, token, SESSION_SECONDS)
        response.delete_cookie(LOGIN_COOKIE, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request, user=Depends(current_user)):
        values = await form(request)
        check_csrf(user["csrf"], values.get("csrf", ""))
        store.revoke_session(request.cookies.get(SESSION_COOKIE, ""))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        user = store.session(request.cookies.get(SESSION_COOKIE, ""))
        if user is None:
            return RedirectResponse("/login", status_code=303)
        runs = store.runs(user["id"])
        if runs:
            rows = "".join(f'<tr><td><a href="/runs/{escape(run["id"])}">{escape(run["name"])}</a></td><td>{escape(run["status"].replace("_", " "))}</td></tr>' for run in runs)
            content = f'<table><thead><tr><th scope="col">Run</th><th scope="col">Status</th></tr></thead><tbody>{rows}</tbody></table>'
        else:
            content = '<p>No coding runs yet.</p>'
        return page("My runs", f"""<section class="card"><div class="topline"><div>
<h1>My runs</h1><p class="muted">{escape(user['username'])} · Survey user</p></div>
<form action="/logout" method="post"><input type="hidden" name="csrf" value="{user['csrf']}">
<button type="submit">Sign out</button></form></div><p><a class="button" href="/runs/new">New coding run</a></p>{content}</section>""")

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return {"id": user["id"], "username": user["username"], "role": "survey_user"}

    @app.get("/api/runs")
    def runs(user=Depends(current_user)):
        return store.runs(user["id"])

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, user=Depends(current_user)):
        result = store.run(user["id"], run_id)
        if result is None:
            raise HTTPException(404, "Run not found.")
        job = store.job(user["id"], run_id)
        if job:
            result.update(counts=job["counts"], error=job["error"], result_id=job["result_id"], manifest_id=job["manifest_id"])
        return result

    @app.get("/api/files/{file_id}")
    def download(file_id: str, user=Depends(current_user)):
        result = store.file(user["id"], file_id)
        if result is None:
            raise HTTPException(404, "File not found.")
        return Response(bytes(result["content"]), media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(result["filename"], safe="")})

    from .web_routes import register_coding_routes
    register_coding_routes(app, store, current_user, check_csrf, check_origin, form, page, config)
    return app


def main():
    parser = argparse.ArgumentParser(description="Survey Coder single-role web service")
    parser.add_argument("--database", type=Path, default=Path(os.environ.get(
        "SURVEY_CODER_WEB_DATABASE", str(Path.home() / ".survey-coder" / "web.sqlite3"))))
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("create-user", help="Provision a Survey user; passwords are prompted")
    add.add_argument("username")
    reset = commands.add_parser("reset-password", help="Reset a password while preserving the user's runs and files")
    reset.add_argument("username")
    reset.add_argument("--enable", action="store_true", help="Also re-enable a disabled account")
    disable = commands.add_parser("disable-user", help="Disable an account and revoke its sessions")
    disable.add_argument("username")
    enable = commands.add_parser("enable-user", help="Re-enable an account without changing its password or data")
    enable.add_argument("username")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--origin", help="Public HTTPS origin when serving behind a reverse proxy")
    serve.add_argument("--config", default=os.environ.get("SURVEY_CODER_WEB_CONFIG"),
                       help="Server analysis setup JSON; uses its context and llm settings")
    serve.add_argument("--local-http", action="store_true", help="Allow HTTP cookies on loopback for development")
    args = parser.parse_args()
    if args.command in ("create-user", "reset-password"):
        password = getpass.getpass("Password (at least 12 characters): ")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords do not match.")
        try:
            store = Store(args.database)
            if args.command == "create-user":
                store.add_user(args.username, password)
            elif not store.reset_password(args.username, password, enable=args.enable):
                parser.error("Account not found. Check the account name and --database path.")
        except (ValueError, sqlite3.IntegrityError) as exc:
            parser.error("Account already exists." if isinstance(exc, sqlite3.IntegrityError) else str(exc))
        print("Survey user created." if args.command == "create-user" else "Password reset; existing sessions revoked.")
        if args.command == "reset-password" and not store.account_enabled(args.username):
            print("The account is still disabled. Run enable-user for this account before signing in.")
    elif args.command == "disable-user":
        if not Store(args.database).disable_user(args.username):
            parser.error("Account not found.")
        print("Account disabled; sessions revoked.")
    elif args.command == "enable-user":
        if not Store(args.database).enable_user(args.username):
            parser.error("Account not found. Check the account name and --database path.")
        print("Account enabled. Sign in with your current password.")
    else:
        if args.local_http and args.host not in ("127.0.0.1", "localhost"):
            parser.error("--local-http requires a loopback bind address.")
        public_origin = args.origin or (f"http://127.0.0.1:{args.port}" if args.local_http else None)
        if public_origin is None:
            parser.error("Supply --origin https://your-service.example or --local-http for development.")
        try:
            app = create_app(args.database, origin=public_origin, secure_cookie=not args.local_http,
                             config_path=args.config, start_worker=True)
        except ValueError as exc:
            parser.error(str(exc))
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
