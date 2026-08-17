# app/notifications/telegram.py
import os
import requests

def send_telegram(text: str) -> bool:
    """
    Отправляет сообщение в Telegram.
    ENV внутри docker-compose:
      TELEGRAM_BOT_TOKEN="<optional-token>"
      TELEGRAM_CHAT_ID="<optional-chat-id>"
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        # если переменных нет — просто не падаем
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("❌ Telegram send error:", e)
        return False