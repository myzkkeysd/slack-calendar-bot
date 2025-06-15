import os
import base64
from dotenv import load_dotenv
import re
import datetime
import dateparser  # ★ 追加

from googleapiclient.discovery import build
from google.oauth2 import service_account
from slack_bolt import App

# ==========================
# 環境変数読み込み
# ==========================
load_dotenv()

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
GOOGLE_CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]

service_account_b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_B64")
with open("service_account.json", "w") as f:
    f.write(base64.b64decode(service_account_b64).decode('utf-8'))

SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=credentials)

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

# ==========================
# 既存: 数字形式のパース
# ==========================

def parse_standard_format(text):
    match = re.match(r'(?:<@[\w]+>\s*)?(\d{8}) (\d{2,4})-(\d{2,4}) (.+)', text)
    if not match:
        return None

    yyyymmdd, start_raw, end_raw, title = match.groups()
    date_obj = datetime.datetime.strptime(yyyymmdd, "%Y%m%d").date()

    def parse_time(raw):
        if len(raw) == 2:
            return datetime.datetime.combine(date_obj, datetime.time(int(raw), 0))
        elif len(raw) == 4:
            return datetime.datetime.combine(date_obj, datetime.time(int(raw[:2]), int(raw[2:])))
        else:
            raise ValueError("時刻フォーマット不正")

    start_time = parse_time(start_raw)
    end_time = parse_time(end_raw)
    return start_time, end_time, title

# ==========================
# 新規: AI自然言語パース
# ==========================

def parse_natural_language(text):
    now = datetime.datetime.now()

    dt = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
    if not dt:
        raise ValueError("日時を認識できませんでした")

    start_time = dt
    end_time = start_time + datetime.timedelta(hours=1)

    # 簡易タイトル抽出
    if "から" in text:
        title = text.split("から")[-1].strip()
    else:
        title = "予定"

    return start_time, end_time, title

# ==========================
# app_mentionイベント処理
# ==========================

@app.event("app_mention")
def handle_app_mention_events(body, client):
    text = body.get("event", {}).get("text", "")
    channel_id = body.get("event", {}).get("channel")
    ts = body.get("event", {}).get("ts")

    client.reactions_add(channel=channel_id, name="thinking_face", timestamp=ts)

    try:
        # ① まず従来フォーマットを優先判定
        parsed = parse_standard_format(text)

        if parsed:
            start_time, end_time, title = parsed
        else:
            # ② ダメならAI自然言語パース
            start_time, end_time, title = parse_natural_language(text)

        # 重複チェック
        time_min = (start_time - datetime.timedelta(minutes=1)).isoformat() + 'Z'
        time_max = (end_time + datetime.timedelta(minutes=1)).isoformat() + 'Z'

        events_result = calendar_service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            q=title,
            singleEvents=True
        ).execute()

        existing_events = events_result.get('items', [])

        if existing_events:
            message = f"⚠ 既に予定が登録されています: {title} ({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')})"
        else:
            event = {
                'summary': title,
                'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Tokyo'},
                'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Tokyo'},
            }
            calendar_service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
            message = f"✅ Googleカレンダーに登録しました: {title} ({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')})"

    except Exception as e:
        message = f"❌ 登録失敗: {e}"

    client.chat_postMessage(channel=channel_id, thread_ts=ts, text=message)
    client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=ts)

# ==========================
# サーバ起動
# ==========================

if __name__ == "__main__":
    app.start(port=3000)
