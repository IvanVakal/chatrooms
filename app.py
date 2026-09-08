import os
import random
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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


ADJECTIVES = [
    "Blue", "Red", "Silent", "Happy", "Brave", "Clever", "Mighty", "Sneaky",
    "Gentle", "Fierce", "Lucky", "Jolly", "Rusty", "Shiny", "Wild", "Calm",
    "Swift", "Grumpy", "Curious", "Bold",
]

ANIMALS = [
    "Fox", "Wolf", "Bear", "Eagle", "Otter", "Falcon", "Panda", "Tiger",
    "Rabbit", "Hawk", "Lynx", "Owl", "Badger", "Moose", "Raven", "Shark",
    "Turtle", "Cobra", "Heron", "Bison",
]


def generate_nickname():
    adjective = random.choice(ADJECTIVES)
    animal = random.choice(ANIMALS)
    number = random.randint(10, 99)
    return f"{adjective}{animal}{number}"


def get_nickname():
    if "nickname" not in session:
        session["nickname"] = generate_nickname()
    return session["nickname"]


@app.before_request
def ensure_nickname():
    get_nickname()


@app.route("/")
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
    return render_template("index.html", rooms=room_data, nickname=get_nickname())


@app.route("/rooms", methods=["POST"])
def create_room():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("index"))

    existing = Room.query.filter_by(name=name).first()
    if existing:
        return redirect(url_for("room", room_id=existing.id))

    room = Room(name=name, created_by=get_nickname())
    db.session.add(room)
    db.session.commit()
    return redirect(url_for("room", room_id=room.id))


@app.route("/rooms/<int:room_id>")
def room(room_id):
    room = Room.query.get_or_404(room_id)
    messages = (
        Message.query.filter_by(room_id=room.id)
        .order_by(Message.posted_at.desc())
        .limit(50)
        .all()
    )
    messages.reverse()
    return render_template("room.html", room=room, messages=messages, nickname=get_nickname())


@app.route("/rooms/<int:room_id>/messages", methods=["POST"])
def post_message(room_id):
    room = Room.query.get_or_404(room_id)
    content = request.form.get("content", "").strip()
    if content:
        message = Message(room_id=room.id, author=get_nickname(), content=content)
        db.session.add(message)
        db.session.commit()
    return redirect(url_for("room", room_id=room.id))


if __name__ == "__main__":
    app.run(debug=True)
