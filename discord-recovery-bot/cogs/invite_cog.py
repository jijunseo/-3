"""
cogs/invite_cog.py ─ 인바이트 로거
  - 누가 누굴 초대했는지 추적
  - 초대 횟수 기록
  - /invite top  ─ 초대 순위
  - /invite info ─ 내 초대 현황
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import database as db
import logging

log = logging.getLogger(__name__)


class InviteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # { guild_id: { invite_code: uses } }
        self.invite_cache: dict[int, dict[str, int]] = {}

    # ══════════════════════════════════════════════════
    #  초대 캐시 초기화
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_invites(guild)

    async def _cache_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            self.invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            log.warning("초대 캐시 실패 (권한 없음): %s", guild.name)

    # ══════════════════════════════════════════════════
    #  멤버 입장 - 초대자 추적
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        try:
            new_invites = await guild.invites()
        except discord.Forbidden:
            return

        old_cache   = self.invite_cache.get(guild.id, {})
        inviter     = None
        used_code   = None

        for inv in new_invites:
            old_uses = old_cache.get(inv.code, 0)
            if inv.uses > old_uses:
                inviter   = inv.inviter
                used_code = inv.code
                break

        # 캐시 업데이트
        self.invite_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}

        # DB 저장
        db.save_invite_log(
            guild_id    = str(guild.id),
            inviter_id  = str(inviter.id) if inviter else "unknown",
            inviter_name= str(inviter) if inviter else "알 수 없음",
            invitee_id  = str(member.id),
            invitee_name= str(member),
            invite_code = used_code or "unknown"
        )

        # 로그 채널 전송
        from config import LOG_CHANNEL
        if LOG_CHANNEL:
            ch = guild.get_channel(LOG_CHANNEL)
            if ch:
                embed = discord.Embed(
                    title       = "📨 새 멤버 입장",
                    description = (
                        f"**입장:** {member.mention} (`{member}`)\n"
                        f"**초대자:** {inviter.mention if inviter else '알 수 없음'}\n"
                        f"**초대 코드:** `{used_code or '알 수 없음'}`"
                    ),
                    color     = discord.Color.green(),
                    timestamp = datetime.utcnow()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass

    # ══════════════════════════════════════════════════
    #  멤버 퇴장 로그
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        from config import LOG_CHANNEL
        if not LOG_CHANNEL:
            return
        ch = member.guild.get_channel(LOG_CHANNEL)
        if not ch:
            return

        embed = discord.Embed(
            title       = "🚪 멤버 퇴장",
            description = f"{member.mention} (`{member}`) 님이 서버를 떠났습니다.",
            color       = discord.Color.red(),
            timestamp   = datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    # ══════════════════════════════════════════════════
    #  초대 생성/삭제 캐시 업데이트
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild:
            cache = self.invite_cache.setdefault(invite.guild.id, {})
            cache[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild:
            cache = self.invite_cache.get(invite.guild.id, {})
            cache.pop(invite.code, None)

    # ══════════════════════════════════════════════════
    #  /invite 슬래시 커맨드
    # ══════════════════════════════════════════════════

    invite_group = app_commands.Group(name="invite", description="초대 현황 확인")

    @invite_group.command(name="top", description="초대 순위를 확인합니다")
    async def invite_top(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        top      = db.get_invite_top(guild_id, limit=10)

        if not top:
            return await interaction.followup.send("📭 아직 초대 기록이 없어요!", ephemeral=True)

        embed = discord.Embed(
            title = "🏆 초대 순위 TOP 10",
            color = discord.Color.gold()
        )

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, row in enumerate(top):
            medal  = medals[i] if i < 3 else f"`{i+1}.`"
            member = interaction.guild.get_member(int(row["inviter_id"]))
            name   = member.mention if member else f"`{row['inviter_name']}`"
            lines.append(f"{medal} {name} — **{row['cnt']}명** 초대")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"총 {len(top)}명의 초대 기록")
        await interaction.followup.send(embed=embed)

    @invite_group.command(name="info", description="내 초대 현황을 확인합니다")
    async def invite_info(self, interaction: discord.Interaction):
        guild_id  = str(interaction.guild_id)
        user_id   = str(interaction.user.id)
        cnt       = db.get_invite_count(guild_id, user_id)
        invitees  = db.get_my_invitees(guild_id, user_id, limit=5)

        embed = discord.Embed(
            title       = f"📨 {interaction.user.display_name} 님의 초대 현황",
            description = f"총 **{cnt}명** 초대했어요!",
            color       = discord.Color.blurple()
        )

        if invitees:
            names = "\n".join(
                f"• `{row['invitee_name']}`"
                for row in invitees
            )
            embed.add_field(
                name  = "최근 초대한 멤버",
                value = names,
                inline = False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @invite_group.command(name="check", description="특정 멤버의 초대 현황을 확인합니다")
    @app_commands.describe(member="확인할 멤버")
    async def invite_check(self, interaction: discord.Interaction, member: discord.Member):
        guild_id = str(interaction.guild_id)
        user_id  = str(member.id)
        cnt      = db.get_invite_count(guild_id, user_id)

        await interaction.response.send_message(
            f"📨 {member.mention} 님은 총 **{cnt}명** 초대했어요!",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(InviteCog(bot))
