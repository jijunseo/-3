"""
cogs/server_cog.py ─ 서버 현황
  /server status ─ 서버 인원 현황
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging

log = logging.getLogger(__name__)


class ServerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    server_group = app_commands.Group(name="server", description="서버 정보")

    @server_group.command(name="status", description="서버 인원 현황을 확인합니다")
    async def server_status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild

        # 멤버 통계
        total       = guild.member_count
        bots        = sum(1 for m in guild.members if m.bot)
        humans      = total - bots
        online      = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle        = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd         = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline     = sum(1 for m in guild.members if m.status == discord.Status.offline)

        # 채널 통계
        text_ch   = len(guild.text_channels)
        voice_ch  = len(guild.voice_channels)
        category  = len(guild.categories)

        # 역할 통계
        roles     = len(guild.roles) - 1  # @everyone 제외

        # 부스트
        boost_lvl = guild.premium_tier
        boosters  = guild.premium_subscription_count or 0

        embed = discord.Embed(
            title       = f"📊 {guild.name} 서버 현황",
            color       = discord.Color.blurple(),
            timestamp   = datetime.utcnow()
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        # 멤버 현황
        embed.add_field(
            name  = "👥 멤버 현황",
            value = (
                f"전체: **{total:,}명**\n"
                f"일반: `{humans:,}명`\n"
                f"봇: `{bots:,}명`"
            ),
            inline = True
        )

        # 접속 현황
        embed.add_field(
            name  = "🟢 접속 현황",
            value = (
                f"🟢 온라인: `{online:,}명`\n"
                f"🟡 자리비움: `{idle:,}명`\n"
                f"🔴 방해금지: `{dnd:,}명`\n"
                f"⚫ 오프라인: `{offline:,}명`"
            ),
            inline = True
        )

        # 채널 현황
        embed.add_field(
            name  = "📢 채널 현황",
            value = (
                f"📝 텍스트: `{text_ch}개`\n"
                f"🔊 음성: `{voice_ch}개`\n"
                f"📁 카테고리: `{category}개`"
            ),
            inline = True
        )

        # 서버 정보
        embed.add_field(
            name  = "🏠 서버 정보",
            value = (
                f"역할 수: `{roles}개`\n"
                f"부스트 레벨: `{boost_lvl}레벨`\n"
                f"부스터: `{boosters}명`"
            ),
            inline = True
        )

        # 서버 개설일
        created = guild.created_at.strftime("%Y년 %m월 %d일")
        embed.add_field(
            name  = "📅 서버 개설일",
            value = f"`{created}`",
            inline = True
        )

        # 서버 소유자
        embed.add_field(
            name  = "👑 서버 소유자",
            value = f"{guild.owner.mention}" if guild.owner else "알 수 없음",
            inline = True
        )

        embed.set_footer(text=f"서버 ID: {guild.id}")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerCog(bot))
