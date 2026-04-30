"""
bot.py ─ RecoveryBot 메인 진입점
봇 + 웹서버(OAuth2 콜백) 동시 실행
실행: python bot.py
"""

import asyncio
import logging
import os
import sys
import threading
import discord
from discord.ext import commands
import uvicorn

# ── 로그 설정 ─────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers = [
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("RecoveryBot")

# ── config & DB ────────────────────────────────────
from config import BOT_TOKEN, PREFIX, WEB_PORT
import database as db

# ── Cogs 목록 ──────────────────────────────────────
COGS = [
    "cogs.backup_cog",
    "cogs.restore_cog",
    "cogs.auto_recovery_cog",
    "cogs.auto_backup_cog",
    "cogs.auth_cog",        # 인증 버튼
    "cogs.server_cog",      # 서버 현황
    "cogs.invite_cog",      # 초대 로거
    "cogs.shop_cog",        # 자판기
]

# ══════════════════════════════════════════════════
#  Bot 클래스
# ══════════════════════════════════════════════════

class RecoveryBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members         = True
        intents.guilds          = True
        intents.message_content = True

        super().__init__(
            command_prefix = PREFIX,
            intents        = intents,
            help_command   = None,
        )

    async def setup_hook(self):
        # DB 초기화
        db.init_db()

        # Cogs 로드
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("✅ Cog 로드: %s", cog)
            except Exception as e:
                log.error("❌ Cog 로드 실패: %s — %s", cog, e)

        # 슬래시 커맨드 동기화
        synced = await self.tree.sync()
        log.info("🔄 슬래시 커맨드 동기화 완료: %d개", len(synced))

    async def on_ready(self):
        log.info("=" * 50)
        log.info("✅ RecoveryBot 온라인!")
        log.info("   봇 이름 : %s", self.user.name)
        log.info("   봇 ID   : %s", self.user.id)
        log.info("   서버 수  : %d개", len(self.guilds))
        log.info("=" * 50)

        await self.change_presence(
            activity = discord.Activity(
                type = discord.ActivityType.watching,
                name = "서버 보호 중 🛡️"
            )
        )

    async def on_guild_join(self, guild: discord.Guild):
        log.info("새 서버 참가: %s (id=%s)", guild.name, guild.id)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 권한이 없습니다.")
        elif isinstance(error, commands.CommandNotFound):
            pass
        else:
            log.error("커맨드 오류: %s", error)


# ══════════════════════════════════════════════════
#  웹서버 (별도 스레드로 실행)
# ══════════════════════════════════════════════════

def run_webserver():
    """FastAPI 웹서버를 별도 스레드에서 실행"""
    from webserver import app
    config = uvicorn.Config(
        app    = app,
        host   = "0.0.0.0",
        port   = WEB_PORT,
        log_level = "info"
    )
    server = uvicorn.Server(config)
    # 새 이벤트 루프에서 실행
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


# ══════════════════════════════════════════════════
#  메인 실행
# ══════════════════════════════════════════════════

async def main():
    if not BOT_TOKEN or BOT_TOKEN == "여기에_봇_토큰_입력":
        log.critical("❌ BOT_TOKEN 이 설정되지 않았습니다!")
        sys.exit(1)

    # 웹서버 백그라운드 스레드 시작
    web_thread = threading.Thread(target=run_webserver, daemon=True)
    web_thread.start()
    log.info("🌐 웹서버 시작 (포트: %d)", WEB_PORT)

    # 봇 시작
    bot = RecoveryBot()
    async with bot:
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
