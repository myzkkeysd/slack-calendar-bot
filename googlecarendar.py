import os
import base64
from dotenv import load_dotenv

load_dotenv()

import re
import datetime
from googleapiclient.discovery import build
from google.oauth2 import service_account
from slack_bolt import App

# ==========================
# ① ここをあなたの環境に合わせて設定
# ==========================

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
GOOGLE_CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]

service_account_b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_B64")
with open("service_account.json", "w") as f:
    f.write(base64.b64decode(service_account_b64).decode('utf-8'))

# サービスアカウントJSONファイル
SERVICE_ACCOUNT_FILE = 'service_account.json'

SCOPES = ['https://www.googleapis.com/auth/calendar']
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=credentials)

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

# ==========================
# ② app_mention処理 (重複防止＋リアクション＋スレッド対応)
# ==========================

@app.event("app_mention")
def handle_app_mention_events(body, client):
    text = body.get("event", {}).get("text", "")
    channel_id = body.get("event", {}).get("channel")
    ts = body.get("event", {}).get("ts")

    # 処理中リアクション追加
    client.reactions_add(channel=channel_id, name="thinking_face", timestamp=ts)

    match = re.match(r'(?:<@[\w]+>\s*)?(\d{8}) (\d{1,2})-(\d{1,2}) (.+)', text)
    if match:
        yyyymmdd, start_h, end_h, title = match.groups()
        try:
            date_obj = datetime.datetime.strptime(yyyymmdd, "%Y%m%d").date()
            start_time = datetime.datetime.combine(date_obj, datetime.time(int(start_h)))
            end_time = datetime.datetime.combine(date_obj, datetime.time(int(end_h)))

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
                message = f"⚠ 既に予定が登録されています: {title} ({start_h}:00 - {end_h}:00)"
            else:
                event = {
                    'summary': title,
                    'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Tokyo'},
                    'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Tokyo'},
                }
                calendar_service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
                message = f"✅ Googleカレンダーに登録しました: {title} ({start_h}:00 - {end_h}:00)"

        except Exception as e:
            message = f"❌ 登録失敗: {e}"
    else:
        message = "⚠ 書式が違います。 例: 20250620 14-15 打合せ"

    # スレッドでメッセージ投稿
    client.chat_postMessage(channel=channel_id, thread_ts=ts, text=message)

    # リアクション削除
    client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=ts)

# ==========================
# ③ サーバ起動
# ==========================

if __name__ == "__main__":
    app.start(port=3000)
