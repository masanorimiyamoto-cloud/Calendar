import os
from flask import Flask, render_template, request, redirect
import calendar
from datetime import datetime
import jpholiday
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# PostgreSQL 用の設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///events.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)



# イベントモデル
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)

# 初回実行時にDBテーブルを作成
with app.app_context():
    db.create_all()

@app.route('/')
def show_calendar():
    # クエリパラメータから年月を取得
    year = request.args.get('year', default=datetime.now().year, type=int)
    month = request.args.get('month', default=datetime.now().month, type=int)

    # 月の範囲チェック
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    # 日曜始まりのカレンダー（前後月の日付も含む）
    cal = calendar.Calendar(firstweekday=6)
    raw_weeks = cal.monthdatescalendar(year, month)
    today = datetime.now().date()

    # 表示範囲の開始日・終了日
    first_day = raw_weeks[0][0]
    last_day = raw_weeks[-1][-1]

    # 表示範囲内のイベントを取得
    events = Event.query.filter(Event.date >= first_day, Event.date <= last_day).all()
    events_by_date = {}
    for event in events:
        events_by_date.setdefault(event.date, []).append(event)

    # 各日付ごとに祝日やイベントのフラグを付与
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
    if request.method == 'POST':
        event_date_str = request.form.get('date')
        title = request.form.get('title')
        description = request.form.get('description')
        try:
            event_date = datetime.strptime(event_date_str, '%Y-%m-%d').date()
        except ValueError:
            return "日付の形式が正しくありません。YYYY-MM-DD の形式で入力してください。"
        new_event = Event(date=event_date, title=title, description=description)
        db.session.add(new_event)
        db.session.commit()
        return redirect('/')
    return render_template('add_event.html')
@app.route('/event/<int:event_id>')
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    return render_template('event_detail.html', event=event)

# サーバーレス環境向けにエクスポート
application = app

#if __name__ == '__main__':
 #   app.run(debug=True)
