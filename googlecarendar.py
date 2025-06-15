import os
import base64
import re
import datetime
import json
import openai
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account
from slack_bolt import App

# .env読み込み (ローカル用)
load_dotenv()

# 環境変数
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
GOOGLE_CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# Googleサービスアカウント復元
service_account_b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_B64")
with open("service_account.json", "w") as f:
    f.write(base64.b64decode(service_account_b64).decode('utf-8'))

# Google Calendar 認証
SCOPES = ['https://www.googleapis.com/auth/calendar']
credentials = service_account.Credentials.from_service_account_file(
    "service_account.json", scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=credentials)

# Slack Bolt初期化
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
openai.api_key = OPENAI_API_KEY

# OpenAIによる自然言語解析関数
def parse_event_with_openai(input_text):
    prompt = f"""
あなたはスケジュール登録アシスタントです。
ユーザーが入力した予定から以下3つを抽出して下さい:
- 開始日時 (start): ISO8601形式 (例: 2025-06-20T15:30:00)
- 終了日時 (end): ISO8601形式 (例: 2025-06-20T16:30:00)
- タイトル (title)

今日の日付は {datetime.date.today()} です。
日時が明記されていない場合は適切に補完して下さい。
出力は必ず次のJSON形式で返してください:

{{
"start": "...",
"end": "...",
"title": "..."
}}

ユーザー入力: 「{input_text}」
"""
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    message = response['choices'][0]['message']['content']
    return json.loads(message)

@app.event("app_mention")
def handle_app_mention_events(body, client):
    text = body.get("event", {}).get("text", "")
    channel_id = body.get("event", {}).get("channel")
    ts = body.get("event", {}).get("ts")

    # 処理中リアクション
    client.reactions_add(channel=channel_id, name="thinking_face", timestamp=ts)

    try:
        parsed = parse_event_with_openai(text)
        start_dt = datetime.datetime.fromisoformat(parsed["start"])
        end_dt = datetime.datetime.fromisoformat(parsed["end"])
        title = parsed["title"]

        # 重複チェック
        time_min = (start_dt - datetime.timedelta(minutes=1)).isoformat() + 'Z'
        time_max = (end_dt + datetime.timedelta(minutes=1)).isoformat() + 'Z'
        events_result = calendar_service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            q=title,
            singleEvents=True
        ).execute()

        existing_events = events_result.get('items', [])

        if existing_events:
            message = f"⚠ 既に予定が登録されています: {title} ({start_dt}〜{end_dt})"
        else:
            event = {
                'summary': title,
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
            }
            calendar_service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
            message = f"✅ Googleカレンダーに登録しました: {title} ({start_dt}〜{end_dt})"

    except Exception as e:
        message = f"❌ 登録失敗: {e}"

    # 結果をスレッド返信
    client.chat_postMessage(channel=channel_id, thread_ts=ts, text=message)
    client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=ts)

if __name__ == "__main__":
    app.start(port=3000)
