import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
#  봇 설정
# ─────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "여기에_봇_토큰_입력")
PREFIX      = os.getenv("PREFIX", "!")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL_ID", 0))   # 로그 전송할 채널 ID (없으면 0)

# ─────────────────────────────────────────
#  DB 경로
# ─────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "backup.db")

# ─────────────────────────────────────────
#  자동 백업 주기 (초)  기본 6시간
# ─────────────────────────────────────────
AUTO_BACKUP_INTERVAL = int(os.getenv("AUTO_BACKUP_INTERVAL", 21600))

# ─────────────────────────────────────────
#  복구 권한: 이 역할 이름을 가진 사람만 /restore 사용 가능
#  (비워두면 관리자(Administrator) 권한 체크만 함)
# ─────────────────────────────────────────
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "")

# ─────────────────────────────────────────
#  OAuth2 설정 (인증 버튼 / 멤버 자동 참가)
# ─────────────────────────────────────────
CLIENT_ID     = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
REDIRECT_URI  = os.getenv("REDIRECT_URI", "http://localhost:8000/callback")

# ─────────────────────────────────────────
#  웹서버 포트
# ─────────────────────────────────────────
WEB_PORT = int(os.getenv("PORT", 8000))
