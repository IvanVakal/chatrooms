from dotenv import load_dotenv

load_dotenv()  # Load .env file into environment variables (local dev only)

import json
import os
import urllib.request
import uuid
from datetime import datetime
from functools import wraps

import msal
from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
# Azure App Service terminates HTTPS in front of the app; trust its forwarded
# headers so url_for(..., _external=True) builds https:// redirect URIs.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///chatrooms.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ── Microsoft Entra ID Configuration ─────────────────────────
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
TENANT_ID = os.environ.get("TENANT_ID", "common")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["User.Read"]  # Ask for permission to read the user's profile

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class Room(db.Model):
    __tablename__ = "room"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    created_by = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    messages = db.relationship(
        "Message", backref="room", lazy="dynamic", cascade="all, delete-orphan"
    )


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    author = db.Column(db.String(64), nullable=False)
    content = db.Column(db.String(2000), nullable=False)
    posted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def _build_msal_app():
    """Create an MSAL ConfidentialClientApplication instance."""
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


def login_required(f):
    """Decorator: redirect to Microsoft login if user is not in session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            # Save where the user was trying to go (GET only; a POST can't be replayed)
            if request.method == "GET":
                session["next"] = request.url
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def _graph_display_name(access_token):
    """Look up the user's display name from Microsoft Graph (/me), using User.Read."""
    req = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return (json.load(resp).get("displayName") or "").strip() or None
    except Exception:
        return None


def current_user_name():
    """Display name of the signed-in user: name claim, then email, then 'Unknown'."""
    user = session["user"]
    for key in ("name", "preferred_username", "email"):
        value = (user.get(key) or "").strip()
        if value:
            return value[:64]  # created_by / author columns are String(64)
    return "Unknown"


@app.context_processor
def inject_user():
    if "user" not in session:
        return {"user": None, "user_name": None}
    return {"user": session["user"], "user_name": current_user_name()}


# ── Authentication routes ────────────────────────────────────
@app.route("/login")
def login():
    """Redirect the user to Microsoft's login page."""
    # Generate a random state value to prevent CSRF attacks
    session["state"] = str(uuid.uuid4())

    auth_url = _build_msal_app().get_authorization_request_url(
        SCOPE,
        state=session["state"],
        redirect_uri=url_for("callback", _external=True),
    )
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """Microsoft redirects here after the user logs in."""
    # Security check: verify the state matches to prevent CSRF
    if request.args.get("state") != session.get("state"):
        return redirect(url_for("index"))

    # Check if Microsoft returned an error (e.g. user cancelled login)
    if "error" in request.args:
        error_msg = request.args.get("error_description", request.args.get("error"))
        return render_template("auth_error.html", title="Login Error", message=error_msg)

    # Exchange the authorization code for an ID token
    result = _build_msal_app().acquire_token_by_authorization_code(
        request.args["code"],
        scopes=SCOPE,
        redirect_uri=url_for("callback", _external=True),
    )

    if "error" in result:
        return render_template(
            "auth_error.html", title="Token Error", message=result.get("error_description")
        )

    # Store the token claims in the session (contains name, email, etc.)
    session.pop("state", None)
    claims = result.get("id_token_claims") or {}
    # Some accounts have no "name" claim in the ID token; ask Graph for the display name
    if not (claims.get("name") or "").strip() and result.get("access_token"):
        claims["name"] = _graph_display_name(result["access_token"])
    session["user"] = claims

    # Redirect to where the user was trying to go, or the home page
    next_page = session.pop("next", None)
    return redirect(next_page or url_for("index"))


@app.route("/logout")
def logout():
    """Clear the local session and sign out of Microsoft."""
    session.clear()
    # Redirect to Microsoft's logout endpoint so the browser session is fully cleared
    logout_url = (
        AUTHORITY
        + "/oauth2/v2.0/logout"
        + "?post_logout_redirect_uri="
        + url_for("signed_out", _external=True)
    )
    return redirect(logout_url)


@app.route("/signed-out")
def signed_out():
    """Public landing page shown after sign-out."""
    return render_template("signed_out.html")


# ── Application routes (all require sign-in) ─────────────────
@app.route("/")
@login_required
def index():
    rooms = db.session.query(Room).all()
    room_data = []
    for room in rooms:
        message_count = room.messages.count()
        last_message = room.messages.order_by(Message.posted_at.desc()).first()
        last_activity = last_message.posted_at if last_message else room.created_at
        room_data.append(
            {
                "id": room.id,
                "name": room.name,
                "created_by": room.created_by,
                "message_count": message_count,
                "last_activity": last_activity,
            }
        )
    room_data.sort(key=lambda r: r["last_activity"], reverse=True)
    return render_template("index.html", rooms=room_data)


@app.route("/rooms", methods=["POST"])
@login_required
def create_room():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("index"))

    existing = Room.query.filter_by(name=name).first()
    if existing:
        return redirect(url_for("room", room_id=existing.id))

    room = Room(name=name, created_by=current_user_name())
    db.session.add(room)
    db.session.commit()
    return redirect(url_for("room", room_id=room.id))


@app.route("/rooms/<int:room_id>")
@login_required
def room(room_id):
    room = Room.query.get_or_404(room_id)
    messages = (
        Message.query.filter_by(room_id=room.id)
        .order_by(Message.posted_at.desc())
        .limit(50)
        .all()
    )
    messages.reverse()
    return render_template("room.html", room=room, messages=messages)


@app.route("/rooms/<int:room_id>/messages", methods=["POST"])
@login_required
def post_message(room_id):
    room = Room.query.get_or_404(room_id)
    content = request.form.get("content", "").strip()
    if content:
        message = Message(room_id=room.id, author=current_user_name(), content=content)
        db.session.add(message)
        db.session.commit()
    return redirect(url_for("room", room_id=room.id))


if __name__ == "__main__":
    app.run(debug=True)
