"""
cogs/restore_cog.py ─ 복구 관련 슬래시 커맨드
  /restore id <backup_id>   ─ 특정 백업으로 복구
  /restore latest           ─ 가장 최근 백업으로 복구
  /restore preview <id>     ─ 복구 미리보기 (실제 적용 없음)
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from recovery_engine import RecoveryEngine
from config import ADMIN_ROLE_NAME, LOG_CHANNEL
import logging

log = logging.getLogger(__name__)


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if ADMIN_ROLE_NAME:
            has_role = any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles)
            if not has_role and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    f"❌ `{ADMIN_ROLE_NAME}` 역할 또는 관리자 권한이 필요합니다.",
                    ephemeral=True
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


# ──────────────────────────────────────────────────────────
#  복구 확인 버튼 View
# ──────────────────────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, backup_data: dict, restore_roles: bool,
                 restore_channels: bool, restore_members: bool):
        super().__init__(timeout=60)
        self.backup_data      = backup_data
        self.restore_roles    = restore_roles
        self.restore_channels = restore_channels
        self.restore_members  = restore_members
        self.confirmed        = False

    @discord.ui.button(label="✅ 복구 시작", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("복구가 취소되었습니다.", ephemeral=True)


# ──────────────────────────────────────────────────────────
#  RestoreCog
# ──────────────────────────────────────────────────────────
class RestoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    restore_group = app_commands.Group(name="restore", description="서버 복구 관리")

    # ─────────────────────────────────────────
    #  /restore id <backup_id>
    # ─────────────────────────────────────────
    @restore_group.command(name="id", description="특정 백업 ID로 서버를 복구합니다")
    @app_commands.describe(
        backup_id        = "복구할 백업 ID",
        restore_roles    = "역할 복구 여부 (기본: True)",
        restore_channels = "채널 복구 여부 (기본: True)",
        restore_members  = "멤버 역할 복구 여부 (기본: True)",
    )
    @is_admin()
    async def restore_id(
        self,
        interaction: discord.Interaction,
        backup_id:        int,
        restore_roles:    bool = True,
        restore_channels: bool = True,
        restore_members:  bool = True,
    ):
        try:
            data = db.load_backup(backup_id)
        except ValueError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)

        await self._confirm_and_restore(
            interaction, data, restore_roles, restore_channels, restore_members
        )

    # ─────────────────────────────────────────
    #  /restore latest
    # ─────────────────────────────────────────
    @restore_group.command(name="latest", description="가장 최근 백업으로 서버를 복구합니다")
    @app_commands.describe(
        restore_roles    = "역할 복구 여부 (기본: True)",
        restore_channels = "채널 복구 여부 (기본: True)",
        restore_members  = "멤버 역할 복구 여부 (기본: True)",
    )
    @is_admin()
    async def restore_latest(
        self,
        interaction: discord.Interaction,
        restore_roles:    bool = True,
        restore_channels: bool = True,
        restore_members:  bool = True,
    ):
        data = db.load_latest_backup(str(interaction.guild_id))
        if not data:
            return await interaction.response.send_message(
                "📭 저장된 백업이 없습니다. `/backup save` 로 먼저 백업하세요.",
                ephemeral=True
            )

        await self._confirm_and_restore(
            interaction, data, restore_roles, restore_channels, restore_members
        )

    # ─────────────────────────────────────────
    #  /restore preview <backup_id>
    # ─────────────────────────────────────────
    @restore_group.command(name="preview", description="복구 예상 내용을 미리 봅니다 (실제 적용 없음)")
    @app_commands.describe(backup_id="미리볼 백업 ID")
    @is_admin()
    async def restore_preview(self, interaction: discord.Interaction, backup_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            data = db.load_backup(backup_id)
        except ValueError as e:
            return await interaction.followup.send(f"❌ {e}", ephemeral=True)

        m = data["meta"]
        guild = interaction.guild

        # 현재 서버와 비교
        existing_roles    = {r.name for r in guild.roles}
        existing_channels = {c.name.lower() for c in guild.channels}

        new_roles    = [r["name"] for r in data["roles"]    if r["name"] not in existing_roles]
        new_channels = [c["name"] for c in data["channels"] if c["name"].lower() not in existing_channels]
        new_cats     = [c["name"] for c in data["categories"]]

        desc = (
            f"**백업 ID:** `{m['id']}`\n"
            f"**생성일:** `{m['created_at']}` UTC\n"
            f"**라벨:** `{m['label'] or '없음'}`\n\n"
            f"📌 **복구 시 생성될 항목 (미리보기)**\n"
            f"🎭 새 역할: {len(new_roles)}개  `{', '.join(new_roles[:5])}{'...' if len(new_roles)>5 else ''}`\n"
            f"📁 카테고리: {len(new_cats)}개\n"
            f"💬 새 채널: {len(new_channels)}개  `{', '.join(new_channels[:5])}{'...' if len(new_channels)>5 else ''}`\n"
            f"👥 멤버 역할 복구 대상: {len(data['member_roles'])}명"
        )

        embed = discord.Embed(
            title       = "🔍 복구 미리보기",
            description = desc,
            color       = discord.Color.orange()
        )
        embed.set_footer(text="실제 복구: /restore id 또는 /restore latest")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════
    #  내부: 확인 후 복구 실행
    # ══════════════════════════════════════════════════
    async def _confirm_and_restore(
        self, interaction: discord.Interaction, data: dict,
        restore_roles: bool, restore_channels: bool, restore_members: bool
    ):
        m = data["meta"]

        confirm_embed = discord.Embed(
            title       = "⚠️  복구 확인",
            description = (
                f"**백업 ID `{m['id']}`** — `{m['created_at']}` UTC\n"
                f"라벨: `{m['label'] or '없음'}`\n\n"
                f"다음 항목을 복구합니다:\n"
                f"{'✅' if restore_roles    else '❌'} 역할 `{len(data['roles'])}개`\n"
                f"{'✅' if restore_channels else '❌'} 채널 `{len(data['channels'])}개` + 카테고리\n"
                f"{'✅' if restore_members  else '❌'} 멤버 역할 `{len(data['member_roles'])}명`\n\n"
                "⚡ **60초 내에 확인하지 않으면 자동 취소됩니다.**"
            ),
            color = discord.Color.red()
        )

        view = ConfirmView(data, restore_roles, restore_channels, restore_members)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
        await view.wait()

        if not view.confirmed:
            return

        # ── 복구 실행 ──
        progress_embed = discord.Embed(
            title       = "🔁 복구 진행 중...",
            description = "잠시 기다려 주세요. 서버 규모에 따라 수 분이 걸릴 수 있습니다.",
            color       = discord.Color.yellow()
        )
        msg = await interaction.followup.send(embed=progress_embed, ephemeral=True)

        engine = RecoveryEngine(interaction.guild, data)
        log_lines = await engine.run(restore_roles, restore_channels, restore_members)

        # ── 결과 embed ──
        result_text = "\n".join(log_lines[-30:])   # 최대 30줄
        result_embed = discord.Embed(
            title       = "✅ 복구 완료",
            description = f"```\n{result_text}\n```",
            color       = discord.Color.green()
        )
        await interaction.followup.send(embed=result_embed, ephemeral=True)

        # ── 로그 채널 전송 ──
        await self._send_log(interaction.guild, interaction.user, m, log_lines)

    async def _send_log(self, guild, user, meta, log_lines):
        if not LOG_CHANNEL:
            return
        ch = guild.get_channel(LOG_CHANNEL)
        if not ch:
            return
        text = "\n".join(log_lines[-40:])
        embed = discord.Embed(
            title       = f"🛡️ 복구 실행 — ID `{meta['id']}`",
            description = f"실행자: {user.mention}\n```\n{text}\n```",
            color       = discord.Color.green()
        )
        try:
            await ch.send(embed=embed)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RestoreCog(bot))
