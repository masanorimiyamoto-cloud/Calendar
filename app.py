import os
import calendar
import hmac
from datetime import datetime

import jpholiday
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Production uses DATABASE_URL, typically PostgreSQL on Vercel/Neon.
# Local development falls back to instance/events.db.
database_url = os.environ.get("DATABASE_URL", "sqlite:///events.db")
database_url = database_url.replace("postgres://", "postgresql://")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
}
if "DATABASE_URL" in os.environ:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"].update({
        "pool_size": 1,
        "max_overflow": 2,
        "pool_recycle": 300,
    })

APP_NAME = "千葉北テニス"
ACCESS_CODE = os.environ.get("ACCESS_CODE", "chibakita")
DEFAULT_EVENT_TITLE = "千葉北"
MAX_PARTICIPANTS = 4
START_HOUR = 6
END_HOUR = 22

db = SQLAlchemy(app)


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    title = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.String(5))
    end_time = db.Column(db.String(5))
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

    @property
    def time_range(self):
        if self.start_time and self.end_time:
            return f"{self.start_time} - {self.end_time}"
        return ""


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


def build_time_options():
    return [
        {"value": f"{hour:02d}:00", "label": f"{hour:02d}:00"}
        for hour in range(START_HOUR, END_HOUR + 1)
    ]


def parse_hour(time_value):
    try:
        parsed_time = datetime.strptime(time_value, "%H:%M").time()
    except (TypeError, ValueError):
        return None

    if parsed_time.minute != 0:
        return None
    if parsed_time.hour < START_HOUR or parsed_time.hour > END_HOUR:
        return None
    return parsed_time.hour


def validate_time_range(start_time, end_time):
    start_hour = parse_hour(start_time)
    end_hour = parse_hour(end_time)

    if start_hour is None or end_hour is None:
        return False
    return start_hour < end_hour


def ensure_event_time_columns():
    inspector = inspect(db.engine)
    existing_columns = {column["name"] for column in inspector.get_columns("event")}
    statements = []

    if "start_time" not in existing_columns:
        statements.append("ALTER TABLE event ADD COLUMN start_time VARCHAR(5)")
    if "end_time" not in existing_columns:
        statements.append("ALTER TABLE event ADD COLUMN end_time VARCHAR(5)")

    for statement in statements:
        db.session.execute(text(statement))

    if statements:
        db.session.commit()


def should_initialize_database():
    if os.environ.get("INIT_DB") == "1":
        return True
    return "DATABASE_URL" not in os.environ


if should_initialize_database():
    with app.app_context():
        try:
            db.create_all()
            ensure_event_time_columns()
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("db.create_all() をスキップしました: %s", exc)


@app.context_processor
def inject_app_name():
    return {"app_name": APP_NAME}


@app.before_request
def require_login():
    if request.endpoint in {"login", "static"} or request.endpoint is None:
        return None
    if session.get("authenticated"):
        return None
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": False, "error": "ログインしてください。"}), 401
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error_message = None
    next_url = request.args.get("next") or url_for("show_calendar")
    if not next_url.startswith("/"):
        next_url = url_for("show_calendar")

    if request.method == "POST":
        code = request.form.get("access_code", "")
        if hmac.compare_digest(code, ACCESS_CODE):
            session["authenticated"] = True
            return redirect(next_url)
        error_message = "合言葉が違います。"

    return render_template("login.html", error_message=error_message, next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


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
    time_options = build_time_options()

    if request.method == "POST":
        event_date_str = request.form.get("date")
        title = request.form.get("title", "").strip() or DEFAULT_EVENT_TITLE
        start_time = request.form.get("start_time", "")
        end_time = request.form.get("end_time", "")
        description = request.form.get("description")

        try:
            event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return "日付の形式が正しくありません。YYYY-MM-DD の形式で入力してください。", 400

        if not validate_time_range(start_time, end_time):
            return "開始時刻より後の終了時刻を選択してください。", 400

        existing_event = Event.query.filter_by(date=event_date).first()
        if existing_event:
            return redirect(url_for("event_detail", event_id=existing_event.id))

        new_event = Event(
            date=event_date,
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )
        db.session.add(new_event)
        db.session.commit()
        return redirect(url_for("event_detail", event_id=new_event.id))

    return render_template(
        "add_event.html",
        date=selected_date,
        time_options=time_options,
        default_event_title=DEFAULT_EVENT_TITLE,
    )


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


@app.route("/event/<int:event_id>/attendance", methods=["POST"])
def update_attendance(event_id):
    name = request.form.get("member_name", "").strip()
    status = request.form.get("status", "")

    if status not in ("attend", "absent", "undecided"):
        return jsonify({"ok": False, "error": "不正な操作です。"}), 400

    event_exists = Event.query.with_entities(Event.id).filter_by(id=event_id).first()
    if not event_exists:
        return jsonify({"ok": False, "error": "予約が見つかりません。"}), 404

    member_exists = Member.query.with_entities(Member.id).filter_by(name=name).first()
    if not member_exists:
        return jsonify({"ok": False, "error": "メンバーが見つかりません。"}), 400

    participant = Participant.query.filter_by(event_id=event_id, name=name).first()

    if status == "undecided":
        if participant:
            db.session.delete(participant)
    else:
        attending = status == "attend"
        already_attending = participant.attending if participant else False

        if attending and not already_attending:
            attending_count = Participant.query.filter_by(event_id=event_id, attending=True).count()
            if attending_count >= MAX_PARTICIPANTS:
                return jsonify({
                    "ok": False,
                    "error": f"参加は最大 {MAX_PARTICIPANTS} 名までです。",
                }), 400

        if participant:
            participant.attending = attending
        else:
            db.session.add(Participant(event_id=event_id, name=name, attending=attending))

    db.session.commit()

    attending_count = Participant.query.filter_by(event_id=event_id, attending=True).count()
    return jsonify({
        "ok": True,
        "name": name,
        "status": status,
        "attending_count": attending_count,
        "max_participants": MAX_PARTICIPANTS,
    })


@app.route("/event/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return redirect(url_for("show_calendar", year=event.date.year, month=event.date.month))


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
