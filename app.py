
import os
from flask import Flask, render_template, request, redirect, url_for
import calendar
from datetime import datetime
import jpholiday
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# PostgreSQL 用の設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///events.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
MAX_PARTICIPANTS = 4

db = SQLAlchemy(app)


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    participants = db.relationship(
        'Participant', back_populates='event', cascade='all, delete-orphan', lazy='joined'
    )

    @property
    def attending_count(self):
        return sum(1 for participant in self.participants if participant.attending)

    @property
    def participant_count(self):
        return len(self.participants)


class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    attending = db.Column(db.Boolean, nullable=False, default=True)
    event = db.relationship('Event', back_populates='participants')

    __table_args__ = (
        db.UniqueConstraint('event_id', 'name', name='uix_event_participant_name'),
    )


# 初回実行時にDBテーブルを作成
with app.app_context():
    db.create_all()


@app.route('/')
def show_calendar():
    year = request.args.get('year', default=datetime.now().year, type=int)
    month = request.args.get('month', default=datetime.now().month, type=int)

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
            week_data.append({
                'date': day,
                'in_month': day.month == month,
                'is_today': day == today,
                'is_holiday': jpholiday.is_holiday(day),
                'holiday_name': jpholiday.is_holiday_name(day),
                'events': events_by_date.get(day, [])
            })
        month_days.append(week_data)

    return render_template(
        'calendar.html',
        year=year,
        month=month,
        month_days=month_days,
        today=today
    )


@app.route('/event/add', methods=['GET', 'POST'])
def add_event():
    selected_date = request.args.get('date', '')
    if request.method == 'POST':
        event_date_str = request.form.get('date')
        title = request.form.get('title') or 'テニスコート予約'
        description = request.form.get('description')
        try:
            event_date = datetime.strptime(event_date_str, '%Y-%m-%d').date()
        except ValueError:
            return "日付の形式が正しくありません。YYYY-MM-DD の形式で入力してください。"

        existing_event = Event.query.filter_by(date=event_date).first()
        if existing_event:
            return redirect(url_for('event_detail', event_id=existing_event.id))

        new_event = Event(date=event_date, title=title, description=description)
        db.session.add(new_event)
        db.session.commit()
        return redirect(url_for('event_detail', event_id=new_event.id))

    return render_template('add_event.html', date=selected_date)


@app.route('/event/<int:event_id>', methods=['GET', 'POST'])
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    error_message = None
    success_message = None

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        attending = request.form.get('attending') == '1'

        if not name:
            error_message = '名前を入力してください。'
        elif attending and event.attending_count >= MAX_PARTICIPANTS and not any(
            participant.name == name and participant.attending for participant in event.participants
        ):
            error_message = f'参加者は最大 {MAX_PARTICIPANTS} 名までです。'
        else:
            participant = Participant.query.filter_by(event_id=event.id, name=name).first()
            if participant:
                participant.attending = attending
                success_message = f'{name} さんの参加ステータスを更新しました。'
            else:
                participant = Participant(event=event, name=name, attending=attending)
                db.session.add(participant)
                success_message = f'{name} さんを登録しました。'

            if not error_message:
                db.session.commit()
                return redirect(url_for('event_detail', event_id=event.id))

    return render_template(
        'event_detail.html',
        event=event,
        max_participants=MAX_PARTICIPANTS,
        error_message=error_message,
        success_message=success_message
    )


@app.route('/event/<int:event_id>/participant/<int:participant_id>/delete', methods=['POST'])
def delete_participant(event_id, participant_id):
    participant = Participant.query.filter_by(id=participant_id, event_id=event_id).first_or_404()
    db.session.delete(participant)
    db.session.commit()
    return redirect(url_for('event_detail', event_id=event_id))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
