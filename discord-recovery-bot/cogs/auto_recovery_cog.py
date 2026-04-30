"""
cogs/auto_recovery_cog.py ─ 서버가 터졌을 때 자동 복구 감지 + 실행
감지 조건:
  1. on_guild_channel_delete  → 채널이 대량 삭제되면 자동 복구 트리거
  2. on_member_ban            → 대량 밴(레이드)이 감지되면 트리거
  3. on_guild_role_delete     → 역할이 대량 삭제되면 트리거
  4. /autorecovery 설정 커맨드
"""

import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import database as db
from recovery_engine import RecoveryEngine
from config import LOG_CHANNEL, ADMIN_ROLE_NAME
import logging

log = logging.getLogger(__name__)


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if ADMIN_ROLE_NAME:
            has_role = any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles)
            if not has_role and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    f"❌ `{ADMIN_ROLE_NAME}` 역할 또는 관리자 권한이 필요합니다.", ephemeral=True
                )
                return False
        else:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
                return False
        return True
    return app_commands.check(predicate)


class AutoRecoveryCog(commands.Cog):
    """
    자동 복구 감지 & 실행 Cog

    guild_id 별로 아래 상태를 메모리에 유지합니다:
      - auto_enabled  : 자동복구 활성화 여부
      - threshold     : 몇 초 안에 몇 개 삭제되면 트리거
      - cooldown_until: 이미 복구 중이면 재트리거 방지
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # { guild_id: { "enabled": bool, "ch_thresh": int, "role_thresh": int,
        #               "ban_thresh": int, "window_sec": int } }
        self.settings: dict[int, dict] = {}

        # 이벤트 카운터 { guild_id: { "channel_delete": [timestamps], ... } }
        self._counters: dict[int, dict] = defaultdict(
            lambda: {"channel_delete": [], "role_delete": [], "ban": []}
        )

        # 복구 쿨다운 { guild_id: datetime }
        self._cooldown: dict[int, datetime] = {}

        # 현재 자동복구 진행 중인 길드 세트
        self._recovering: set[int] = set()

    # ══════════════════════════════════════════════════
    #  이벤트 리스너
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._record_event(channel.guild, "channel_delete")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._record_event(role.guild, "role_delete")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._record_event(guild, "ban")

    # ══════════════════════════════════════════════════
    #  이벤트 기록 & 트리거 판단
    # ══════════════════════════════════════════════════

    async def _record_event(self, guild: discord.Guild, event_type: str):
        gid      = guild.id
        settings = self.settings.get(gid)

        if not settings or not settings.get("enabled"):
            return

        # 쿨다운 체크
        if gid in self._cooldown and datetime.utcnow() < self._cooldown[gid]:
            return

        # 이미 복구 중이면 무시
        if gid in self._recovering:
            return

        now    = datetime.utcnow()
        window = timedelta(seconds=settings.get("window_sec", 10))

        counter = self._counters[gid][event_type]
        counter.append(now)
        # 윈도우 밖 항목 제거
        self._counters[gid][event_type] = [t for t in counter if now - t <= window]

        thresh_key = {
            "channel_delete": "ch_thresh",
            "role_delete":    "role_thresh",
            "ban":            "ban_thresh",
        }.get(event_type)

        if thresh_key and len(self._counters[gid][event_type]) >= settings.get(thresh_key, 5):
            log.warning(
                "[AutoRecovery] 트리거 감지! guild=%s event=%s count=%d",
                guild.name, event_type, len(self._counters[gid][event_type])
            )
            # 카운터 리셋
            self._counters[gid][event_type] = []
            await self._auto_restore(guild, event_type)

    # ══════════════════════════════════════════════════
    #  자동 복구 실행
    # ══════════════════════════════════════════════════

    async def _auto_restore(self, guild: discord.Guild, trigger: str):
        gid = guild.id
        self._recovering.add(gid)

        # 5분 쿨다운 설정
        self._cooldown[gid] = datetime.utcnow() + timedelta(minutes=5)

        data = db.load_latest_backup(str(gid))
        if not data:
            log.warning("[AutoRecovery] 백업 없음 — guild=%s", guild.name)
            await self._notify(guild, "⚠️ 자동복구 감지됐지만 **저장된 백업이 없습니다!**\n"
                                      "`/backup save` 로 백업을 먼저 생성하세요.", discord.Color.orange())
            self._recovering.discard(gid)
            return

        m = data["meta"]
        await self._notify(
            guild,
            f"🚨 **서버 공격 감지 → 자동 복구 시작**\n"
            f"트리거: `{trigger}` | 백업 ID: `{m['id']}` (`{m['created_at']}` UTC)\n"
            f"복구 완료까지 잠시 기다려 주세요...",
            discord.Color.red()
        )

        try:
            engine = RecoveryEngine(guild, data)
            log_lines = await engine.run(
                restore_roles    = True,
                restore_channels = True,
                restore_members  = True,
            )
            text = "\n".join(log_lines[-30:])
            await self._notify(
                guild,
                f"✅ **자동 복구 완료!**\n```\n{text}\n```",
                discord.Color.green()
            )
        except Exception as e:
            log.exception("[AutoRecovery] 복구 오류")
            await self._notify(guild, f"❌ 자동 복구 중 오류 발생: `{e}`", discord.Color.red())
        finally:
            self._recovering.discard(gid)

    # ══════════════════════════════════════════════════
    #  알림 전송
    # ══════════════════════════════════════════════════

    async def _notify(self, guild: discord.Guild, message: str, color: discord.Color):
        """로그 채널 또는 시스템 채널로 알림 전송"""
        target = None
        if LOG_CHANNEL:
            target = guild.get_channel(LOG_CHANNEL)
        if not target:
            target = guild.system_channel
        if not target:
            # 봇이 볼 수 있는 첫 번째 텍스트 채널
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    target = ch
                    break
        if not target:
            return

        embed = discord.Embed(description=message, color=color)
        embed.set_footer(text="RecoveryBot 자동복구 시스템")
        try:
            await target.send(embed=embed)
        except Exception as e:
            log.warning("[AutoRecovery] 알림 전송 실패: %s", e)

    # ══════════════════════════════════════════════════
    #  /autorecovery 슬래시 커맨드
    # ══════════════════════════════════════════════════

    ar_group = app_commands.Group(name="autorecovery", description="자동 복구 설정")

    @ar_group.command(name="enable", description="자동 복구를 활성화합니다")
    @app_commands.describe(
        channel_threshold = "채널 삭제 몇 개 이상이면 트리거? (기본: 3)",
        role_threshold    = "역할 삭제 몇 개 이상이면 트리거? (기본: 3)",
        ban_threshold     = "밴 몇 명 이상이면 트리거? (기본: 5)",
        window_seconds    = "몇 초 안에 발생해야 트리거? (기본: 10초)",
    )
    @is_admin()
    async def ar_enable(
        self,
        interaction: discord.Interaction,
        channel_threshold: int = 3,
        role_threshold:    int = 3,
        ban_threshold:     int = 5,
        window_seconds:    int = 10,
    ):
        gid = interaction.guild_id
        self.settings[gid] = {
            "enabled":    True,
            "ch_thresh":  channel_threshold,
            "role_thresh": role_threshold,
            "ban_thresh": ban_threshold,
            "window_sec": window_seconds,
        }

        # 백업 존재 여부 확인
        latest = db.load_latest_backup(str(gid))
        backup_warn = "" if latest else \
            "\n\n⚠️ **백업이 없습니다!** `/backup save` 로 먼저 백업을 생성하세요."

        embed = discord.Embed(
            title       = "🛡️ 자동 복구 활성화",
            description = (
                f"채널 삭제 임계값: `{channel_threshold}개 / {window_seconds}초`\n"
                f"역할 삭제 임계값: `{role_threshold}개 / {window_seconds}초`\n"
                f"밴 임계값:       `{ban_threshold}명 / {window_seconds}초`\n"
                f"{backup_warn}"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ar_group.command(name="disable", description="자동 복구를 비활성화합니다")
    @is_admin()
    async def ar_disable(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        if gid in self.settings:
            self.settings[gid]["enabled"] = False
        await interaction.response.send_message(
            "🔴 자동 복구가 **비활성화**되었습니다.", ephemeral=True
        )

    @ar_group.command(name="status", description="자동 복구 현재 상태를 확인합니다")
    @is_admin()
    async def ar_status(self, interaction: discord.Interaction):
        gid      = interaction.guild_id
        settings = self.settings.get(gid, {})
        enabled  = settings.get("enabled", False)
        latest   = db.load_latest_backup(str(gid))
        backup_info = (
            f"백업 ID `{latest['meta']['id']}` — `{latest['meta']['created_at']}` UTC"
            if latest else "❌ 없음"
        )

        recovering = "⚙️ 복구 진행 중!" if gid in self._recovering else "대기 중"
        cooldown_str = ""
        if gid in self._cooldown:
            left = (self._cooldown[gid] - datetime.utcnow()).total_seconds()
            if left > 0:
                cooldown_str = f"\n쿨다운: `{int(left)}초` 남음"

        embed = discord.Embed(
            title = "📊 자동 복구 상태",
            color = discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(name="상태",   value="✅ 활성화" if enabled else "🔴 비활성화", inline=True)
        embed.add_field(name="복구",   value=recovering,   inline=True)
        embed.add_field(name="최근 백업", value=backup_info, inline=False)
        if enabled:
            embed.add_field(
                name  = "임계값",
                value = (
                    f"채널 삭제: `{settings.get('ch_thresh')}개`\n"
                    f"역할 삭제: `{settings.get('role_thresh')}개`\n"
                    f"밴:       `{settings.get('ban_thresh')}명`\n"
                    f"감지 윈도우: `{settings.get('window_sec')}초`"
                    f"{cooldown_str}"
                ),
                inline = False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRecoveryCog(bot))
