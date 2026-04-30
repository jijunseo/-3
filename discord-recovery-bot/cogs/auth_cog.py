"""
cogs/auth_cog.py ─ 인증 버튼 시스템
  - 새 멤버 입장 시 인증 채널에 버튼 자동 전송
  - 버튼 클릭 시 OAuth2 로그인 링크 제공
  - /auth setup   ─ 인증 채널 설정
  - /auth panel   ─ 인증 패널 수동 전송
"""

import discord
from discord import app_commands
from discord.ext import commands
from config import CLIENT_ID, REDIRECT_URI, ADMIN_ROLE_NAME
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


# ══════════════════════════════════════════════════
#  인증 버튼 View
# ══════════════════════════════════════════════════

class AuthView(discord.ui.View):
    """인증 패널에 붙는 버튼 (persistent = 봇 재시작해도 유지)"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # 영구 유지
        self.guild_id = guild_id

        # OAuth2 링크 버튼 (링크 버튼은 callback 없이 바로 URL 이동)
        oauth_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={CLIENT_ID}"
            f"&redirect_uri={REDIRECT_URI}"
            f"&response_type=code"
            f"&scope=identify+guilds.join"
            f"&state={guild_id}"
        )
        self.add_item(
            discord.ui.Button(
                label    = "✅ 인증하기",
                style    = discord.ButtonStyle.link,
                url      = oauth_url,
                emoji    = "🔐"
            )
        )


# ══════════════════════════════════════════════════
#  AuthCog
# ══════════════════════════════════════════════════

class AuthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # { guild_id: channel_id }  인증 채널 설정 저장
        self.auth_channels: dict[int, int] = {}
        # { guild_id: role_name }   인증 완료 후 부여할 역할
        self.member_roles: dict[int, str]  = {}

    # ══════════════════════════════════════════════════
    #  새 멤버 입장 시 인증 버튼 자동 전송
    # ══════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gid = member.guild.id

        # 인증 채널 설정 안 되어 있으면 무시
        if gid not in self.auth_channels:
            return

        ch = member.guild.get_channel(self.auth_channels[gid])
        if not ch:
            return

        embed = discord.Embed(
            title       = "🔐 서버 인증",
            description = (
                f"{member.mention} 님, 환영합니다!\n\n"
                f"아래 **인증하기** 버튼을 눌러\n"
                f"디스코드 로그인을 완료하면\n"
                f"채널이 열립니다! 😊"
            ),
            color = discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="인증은 1회만 하면 됩니다")

        try:
            await ch.send(
                content = f"{member.mention}",
                embed   = embed,
                view    = AuthView(gid)
            )
        except Exception as e:
            log.warning("인증 버튼 전송 실패: %s", e)

    # ══════════════════════════════════════════════════
    #  /auth 슬래시 커맨드
    # ══════════════════════════════════════════════════

    auth_group = app_commands.Group(name="auth", description="인증 시스템 설정")

    # ─────────────────────────────────────────
    #  /auth setup
    # ─────────────────────────────────────────
    @auth_group.command(name="setup", description="인증 채널과 역할을 설정합니다")
    @app_commands.describe(
        channel     = "인증 버튼을 보낼 채널",
        member_role = "인증 완료 후 부여할 역할 이름 (예: 멤버)"
    )
    @is_admin()
    async def auth_setup(
        self,
        interaction : discord.Interaction,
        channel     : discord.TextChannel,
        member_role : str = "멤버"
    ):
        gid = interaction.guild_id
        self.auth_channels[gid] = channel.id
        self.member_roles[gid]  = member_role

        embed = discord.Embed(
            title       = "✅ 인증 시스템 설정 완료",
            description = (
                f"인증 채널: {channel.mention}\n"
                f"부여 역할: `{member_role}`\n\n"
                f"이제 새 멤버가 입장하면\n"
                f"{channel.mention} 채널에\n"
                f"인증 버튼이 자동으로 생성돼요!\n\n"
                f"지금 바로 패널 올리려면:\n"
                f"`/auth panel` 입력하세요"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────
    #  /auth panel
    # ─────────────────────────────────────────
    @auth_group.command(name="panel", description="인증 패널을 채널에 전송합니다")
    @is_admin()
    async def auth_panel(self, interaction: discord.Interaction):
        gid = interaction.guild_id

        if gid not in self.auth_channels:
            return await interaction.response.send_message(
                "❌ 먼저 `/auth setup` 으로 인증 채널을 설정해주세요!", ephemeral=True
            )

        ch = interaction.guild.get_channel(self.auth_channels[gid])
        if not ch:
            return await interaction.response.send_message(
                "❌ 설정된 채널을 찾을 수 없어요. `/auth setup` 다시 해주세요!", ephemeral=True
            )

        embed = discord.Embed(
            title       = "🔐 서버 인증",
            description = (
                "아래 **인증하기** 버튼을 클릭해서\n"
                "디스코드 로그인을 완료하면\n"
                "채널이 열립니다! 😊\n\n"
                "**인증은 1회만 하면 됩니다**"
            ),
            color = discord.Color.blurple()
        )
        embed.set_footer(text="서버 보안을 위해 인증이 필요합니다")

        await ch.send(embed=embed, view=AuthView(gid))
        await interaction.response.send_message(
            f"✅ {ch.mention} 채널에 인증 패널을 전송했어요!", ephemeral=True
        )

    # ─────────────────────────────────────────
    #  /auth status
    # ─────────────────────────────────────────
    @auth_group.command(name="status", description="인증 시스템 현재 상태 확인")
    @is_admin()
    async def auth_status(self, interaction: discord.Interaction):
        gid     = interaction.guild_id
        enabled = gid in self.auth_channels
        ch      = interaction.guild.get_channel(self.auth_channels.get(gid, 0))
        role    = self.member_roles.get(gid, "미설정")

        embed = discord.Embed(
            title = "📊 인증 시스템 상태",
            color = discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(
            name  = "상태",
            value = "✅ 활성화" if enabled else "🔴 비활성화",
            inline = True
        )
        embed.add_field(
            name  = "인증 채널",
            value = ch.mention if ch else "❌ 미설정",
            inline = True
        )
        embed.add_field(
            name  = "부여 역할",
            value = f"`{role}`",
            inline = True
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuthCog(bot))
