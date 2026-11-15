"""出欠の同時更新テスト（独立した DB を使う。本番 DB には接続しない）。

使い方（本番の DATABASE_URL は使わず、毎回新しいテスト用 DB を作る）:
  pip install pgserver -r requirements.txt   # 別の仮想環境を推奨
  python tests/attendance_race_test.py postgres
  python tests/attendance_race_test.py sqlite
  - postgres: pgserver で起動したローカル PostgreSQL 16（本番と同じ FOR UPDATE を検証）
  - sqlite:   一時ファイルの SQLite（FOR UPDATE が無いので本番の検証にはならない）

競合を確実に起こすため、participant テーブルを読む SELECT の直後に 0.3 秒の
遅延を入れる（修正前のコードでは「人数確認」と「登録」の間が広がる）。
"""
import os
import sys
import tempfile
import threading
import time
import uuid

MODE = sys.argv[1]
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(tempfile.gettempdir(), "calendar-race-test")
os.makedirs(SCRATCH, exist_ok=True)
# テスト用 DB 以外に繋がないよう、外から渡された DATABASE_URL は無視する。
os.environ.pop("DATABASE_URL", None)

if MODE == "postgres":
    import pgserver
    import psycopg2

    srv = pgserver.get_server(os.path.join(SCRATCH, "pgdata"), cleanup_mode=None)
    base_uri = srv.get_uri()
    dbname = "caltest_" + uuid.uuid4().hex[:8]
    conn = psycopg2.connect(base_uri)
    conn.autocommit = True
    conn.cursor().execute(f"CREATE DATABASE {dbname}")
    conn.close()
    uri = base_uri.rsplit("/", 1)[0] + "/" + dbname if "?" not in base_uri else None
    if uri is None:
        head, query = base_uri.split("?", 1)
        uri = head.rsplit("/", 1)[0] + "/" + dbname + "?" + query
    os.environ["DATABASE_URL"] = uri
else:
    path = os.path.join(SCRATCH, f"race_{uuid.uuid4().hex[:8]}.db")
    os.environ["DATABASE_URL"] = "sqlite:///" + path.replace("\\", "/")

os.environ["INIT_DB"] = "1"
sys.path.insert(0, APP_DIR)
import logging
logging.disable(logging.WARNING)
from app import app, db, Event, Participant, Member, MAX_PARTICIPANTS  # noqa: E402
from sqlalchemy import event as sa_event  # noqa: E402

with app.app_context():
    ENGINE = db.engine
print(f"[{MODE}] dialect={ENGINE.dialect.name} url={ENGINE.url.render_as_string(hide_password=True)}")

SLOW = {"on": False}


@sa_event.listens_for(ENGINE, "after_cursor_execute")
def _slow_participant_reads(conn, cursor, statement, params, context, executemany):
    s = statement.lower()
    if SLOW["on"] and s.lstrip().startswith("select") and "participant" in s:
        time.sleep(0.3)


def reset(attending_names, absent_names=()):
    with app.app_context():
        Participant.query.delete()
        Event.query.delete()
        Member.query.delete()
        for n in ["A", "B", "C", "D", "E", "F"]:
            db.session.add(Member(name=n))
        ev = Event(date=__import__("datetime").date(2030, 1, 1), title="t",
                   start_time="09:00", end_time="11:00", change_version=0)
        db.session.add(ev)
        db.session.flush()
        for n in attending_names:
            db.session.add(Participant(event_id=ev.id, name=n, attending=True))
        for n in absent_names:
            db.session.add(Participant(event_id=ev.id, name=n, attending=False))
        db.session.commit()
        return ev.id


def state(event_id):
    with app.app_context():
        ev = db.session.get(Event, event_id)
        rows = Participant.query.filter_by(event_id=event_id).order_by(Participant.name).all()
        return {
            "attending": sorted(p.name for p in rows if p.attending),
            "absent": sorted(p.name for p in rows if not p.attending),
            "rows": len(rows),
            "change_version": ev.change_version,
        }


def client():
    c = app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
    return c


def concurrent(requests_):
    """requests_: list of callables(client) -> (status, body). 同時に開始する。"""
    barrier = threading.Barrier(len(requests_))
    results = [None] * len(requests_)

    def run(i, fn):
        c = client()
        barrier.wait()
        try:
            results[i] = fn(c)
        except Exception as exc:  # noqa: BLE001
            results[i] = ("EXC", repr(exc))

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(requests_)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def ajax(event_id, name, status):
    def fn(c):
        r = c.post(f"/event/{event_id}/attendance", data={"member_name": name, "status": status},
                   headers={"X-Requested-With": "fetch"}, base_url="https://localhost")
        return r.status_code, r.get_json()
    return fn


