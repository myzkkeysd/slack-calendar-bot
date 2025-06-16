import os
import base64
import json
import datetime
from dotenv import load_dotenv

from slack_bolt import App
from googleapiclient.discovery import build
from google.oauth2 import service_account
from openai import OpenAI

# 環境変数ロード
load_dotenv()

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
GOOGLE_CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GOOGLE_SERVICE_ACCOUNT_B64 = os.environ["GOOGLE_SERVICE_ACCOUNT_B64"]

# service_account.json をRender内で復元
with open("service_account.json", "w") as f:
    f.write(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode('utf-8'))

# Google Calendar 認証
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=credentials)

# Slack & OpenAI初期化
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# 日本語曜日変換
WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

def parse_schedule_ai(text):
    today = datetime.date.today()
    prompt = f"""
あなたはスケジュール変換AIです。以下の入力文を読み取り、予定日・開始時刻・終了時刻・予定タイトルを抽出してください。

- 入力に「明日」などがある場合は今日の日付 ({today.strftime('%Y-%m-%d')}) を基準に計算。
- 日付が欠落している場合は今日の日付を使用。
- 終了時刻が省略されている場合は開始時刻＋1時間とする。
- 時刻は24時間表記（例：15:00）に統一。
- 出力は以下JSONフォーマットのみ許可：
{{"date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM", "title": "予定タイトル"}}

入力: {text}
"""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "あなたはプロの自然言語スケジュール抽出AIです。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
    )

    result = response.choices[0].message.content
    return json.loads(result)

@app.event("app_mention")
def handle_app_mention_events(body, client):
    text = body.get("event", {}).get("text", "")
    channel_id = body.get("event", {}).get("channel")
    ts = body.get("event", {}).get("ts")

    client.reactions_add(channel=channel_id, name="thinking_face", timestamp=ts)

    try:
        parsed = parse_schedule_ai(text)

        date_obj = datetime.datetime.strptime(parsed['date'], "%Y-%m-%d").date()
        start_time = datetime.datetime.strptime(parsed['start'], "%H:%M").time()
        end_time = datetime.datetime.strptime(parsed['end'], "%H:%M").time()

        start_dt = datetime.datetime.combine(date_obj, start_time)
        end_dt = datetime.datetime.combine(date_obj, end_time)

        # 重複チェック
        time_min = (start_dt - datetime.timedelta(minutes=1)).isoformat() + 'Z'
        time_max = (end_dt + datetime.timedelta(minutes=1)).isoformat() + 'Z'

        events_result = calendar_service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            q=parsed['title'],
            singleEvents=True
        ).execute()

        existing_events = events_result.get('items', [])

        if existing_events:
            message = f"⚠ 既に予定が登録されています: {parsed['title']} ({parsed['start']} - {parsed['end']})"
            client.reactions_add(channel=channel_id, name="x", timestamp=ts)
        else:
            event = {
                'summary': parsed['title'],
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
            }
            calendar_service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()

            weekday = WEEKDAYS_JA[date_obj.weekday()]
            formatted_date = f"{date_obj.year}/{date_obj.month:02}/{date_obj.day:02} ({weekday})"
            formatted_time = f"{parsed['start']} - {parsed['end']}"

            message = f"✅ Googleカレンダー登録完了しました。\n\n{formatted_date} {formatted_time} {parsed['title']}"
            client.reactions_add(channel=channel_id, name="white_check_mark", timestamp=ts)

    except Exception as e:
        message = f"❌ 登録失敗: {e}"
        client.reactions_add(channel=channel_id, name="x", timestamp=ts)

    client.chat_postMessage(channel=channel_id, thread_ts=ts, text=message)
    client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=ts)

if __name__ == "__main__":
    app.start(port=3000)
