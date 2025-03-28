from flask import Flask, render_template, request
import calendar
from datetime import datetime

app = Flask(__name__)

class CustomHTMLCalendar(calendar.HTMLCalendar):
    def formatday(self, day, weekday):
        if day == 0:
            return '<td class="noday">&nbsp;</td>'
        else:
            return f'<td>{day}</td>'

    def formatmonth(self, year, month, withyear=True):
        self.year = year
        self.month = month
        html = super().formatmonth(year, month, withyear)
        return html

@app.route('/')
def show_calendar():
    year = request.args.get('year', default=datetime.now().year, type=int)
    month = request.args.get('month', default=datetime.now().month, type=int)

    # 月の範囲チェック
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    cal = CustomHTMLCalendar()
    html_calendar = cal.formatmonth(year, month)

    return render_template(
        'calendar.html',
        calendar_html=html_calendar,
        year=year,
        month=month
    )

if __name__ == '__main__':
    app.run(debug=True)
