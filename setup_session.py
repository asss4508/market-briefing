"""
최초 1회만 실행하세요.
출력된 Session String을 GitHub Secret 'TELEGRAM_SESSION' 에 저장하세요.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID 입력: "))
api_hash = input("API Hash 입력: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n✅ 인증 완료! 아래 Session String을 복사하세요:\n")
    print(client.session.save())
    print("\n위 문자열을 GitHub Secret 'TELEGRAM_SESSION' 에 저장하세요.")
