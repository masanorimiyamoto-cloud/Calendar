import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

class CalendarButton:
    """日付ボタンを表現するクラス"""
    def __init__(self, parent, row, col):
        self.parent = parent
        self.year = None
        self.month = None
        self.day = None
        self.original_bg = '#ffffff'
        
        self.button = ttk.Button(
            parent.frame,
            text='',
            width=3,
            command=self.on_click
        )
        self.button.grid(row=row, column=col, padx=2, pady=2)

    def on_click(self):
        """ボタンクリック時の処理"""
        if self.day:
            self.parent.selected_date = datetime(self.year, self.month, self.day)
            self.parent.update_display()

class CalendarApp:
    """メインのカレンダーアプリケーションクラス"""
    def __init__(self, root):
        self.root = root
        self.root.title("Pythonカレンダー")
        self.current_date = datetime.now()
        self.selected_date = None
        
        self.configure_styles()
        self.create_widgets()
        self.create_buttons()
        self.update_calendar()

    def configure_styles(self):
        """ボタンスタイルの設定"""
        style = ttk.Style()
        style.configure('TButton', font=('Helvetica', 10))
        style.configure('Active.TButton', background='white')
        style.configure('Inactive.TButton', background='#e0e0e0')
        style.map('Selected.TButton',
                 background=[('active', '#0078d7'), ('!active', '#0078d7')],
                 foreground=[('active', 'white'), ('!active', 'white')])

    def create_widgets(self):
        """UIウィジェットの作成"""
        self.frame = ttk.Frame(self.root, padding="10")
        self.frame.pack()
        
        self.label = ttk.Label(
            self.frame,
            text="",
            font=('Helvetica', 12, 'bold')
        )
        self.label.grid(row=0, column=0, columnspan=7)
        
        ttk.Button(
            self.frame,
            text="前月",
            command=self.prev_month
        ).grid(row=1, column=0, columnspan=3, pady=5)
        
        ttk.Button(
            self.frame,
            text="次月",
            command=self.next_month
        ).grid(row=1, column=4, columnspan=3, pady=5)

    def create_buttons(self):
        """日付ボタンを生成"""
        self.buttons = []
        for row in range(6):
            row_buttons = []
            for col in range(7):
                btn = CalendarButton(self, row+2, col)
                row_buttons.append(btn)
            self.buttons.append(row_buttons)

    def update_calendar(self):
        """カレンダー表示を更新"""
        year = self.current_date.year
        month = self.current_date.month
        
        self.label.config(text=f"{year}年 {month}月")
        self.reset_buttons()
        
        first_day = datetime(year, month, 1)
        last_day = (datetime(year, month+1, 1) - timedelta(days=1)).day
        first_weekday = first_day.weekday()  # 0=月曜日
        
        # 前月の日付を表示
        prev_month_last_day = (first_day - timedelta(days=1)).day
        day = prev_month_last_day - first_weekday + 1
        
        for col in range(first_weekday):
            btn = self.buttons[0][col]
            btn.year = year if month > 1 else year-1
            btn.month = month-1 if month > 1 else 12
            btn.day = day
            btn.button.config(
                text=str(day),
                style='Inactive.TButton'
            )
            day += 1
        
        # 当月の日付を表示
        day = 1
        for row in range(6):
            for col in range(7):
                if (row == 0 and col < first_weekday) or day > last_day:
                    continue
                
                btn = self.buttons[row][col]
                btn.year = year
                btn.month = month
                btn.day = day
                btn.button.config(
                    text=str(day),
                    style='Active.TButton'
                )
                day += 1
        
        # 次月の日付を表示
        if day <= last_day:
            day = 1
            next_month = month + 1 if month < 12 else 1
            next_year = year if month < 12 else year + 1
            
            for row in range(6):
                for col in range(7):
                    btn = self.buttons[row][col]
                    if not btn.day:
                        btn.year = next_year
                        btn.month = next_month
                        btn.day = day
                        btn.button.config(
                            text=str(day),
                            style='Inactive.TButton'
                        )
                        day += 1

    def reset_buttons(self):
        """ボタンの状態をリセット"""
        for row in self.buttons:
            for btn in row:
                btn.button.config(text='', style='TButton')
                btn.day = None

    def prev_month(self):
        """前月に移動"""
        first_day = self.current_date.replace(day=1)
        self.current_date = first_day - timedelta(days=1)
        self.update_calendar()

    def next_month(self):
        """次月に移動"""
        next_month = self.current_date.month + 1
        next_year = self.current_date.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        self.current_date = self.current_date.replace(
            year=next_year,
            month=next_month
        )
        self.update_calendar()

    def update_display(self):
        """選択日を更新"""
        if self.selected_date:
            self.label.config(
                text=f"選択中: {self.selected_date.strftime('%Y年%m月%d日')}"
            )
    def configure_styles(self):
        style = ttk.Style()
        # 日曜日は赤、土曜日は青に設定
        style.configure('Sun.TButton', foreground='red', font=('Helvetica', 10, 'bold'))
        style.configure('Sat.TButton', foreground='blue', font=('Helvetica', 10, 'bold'))        
    def update_display(self):
       if self.selected_date:
        # 選択日を太字+緑色で表示
        self.label.config(
            text=f"選択中: {self.selected_date.strftime('%Y年%m月%d日')}",
            foreground='green',
            font=('Helvetica', 12, 'bold')
        )
if __name__ == "__main__":
    root = tk.Tk()
    app = CalendarApp(root)
    root.mainloop()