"""
cogs/auto_backup_cog.py ─ 주기적 자동 백업 (기본 6시간마다)
  /autobackup enable  ─ 자동 백업 활성화
  /autobackup disable ─ 자동 백업 비활성화
  /autobackup status  ─ 현재 자동 백업 상태 확인
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime
import database as db
from config import AUTO_BACKUP_INTERVAL, ADMIN_ROLE_NAME, LOG_CHANNEL
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
                await interaction.response.send_message(
                    "❌ 관리자 권한이 필요합니다.", ephemeral=True
                )
                return False
        return True
    return app_commands.check(predicate)


class AutoBackupCog(commands.Cog):
    """
    주기적 자동 백업 Cog
    guild_id 별로 활성화 여부를 메모리에 유지합니다.
    (재시작 시 초기화 — 필요하면 DB에 저장하도록 확장 가능)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # { guild_id: bool }
        self.enabled_guilds: set[int] = set()
        self.auto_backup_task.start()

    def cog_unload(self):
        self.auto_backup_task.cancel()

    # ══════════════════════════════════════════════════
    #  주기 백업 루프 (AUTO_BACKUP_INTERVAL 초마다)
    # ══════════════════════════════════════════════════

    @tasks.loop(seconds=AUTO_BACKUP_INTERVAL)
    async def auto_backup_task(self):
        if not self.enabled_guilds:
            return

        for gid in list(self.enabled_guilds):
            guild = self.bot.get_guild(gid)
            if not guild:
                continue
            try:
                await guild.chunk()   # 멤버 캐시 확보
                backup_id = db.save_backup(guild, label="[자동백업]")
                log.info("[AutoBackup] guild=%s  backup_id=%s", guild.name, backup_id)

                # 오래된 자동 백업 정리 (최근 5개만 유지)
                await self._cleanup_old_auto_backups(str(gid), keep=5)

                # 로그 채널 알림
                await self._notify(
                    guild,
                    f"💾 **자동 백업 완료** — ID `{backup_id}`\n"
                    f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
                )
            except Exception as e:
                log.exception("[AutoBackup] 오류 guild=%s : %s", guild.name, e)

    @auto_backup_task.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════
    #  오래된 자동 백업 정리
    # ══════════════════════════════════════════════════

    async def _cleanup_old_auto_backups(self, guild_id: str, keep: int = 5):
        """[자동백업] 라벨이 붙은 백업 중 최신 keep 개만 남기고 삭제"""
        all_backups = db.list_backups(guild_id)
        auto_backups = [b for b in all_backups if b["label"] == "[자동백업]"]
        if len(auto_backups) > keep:
            to_delete = auto_backups[keep:]   # 오래된 것 (내림차순 정렬이므로 뒤가 오래됨)
            for b in to_delete:
                db.delete_backup(b["id"])
                log.info("[AutoBackup] 오래된 백업 삭제: id=%s", b["id"])

    # ══════════════════════════════════════════════════
    #  알림 전송
    # ══════════════════════════════════════════════════

    async def _notify(self, guild: discord.Guild, message: str):
        if not LOG_CHANNEL:
            return
        ch = guild.get_channel(LOG_CHANNEL)
        if not ch:
            return
        embed = discord.Embed(description=message, color=discord.Color.blurple())
        embed.set_footer(text="RecoveryBot 자동백업 시스템")
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    # ══════════════════════════════════════════════════
    #  /autobackup 슬래시 커맨드
    # ══════════════════════════════════════════════════

    ab_group = app_commands.Group(name="autobackup", description="자동 백업 설정")

    @ab_group.command(name="enable", description="주기적 자동 백업을 활성화합니다")
    @is_admin()
    async def ab_enable(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        self.enabled_guilds.add(gid)

        hours = AUTO_BACKUP_INTERVAL // 3600
        mins  = (AUTO_BACKUP_INTERVAL % 3600) // 60

        interval_str = (
            f"{hours}시간" if mins == 0 else
            f"{hours}시간 {mins}분" if hours > 0 else
            f"{mins}분"
        )

        embed = discord.Embed(
            title       = "💾 자동 백업 활성화",
            description = (
                f"백업 주기: **{interval_str}**마다\n"
                f"최근 5개 자동 백업만 유지 (오래된 것 자동 삭제)\n\n"
                f"비활성화: `/autobackup disable`"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ab_group.command(name="disable", description="자동 백업을 비활성화합니다")
    @is_admin()
    async def ab_disable(self, interaction: discord.Interaction):
        self.enabled_guilds.discard(interaction.guild_id)
        await interaction.response.send_message(
            "🔴 자동 백업이 **비활성화**되었습니다.", ephemeral=True
        )

    @ab_group.command(name="status", description="자동 백업 현재 상태를 확인합니다")
    @is_admin()
    async def ab_status(self, interaction: discord.Interaction):
        gid     = interaction.guild_id
        enabled = gid in self.enabled_guilds
        latest  = db.load_latest_backup(str(gid))
        all_bk  = db.list_backups(str(gid))
        auto_bk = [b for b in all_bk if b["label"] == "[자동백업]"]

        hours = AUTO_BACKUP_INTERVAL // 3600
        mins  = (AUTO_BACKUP_INTERVAL % 3600) // 60
        interval_str = (
            f"{hours}시간" if mins == 0 else
            f"{hours}시간 {mins}분" if hours > 0 else
            f"{mins}분"
        )

        embed = discord.Embed(
            title = "📊 자동 백업 상태",
            color = discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(name="상태",       value="✅ 활성화" if enabled else "🔴 비활성화", inline=True)
        embed.add_field(name="백업 주기",   value=f"`{interval_str}`", inline=True)
        embed.add_field(name="자동 백업 수", value=f"`{len(auto_bk)}`개 (최대 5개 유지)", inline=True)
        embed.add_field(
            name  = "최근 백업",
            value = (
                f"ID `{latest['meta']['id']}` — `{latest['meta']['created_at']}` UTC"
                if latest else "❌ 없음"
            ),
            inline = False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ab_group.command(name="now", description="즉시 백업을 실행합니다")
    @app_commands.describe(label="백업 라벨 (선택사항)")
    @is_admin()
    async def ab_now(self, interaction: discord.Interaction, label: str = ""):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            await guild.chunk()
            backup_id = db.save_backup(guild, label=label or "[즉시백업]")
            await interaction.followup.send(
                f"✅ 즉시 백업 완료 — ID `{backup_id}`", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 백업 실패: `{e}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoBackupCog(bot))
