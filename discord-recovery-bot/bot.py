"""
bot.py ─ RecoveryBot 메인 진입점
봇 + 웹서버를 asyncio 단일 루프에서 동시 실행
"""

import asyncio
import logging
import os
import sys
import discord
from discord.ext import commands
import uvicorn

# ── 로그 설정 ─────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("RecoveryBot")

# ── config & DB ────────────────────────────────────
from config import BOT_TOKEN, PREFIX
import database as db
import os

# Render가 동적으로 PORT를 지정하므로 런타임에 읽음
WEB_PORT = int(os.environ.get("PORT", 8000))

# ── Cogs 목록 ──────────────────────────────────────
COGS = [
    "cogs.backup_cog",
    "cogs.restore_cog",
    "cogs.auto_recovery_cog",
    "cogs.auto_backup_cog",
    "cogs.auth_cog",
    "cogs.server_cog",
    "cogs.invite_cog",
    "cogs.shop_cog",
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
        intents.presences       = True

        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        db.init_db()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("✅ Cog 로드: %s", cog)
            except Exception as e:
                log.error("❌ Cog 로드 실패: %s — %s", cog, e)

        synced = await self.tree.sync()
        log.info("🔄 슬래시 커맨드 동기화: %d개", len(synced))

    async def on_ready(self):
        log.info("=" * 50)
        log.info("✅ RecoveryBot 온라인!")
        log.info("   봇 이름 : %s", self.user.name)
        log.info("   봇 ID   : %s", self.user.id)
        log.info("   서버 수  : %d개", len(self.guilds))
        log.info("=" * 50)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="서버 보호 중 🛡️",
            )
        )

    async def on_guild_join(self, guild):
        log.info("새 서버 참가: %s (id=%s)", guild.name, guild.id)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 권한이 없습니다.")
        elif isinstance(error, commands.CommandNotFound):
            pass
        else:
            log.error("커맨드 오류: %s", error)


# ══════════════════════════════════════════════════
#  메인 — 봇 + 웹서버 동시 실행 (단일 asyncio 루프)
# ══════════════════════════════════════════════════

async def main():
    if not BOT_TOKEN or BOT_TOKEN == "여기에_봇_토큰_입력":
        log.critical("❌ BOT_TOKEN 이 설정되지 않았습니다!")
        sys.exit(1)

    # Render PORT 런타임 확인
    port = int(os.environ.get("PORT", 8000))
    log.info("🌐 웹서버 시작 (포트: %d)", port)

    # 웹서버 설정
    from webserver import app as web_app
    web_config = uvicorn.Config(
        app=web_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        loop="asyncio",
    )
    web_server = uvicorn.Server(web_config)

    bot = RecoveryBot()

    # 봇과 웹서버를 asyncio.gather 로 동시 실행
    await asyncio.gather(
        web_server.serve(),
        bot.start(BOT_TOKEN),
    )


if __name__ == "__main__":
    asyncio.run(main())
