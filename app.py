import os
import calendar
import hmac
from datetime import date, datetime, timedelta, timezone

# 日本標準時 (UTC+9)。Vercel は UTC で動くため、日付計算は JST に合わせる。
JST = timezone(timedelta(hours=9))


def now_jst():
    return datetime.now(JST)

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

APP_NAME = "千葉北テニスメンバー予約"
ACCESS_CODE = os.environ.get("ACCESS_CODE", "chibakita")
SCOREBOARD_URL = os.environ.get("SCOREBOARD_URL", "").strip()
# スコアボード(別サイト)から /api/scores へ自動記録する際の共有トークン。
# 未設定なら無認証で受け付ける（手軽さ優先。気になるなら本番で設定する）。
SCORE_API_TOKEN = os.environ.get("SCORE_API_TOKEN", "").strip()
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
    change_version = db.Column(db.Integer, nullable=False, default=0)
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


class ScoreResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    player_a = db.Column(db.String(100), nullable=False)
    player_b = db.Column(db.String(100), nullable=False)
    score = db.Column(db.String(200), nullable=False)
    memo = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=now_jst)


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
    if "change_version" not in existing_columns:
        statements.append("ALTER TABLE event ADD COLUMN change_version INTEGER DEFAULT 0 NOT NULL")

    for statement in statements:
        db.session.execute(text(statement))

    if statements:
        db.session.commit()


def should_initialize_database():
    if os.environ.get("INIT_DB") == "1":
        return True
    return "DATABASE_URL" not in os.environ


