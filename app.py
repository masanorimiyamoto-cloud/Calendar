# app.py (Flask版サンプル)
from flask import Flask, render_template
import calendar

app = Flask(__name__)

@app.route('/')
def show_calendar():
    cal = calendar.HTMLCalendar()
    return cal.formatmonth(2024, 7)

if __name__ == '__main__':
    app.run()