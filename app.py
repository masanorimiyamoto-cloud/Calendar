import os
import calendar
from datetime import datetime

import jpholiday
from flask import Flask, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.pool import NullPool


app = Flask(__name__)

# Production uses DATABASE_URL, typically PostgreSQL on Vercel/Neon.
# Local development falls back to instance/events.db.
database_url = os.environ.get("DATABASE_URL", "sqlite:///events.db")
database_url = database_url.replace("postgres://", "postgresql://")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "poolclass": NullPool,
    "pool_pre_ping": True,
}

MAX_PARTICIPANTS = 4

db = SQLAlchemy(app)


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    participants = db.relationship(
        "Participant",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    @property
    def attending_count(self):
        return sum(1 for participant in self.participants if participant.attending)

    @property
    def participant_count(self):
        return len(self.participants)


class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    attending = db.Column(db.Boolean, nullable=False, default=True)
    event = db.relationship("Event", back_populates="participants")

    __table_args__ = (
        db.UniqueConstraint("event_id", "name", name="uix_event_participant_name"),
    )


class Member(db.Model):
    """出欠表に並べる固定メンバーの名簿。"""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("db.create_all() をスキップしました: %s", exc)


@app.route("/")
def show_calendar():
    year = request.args.get("year", default=datetime.now().year, type=int)
    month = request.args.get("month", default=datetime.now().month, type=int)

    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    cal = calendar.Calendar(firstweekday=6)
    raw_weeks = cal.monthdatescalendar(year, month)
    today = datetime.now().date()

    first_day = raw_weeks[0][0]
    last_day = raw_weeks[-1][-1]

    events = Event.query.filter(Event.date >= first_day, Event.date <= last_day).all()
    events_by_date = {}
    for event in events:
        events_by_date.setdefault(event.date, []).append(event)

    month_days = []
    for week in raw_weeks:
        week_data = []
        for day in week:
            week_data.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "is_holiday": jpholiday.is_holiday(day),
                    "holiday_name": jpholiday.is_holiday_name(day),
                    "events": events_by_date.get(day, []),
                }
            )
        month_days.append(week_data)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_days=month_days,
        today=today,
    )


@app.route("/event/add", methods=["GET", "POST"])
def add_event():
    selected_date = request.args.get("date", "")

    if request.method == "POST":
        event_date_str = request.form.get("date")
        title = request.form.get("title") or "テニスコート予約"
        description = request.form.get("description")

        try:
            event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return "日付の形式が正しくありません。YYYY-MM-DD の形式で入力してください。", 400

        existing_event = Event.query.filter_by(date=event_date).first()
        if existing_event:
            return redirect(url_for("event_detail", event_id=existing_event.id))

        new_event = Event(date=event_date, title=title, description=description)
        db.session.add(new_event)
        db.session.commit()
        return redirect(url_for("event_detail", event_id=new_event.id))

    return render_template("add_event.html", date=selected_date)


@app.route("/event/<int:event_id>", methods=["GET", "POST"])
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    error_message = None

    if request.method == "POST":
        name = request.form.get("member_name", "").strip()
        status = request.form.get("status", "")

        member = Member.query.filter_by(name=name).first()
        participant = Participant.query.filter_by(event_id=event.id, name=name).first()

        if not member:
            error_message = "メンバーが見つかりません。"
        elif status == "undecided":
            if participant:
                db.session.delete(participant)
                db.session.commit()
            return redirect(url_for("event_detail", event_id=event.id))
        elif status in ("attend", "absent"):
            attending = status == "attend"
            already_attending = participant.attending if participant else False

            if attending and not already_attending and event.attending_count >= MAX_PARTICIPANTS:
                error_message = f"参加は最大 {MAX_PARTICIPANTS} 名までです。"
            else:
                if participant:
                    participant.attending = attending
                else:
                    participant = Participant(event=event, name=name, attending=attending)
                    db.session.add(participant)
                db.session.commit()
                return redirect(url_for("event_detail", event_id=event.id))
        else:
            error_message = "不正な操作です。"

    status_by_name = {
        participant.name: ("attend" if participant.attending else "absent")
        for participant in event.participants
    }
    members = Member.query.order_by(Member.id).all()
    member_rows = [
        {"name": member.name, "status": status_by_name.get(member.name, "undecided")}
        for member in members
    ]

    return render_template(
        "event_detail.html",
        event=event,
        max_participants=MAX_PARTICIPANTS,
        member_rows=member_rows,
        has_members=bool(members),
        error_message=error_message,
    )


@app.route("/members", methods=["GET"])
def members():
    member_list = Member.query.order_by(Member.id).all()
    return render_template("members.html", members=member_list)


@app.route("/members/add", methods=["POST"])
def add_member():
    name = request.form.get("name", "").strip()
    if name and not Member.query.filter_by(name=name).first():
        db.session.add(Member(name=name))
        db.session.commit()
    return redirect(url_for("members"))


@app.route("/members/<int:member_id>/delete", methods=["POST"])
def delete_member(member_id):
    member = Member.query.get_or_404(member_id)
    Participant.query.filter_by(name=member.name).delete()
    db.session.delete(member)
    db.session.commit()
    return redirect(url_for("members"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