def form(event_id, name, status):
    def fn(c):
        r = c.post(f"/event/{event_id}", data={"member_name": name, "status": status},
                   base_url="https://localhost")
        body = r.get_data(as_text=True)
        err = "満席" if f"参加は最大 {MAX_PARTICIPANTS} 名までです。" in body else None
        return r.status_code, err
    return fn


failures = []


def check(label, cond, detail):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(label)
    print(f"  {mark} {label}: {detail}")


# ---- ケース1: 3人参加の予約に別々の2人が同時登録 (JSON API) ----
SLOW["on"] = True
eid = reset(["A", "B", "C"])
res = concurrent([ajax(eid, "D", "attend"), ajax(eid, "E", "attend")])
st = state(eid)
ok = sorted(r[0] for r in res)
print("case1 ajax:", res)
check("1a 同時登録(API) 最終人数4", len(st["attending"]) == 4, st)
check("1a 1人成功・1人満席400", ok == [200, 400], ok)
check("1a 満席メッセージ維持",
      any(r[0] == 400 and r[1] == {"ok": False, "error": "参加は最大 4 名までです。"} for r in res), res)
check("1a change_version +1", st["change_version"] == 1, st["change_version"])

# ---- ケース1b: 同じことをフォームPOST(event_detail)とAPIの混在で ----
eid = reset(["A", "B", "C"])
res = concurrent([form(eid, "D", "attend"), ajax(eid, "E", "attend")])
st = state(eid)
print("case1b form+ajax:", res)
check("1b 同時登録(フォーム+API) 最終人数4", len(st["attending"]) == 4, st)
check("1b change_version +1", st["change_version"] == 1, st["change_version"])

# ---- ケース1c: フォーム同士 ----
eid = reset(["A", "B", "C"])
res = concurrent([form(eid, "D", "attend"), form(eid, "E", "attend")])
st = state(eid)
print("case1c form+form:", res)
check("1c 同時登録(フォーム同士) 最終人数4", len(st["attending"]) == 4, st)
check("1c 片方に満席エラー表示", sorted(str(r[1]) for r in res) == ["None", "満席"], res)

# ---- ケース2: 同じ人が同時に参加登録 ----
eid = reset(["A", "B"])
res = concurrent([ajax(eid, "D", "attend"), ajax(eid, "D", "attend"), form(eid, "D", "attend")])
st = state(eid)
print("case2 same person x3:", res)
check("2 500/例外なし", all(r[0] in (200, 302) for r in res), res)
check("2 二重登録なし", st["rows"] == 3 and st["attending"] == ["A", "B", "D"], st)
check("2 change_version は実変更1回分", st["change_version"] == 1, st["change_version"])

# ---- ケース3: 満席からの不参加・未定 ----
SLOW["on"] = False
eid = reset(["A", "B", "C", "D"])
r1 = ajax(eid, "D", "absent")(client())
r2 = ajax(eid, "C", "undecided")(client())
st = state(eid)
print("case3:", r1, r2)
check("3 満席→不参加 200", r1[0] == 200 and r1[1]["status"] == "absent" and r1[1]["attending_count"] == 3, r1)
check("3 →未定 200", r2[0] == 200 and r2[1]["attending_count"] == 2, r2)
check("3 最終状態", st["attending"] == ["A", "B"] and st["absent"] == ["D"], st)
# 満席で参加済みの人の「参加」再送 / 満席でフォームから不参加
eid = reset(["A", "B", "C", "D"])
r3 = ajax(eid, "A", "attend")(client())
r4 = form(eid, "B", "absent")(client())
r5 = ajax(eid, "E", "attend")(client())
st = state(eid)
print("case3b:", r3, r4, r5)
check("3b 満席で参加済みの再送は200・変化なし", r3[0] == 200 and r3[1]["change_version"] == 0, r3)
check("3b 満席でフォームから不参加", r4 == (302, None) and "B" in st["absent"], (r4, st))
check("3b 空いた枠にE参加", r5[0] == 200 and r5[1]["attending_count"] == 4, r5)

