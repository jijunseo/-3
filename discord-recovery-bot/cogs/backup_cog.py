"""
cogs/backup_cog.py ─ 백업 관련 슬래시 커맨드
  /backup save [label]   ─ 현재 서버 백업
  /backup list           ─ 백업 목록 확인
  /backup delete <id>    ─ 백업 삭제
  /backup info <id>      ─ 백업 상세 정보
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from config import ADMIN_ROLE_NAME
import logging

log = logging.getLogger(__name__)


def is_admin():
    """관리자 또는 지정 역할 체크"""
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


class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    backup_group = app_commands.Group(name="backup", description="서버 백업 관리")

    # ─────────────────────────────────────────
    #  /backup save [label]
    # ─────────────────────────────────────────
    @backup_group.command(name="save", description="현재 서버 구조를 백업합니다")
    @app_commands.describe(label="백업 이름 (선택사항, 예: '이벤트전백업')")
    @is_admin()
    async def backup_save(self, interaction: discord.Interaction, label: str = ""):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild

        try:
            # 멤버 캐시 강제 로드
            await guild.chunk()
            backup_id = db.save_backup(guild, label)

            embed = discord.Embed(
                title       = "✅ 백업 완료",
                description = (
                    f"**서버:** `{guild.name}`\n"
                    f"**백업 ID:** `{backup_id}`\n"
                    f"**라벨:** `{label or '없음'}`\n\n"
                    f"채널 {len(guild.channels)}개 · "
                    f"역할 {len(guild.roles)}개 · "
                    f"멤버 {guild.member_count}명 저장 완료"
                ),
                color = discord.Color.green()
            )
            embed.set_footer(text=f"복구 시: /restore id:{backup_id}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            log.exception("백업 저장 오류")
            await interaction.followup.send(f"❌ 백업 실패: `{e}`", ephemeral=True)

    # ─────────────────────────────────────────
    #  /backup list
    # ─────────────────────────────────────────
    @backup_group.command(name="list", description="이 서버의 백업 목록을 확인합니다")
    @is_admin()
    async def backup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        backups = db.list_backups(str(interaction.guild_id))

        if not backups:
            return await interaction.followup.send(
                "📭 저장된 백업이 없습니다. `/backup save` 로 백업하세요.", ephemeral=True
            )

        lines = []
        for b in backups[:15]:   # 최대 15개 표시
            label = f"  `{b['label']}`" if b["label"] else ""
            lines.append(f"`ID {b['id']}` · {b['created_at']} UTC{label}")

        embed = discord.Embed(
            title       = f"📦 백업 목록 — {interaction.guild.name}",
            description = "\n".join(lines),
            color       = discord.Color.blurple()
        )
        embed.set_footer(text="최신순 정렬 · 최대 15개 표시")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────
    #  /backup delete <id>
    # ─────────────────────────────────────────
    @backup_group.command(name="delete", description="백업을 삭제합니다")
    @app_commands.describe(backup_id="삭제할 백업 ID")
    @is_admin()
    async def backup_delete(self, interaction: discord.Interaction, backup_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            db.delete_backup(backup_id)
            await interaction.followup.send(
                f"🗑️  백업 `ID {backup_id}` 삭제 완료", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 삭제 실패: `{e}`", ephemeral=True)

    # ─────────────────────────────────────────
    #  /backup info <id>
    # ─────────────────────────────────────────
    @backup_group.command(name="info", description="백업 상세 정보를 확인합니다")
    @app_commands.describe(backup_id="조회할 백업 ID")
    @is_admin()
    async def backup_info(self, interaction: discord.Interaction, backup_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            data = db.load_backup(backup_id)
            m    = data["meta"]
            embed = discord.Embed(
                title = f"📋 백업 상세 — ID `{backup_id}`",
                color = discord.Color.blurple()
            )
            embed.add_field(name="서버",       value=m["guild_name"],  inline=True)
            embed.add_field(name="생성 시각",   value=m["created_at"],  inline=True)
            embed.add_field(name="라벨",        value=m["label"] or "없음", inline=True)
            embed.add_field(name="역할 수",     value=f"{len(data['roles'])}개",        inline=True)
            embed.add_field(name="카테고리 수", value=f"{len(data['categories'])}개",   inline=True)
            embed.add_field(name="채널 수",     value=f"{len(data['channels'])}개",     inline=True)
            embed.add_field(name="멤버 수",     value=f"{len(data['member_roles'])}명", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 조회 실패: `{e}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
