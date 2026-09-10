import hashlib
import os
import secrets
import time
from functools import wraps

from flask import abort, redirect, request, session, url_for


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expired = session.get("admin_expires_at") and session["admin_expires_at"] < int(time.time())
        if not session.get("admin_id") or expired:
            if expired:
                session.clear()
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("admin_role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf():
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(str(supplied), str(session.get("csrf_token", ""))):
        abort(400, "Token CSRF tidak valid.")


def hash_ip(value):
    salt = os.environ.get("RATE_LIMIT_SALT", "development-only-change-me")
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