# ---- ケース4: change_version ----
eid = reset([])
seq = [("A", "attend"), ("A", "attend"), ("A", "absent"), ("A", "absent"),
       ("A", "undecided"), ("A", "undecided"), ("B", "absent")]
versions = [ajax(eid, n, s)(client())[1]["change_version"] for n, s in seq]
print("case4 versions:", versions)
check("4 実変更のみ加算", versions == [1, 1, 2, 2, 3, 3, 4], versions)
# 同時に別々の人が変更 → 取りこぼしなし
SLOW["on"] = True
eid = reset([])
res = concurrent([ajax(eid, n, "absent") for n in ["A", "B", "C"]])
st = state(eid)
print("case4b concurrent 3 changes:", [r[0] for r in res], st)
check("4b 同時3変更で +3", st["change_version"] == 3, st["change_version"])
SLOW["on"] = False
# 開催状況更新との同時実行でも取りこぼしなし
SLOW["on"] = True
eid = reset([])


def status_post(c):
    r = c.post(f"/event/{eid}/status", data={"status": "hold", "note": "雨"}, base_url="https://localhost")
    return r.status_code, r.get_json()


res = concurrent([ajax(eid, "A", "attend"), status_post])
st = state(eid)
print("case4c attendance+status:", [r[0] for r in res], st)
check("4c 出欠+開催状況の同時更新で +2", st["change_version"] == 2, st["change_version"])
SLOW["on"] = False

# ---- ケース5: カレンダー表示・開催状況更新 ----
eid = reset(["A"])
c = client()
r = c.get("/?year=2030&month=1", base_url="https://localhost")
check("5 カレンダー表示 200", r.status_code == 200, r.status_code)
r = c.post(f"/event/{eid}/status", data={"status": "cancelled", "note": "雨天"}, base_url="https://localhost")
j = r.get_json()
check("5 開催状況更新", r.status_code == 200 and j["status_label"] == "中止" and j["change_version"] == 1, j)
r = c.get(f"/event/{eid}", base_url="https://localhost")
check("5 予約詳細表示 200", r.status_code == 200 and "中止" in r.get_data(as_text=True), r.status_code)
r = ajax(999999, "A", "attend")(c)
check("5 存在しない予約は404", r[0] == 404 and r[1]["error"] == "予約が見つかりません。", r)
r = ajax(eid, "ZZ", "attend")(c)
check("5 未登録メンバーは400", r[0] == 400 and r[1]["error"] == "メンバーが見つかりません。", r)
r = ajax(eid, "A", "bogus")(c)
check("5 不正statusは400", r[0] == 400 and r[1]["error"] == "不正な操作です。", r)
r = c.post(f"/event/{eid}", data={"member_name": "ZZ", "status": "attend"}, base_url="https://localhost")
check("5 フォーム:未登録メンバー表示", r.status_code == 200 and "メンバーが見つかりません。" in r.get_data(as_text=True), r.status_code)

# ---- 時刻: DB のタイムゾーンが UTC でも JST で保存・表示される ----
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
_jst_now = _dt.now(_tz(_td(hours=9))).replace(tzinfo=None)
with app.app_context():
    if ENGINE.dialect.name == "postgresql":
        print("  session TimeZone:", db.session.execute(__import__("sqlalchemy").text("SHOW TimeZone")).scalar())
eid = reset([])
c = client()
j = c.post(f"/event/{eid}/status", data={"status": "hold", "note": ""}, base_url="https://localhost").get_json()
_shown = _dt.strptime(j["updated_at"], "%Y/%m/%d %H:%M")
check("6 開催状況の更新時刻がJST", abs((_shown - _jst_now).total_seconds()) < 120, (j["updated_at"], _jst_now))
_page = c.get(f"/event/{eid}", base_url="https://localhost").get_data(as_text=True)
check("6 詳細画面の最終更新がJST", "最終更新: " + j["updated_at"] in _page, j["updated_at"])
r = c.post("/api/scores", json={"player_a": "A", "player_b": "B", "score": "6-4"})
with app.app_context():
    from app import ScoreResult
    _created = db.session.get(ScoreResult, r.get_json()["id"]).created_at
check("6 スコアのcreated_atがJST", abs((_created - _jst_now).total_seconds()) < 120, (_created, _jst_now))

print(f"[{MODE}] RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAIL -> {failures}")
sys.exit(1 if failures else 0)