def ensure_score_result_table():
    """score_result テーブルが無ければ作成する。

    本番 (DATABASE_URL あり) では db.create_all() を実行しないため、
    後から追加したこのテーブルだけは起動時に冪等に作成しておく。
    これを怠ると show_calendar の ScoreResult クエリが失敗し、
    カレンダー全体が 500 になる。
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("score_result"):
        ScoreResult.__table__.create(db.engine)


with app.app_context():
    try:
        if should_initialize_database():
            db.create_all()
            ensure_event_time_columns()
        ensure_score_result_table()
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("データベース初期化をスキップしました: %s", exc)


@app.context_processor
def inject_app_name():
    return {"app_name": APP_NAME}


@app.before_request
def require_login():
    if request.endpoint in {"login", "static", "api_add_score"} or request.endpoint is None:
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
    year = request.args.get("year", default=now_jst().year, type=int)
    month = request.args.get("month", default=now_jst().month, type=int)

    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    cal = calendar.Calendar(firstweekday=6)
    raw_weeks = cal.monthdatescalendar(year, month)
    today = now_jst().date()

    first_day = raw_weeks[0][0]
    last_day = raw_weeks[-1][-1]

    events = Event.query.filter(Event.date >= first_day, Event.date <= last_day).all()
    events_by_date = {}
    for event in events:
        events_by_date.setdefault(event.date, []).append(event)

    score_results = ScoreResult.query.filter(
        ScoreResult.date >= first_day,
        ScoreResult.date <= last_day,
    ).order_by(ScoreResult.date, ScoreResult.id).all()
    score_results_by_date = {}
    for result in score_results:
        score_results_by_date.setdefault(result.date, []).append(result)

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
                    "score_results": score_results_by_date.get(day, []),
                }
            )
        month_days.append(week_data)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # 次月以降(=次月の初日以降)のイベントIDを渡し、クライアント側で
    # 未確認(=詳細未読)の予約が将来にあるかを判定して次月バッジを赤くする。
    next_month_start = date(next_year, next_month, 1)
    future_event_ids = [
        str(row[0])
        for row in db.session.query(Event.id).filter(Event.date >= next_month_start).all()
    ]

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_days=month_days,
        today=today,
        prev_reserved_days=count_reserved_days(prev_year, prev_month),
        next_reserved_days=count_reserved_days(next_year, next_month),
        future_event_ids=future_event_ids,
    )


def count_reserved_days(year, month):
    """指定した年月のうち、予約が入っている日数を返す。

    隣月に予約があることを 前月/次月 ボタンの近くで知らせるために使う。
    """
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return (
        db.session.query(Event.date)
        .filter(Event.date >= first, Event.date <= last)
        .distinct()
        .count()
    )


@app.route("/scoreboard")
def open_scoreboard():
    if SCOREBOARD_URL:
        return redirect(SCOREBOARD_URL)
    return render_template("scoreboard_unconfigured.html")


@app.route("/scores/add", methods=["GET", "POST"])
def add_score_result():
    selected_date = request.args.get("date", "")
    error_message = None

    if request.method == "POST":
        date_text = request.form.get("date", "")
        player_a = request.form.get("player_a", "").strip()
        player_b = request.form.get("player_b", "").strip()
        score = request.form.get("score", "").strip()
        memo = request.form.get("memo", "").strip()

        try:
            result_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            error_message = "日付は YYYY-MM-DD 形式で入力してください。"
        else:
            if not player_a or not player_b or not score:
                error_message = "プレイヤー名とスコアを入力してください。"
            else:
                result = ScoreResult(
                    date=result_date,
                    player_a=player_a,
                    player_b=player_b,
                    score=score,
                    memo=memo or None,
                )
                db.session.add(result)
                db.session.commit()
                return redirect(url_for("score_result_detail", result_id=result.id))

    return render_template(
        "add_score_result.html",
        date=selected_date,
        error_message=error_message,
    )


def _score_api_cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Score-Token",
    }


@app.route("/api/scores", methods=["POST", "OPTIONS"])
def api_add_score():
    """別サイトのスコアボードから当日のスコアを自動記録する API。

    CORS 越しに呼ばれるため OPTIONS プリフライトに応答し、レスポンスにも
    CORS ヘッダーを付ける。SCORE_API_TOKEN が設定されていれば検証する。
    """
    if request.method == "OPTIONS":
        return ("", 204, _score_api_cors_headers())

    if SCORE_API_TOKEN:
        data_for_token = request.get_json(silent=True) or {}
        token = request.headers.get("X-Score-Token", "") or str(data_for_token.get("token", ""))
        if not hmac.compare_digest(token, SCORE_API_TOKEN):
            return jsonify({"ok": False, "error": "unauthorized"}), 401, _score_api_cors_headers()

    data = request.get_json(silent=True) or {}
    player_a = str(data.get("player_a", "")).strip()
    player_b = str(data.get("player_b", "")).strip()
    score = str(data.get("score", "")).strip()
    if not player_a or not player_b or not score:
        return (
            jsonify({"ok": False, "error": "player_a, player_b, score は必須です。"}),
            400,
            _score_api_cors_headers(),
        )

    result = ScoreResult(
        date=now_jst().date(),
        player_a=player_a[:100],
        player_b=player_b[:100],
        score=score[:200],
        memo=None,
    )
    db.session.add(result)
    db.session.commit()
    return jsonify({"ok": True, "id": result.id}), 201, _score_api_cors_headers()


@app.route("/scores/<int:result_id>")
def score_result_detail(result_id):
    result = ScoreResult.query.get_or_404(result_id)
    return render_template("score_result_detail.html", result=result)


@app.route("/scores/<int:result_id>/delete", methods=["POST"])
def delete_score_result(result_id):
    result = ScoreResult.query.get_or_404(result_id)
    year = result.date.year
    month = result.date.month
    db.session.delete(result)
    db.session.commit()
    return redirect(url_for("show_calendar", year=year, month=month))


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

    event = Event.query.filter_by(id=event_id).first()
    if not event:
        return jsonify({"ok": False, "error": "予約が見つかりません。"}), 404

    member_exists = Member.query.with_entities(Member.id).filter_by(name=name).first()
    if not member_exists:
        return jsonify({"ok": False, "error": "メンバーが見つかりません。"}), 400

    participant = Participant.query.filter_by(event_id=event_id, name=name).first()
    previous_status = "undecided"
    if participant:
        previous_status = "attend" if participant.attending else "absent"

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

    if status != previous_status:
        event.change_version = (event.change_version or 0) + 1

    db.session.commit()

    attending_count = Participant.query.filter_by(event_id=event_id, attending=True).count()
    return jsonify({
        "ok": True,
        "name": name,
        "status": status,
        "attending_count": attending_count,
        "max_participants": MAX_PARTICIPANTS,
        "change_version": event.change_version or 0,
    })


@app.route("/event/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    # 削除後はインスタンスにアクセスできないため、リダイレクト先の年月を先に控える。
    year = event.date.year
    month = event.date.month
    db.session.delete(event)
    db.session.commit()
    return redirect(url_for("show_calendar", year=year, month=month))


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
