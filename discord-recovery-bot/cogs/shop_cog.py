"""
cogs/shop_cog.py ─ 자판기 시스템
  - 충전하기 버튼 (핀번호 모달 입력)
  - 상품 구매 버튼 (역할 자동 부여)
  - 잔액 확인 버튼
  - /shop panel  ─ 자판기 패널 전송
  - /shop setup  ─ 자판기 채널 설정
"""

import discord
from discord import app_commands
from discord.ext import commands
import database as db
from config import ADMIN_ROLE_NAME
import logging

log = logging.getLogger(__name__)


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if ADMIN_ROLE_NAME:
            has_role = any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles)
            if not has_role and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
                return False
        else:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
                return False
        return True
    return app_commands.check(predicate)


# ══════════════════════════════════════════════════
#  핀번호 입력 모달
# ══════════════════════════════════════════════════

class ChargeModal(discord.ui.Modal, title="💳 문상 충전하기"):
    pin = discord.ui.TextInput(
        label       = "상품권 핀번호",
        placeholder = "1234-1234-1234-1234 (컬쳐랜드 16자리)",
        min_length  = 16,
        max_length  = 80,
        style       = discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        pin_value = self.pin.value.strip()
        user_id   = str(interaction.user.id)
        guild_id  = str(interaction.guild_id)

        # 중복 핀번호 체크
        if db.check_pin_used(pin_value):
            return await interaction.response.send_message(
                "❌ 이미 사용된 핀번호입니다.", ephemeral=True
            )

        # 충전 신청 DB 저장
        charge_id = db.save_charge_request(
            user_id   = user_id,
            username  = str(interaction.user),
            guild_id  = guild_id,
            pin       = pin_value
        )

        # 관리자 알림 채널 전송
        await notify_admin_charge(interaction.guild, interaction.user, pin_value, charge_id)

        embed = discord.Embed(
            title       = "✅ 충전 신청 완료",
            description = (
                f"핀번호가 접수되었습니다!\n\n"
                f"신청 번호: `#{charge_id}`\n"
                f"관리자 확인 후 자동으로 충전됩니다.\n"
                f"보통 **5~10분** 내에 처리돼요 😊"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def notify_admin_charge(guild, user, pin, charge_id):
    """관리자 로그 채널에 충전 신청 알림"""
    from config import LOG_CHANNEL
    if not LOG_CHANNEL:
        return
    ch = guild.get_channel(LOG_CHANNEL)
    if not ch:
        return

    embed = discord.Embed(
        title       = f"💳 충전 신청 #{charge_id}",
        description = (
            f"신청자: {user.mention} (`{user}`)\n"
            f"핀번호: `{pin}`\n\n"
            f"관리 패널에서 승인/거절하세요!"
        ),
        color = discord.Color.orange()
    )
    embed.set_footer(text="웹 관리 패널 → 충전 관리에서 처리하세요")
    try:
        await ch.send(embed=embed)
    except Exception as e:
        log.warning("관리자 알림 실패: %s", e)


# ══════════════════════════════════════════════════
#  상품 구매 확인 모달
# ══════════════════════════════════════════════════

class BuyConfirmView(discord.ui.View):
    def __init__(self, product: dict):
        super().__init__(timeout=30)
        self.product = product

    @discord.ui.button(label="✅ 구매 확인", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        product  = self.product
        user_id  = str(interaction.user.id)
        guild_id = str(interaction.guild_id)

        # 잔액 확인
        balance = db.get_balance(user_id, guild_id)
        if balance < product["price"]:
            return await interaction.response.send_message(
                f"❌ 잔액이 부족해요!\n"
                f"현재 잔액: `{balance:,}원`\n"
                f"필요 금액: `{product['price']:,}원`",
                ephemeral=True
            )

        # 역할 부여
        role = interaction.guild.get_role(int(product["role_id"]))
        if not role:
            return await interaction.response.send_message(
                "❌ 역할을 찾을 수 없어요. 관리자에게 문의하세요.", ephemeral=True
            )

        if role in interaction.user.roles:
            return await interaction.response.send_message(
                f"❌ 이미 `{role.name}` 역할을 가지고 있어요!", ephemeral=True
            )

        try:
            await interaction.user.add_roles(role, reason="자판기 구매")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ 역할 부여 권한이 없어요. 관리자에게 문의하세요.", ephemeral=True
            )

        # 잔액 차감
        db.update_balance(user_id, guild_id, -product["price"])

        # 구매 기록
        db.save_purchase(
            user_id    = user_id,
            username   = str(interaction.user),
            guild_id   = guild_id,
            product_id = product["id"],
            price      = product["price"]
        )

        new_balance = db.get_balance(user_id, guild_id)

        embed = discord.Embed(
            title       = "🎉 구매 완료!",
            description = (
                f"**{product['name']}** 구매 완료!\n\n"
                f"차감 금액: `{product['price']:,}원`\n"
                f"남은 잔액: `{new_balance:,}원`\n\n"
                f"역할 **{role.name}** 이 부여되었어요! 🎊"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("구매가 취소되었습니다.", ephemeral=True)


# ══════════════════════════════════════════════════
#  자판기 패널 View
# ══════════════════════════════════════════════════

class ShopView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="💳 충전하기", style=discord.ButtonStyle.primary, custom_id="shop_charge", emoji="💳")
    async def charge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChargeModal())

    @discord.ui.button(label="🛒 상품 구매", style=discord.ButtonStyle.success, custom_id="shop_buy", emoji="🛒")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        products = db.get_products(guild_id)

        if not products:
            return await interaction.response.send_message(
                "❌ 등록된 상품이 없어요. 관리자에게 문의하세요.", ephemeral=True
            )

        # 상품 선택 드롭다운
        view = ProductSelectView(products)
        embed = discord.Embed(
            title       = "🛒 상품 선택",
            description = "구매할 상품을 선택해주세요!",
            color       = discord.Color.blurple()
        )
        for p in products:
            embed.add_field(
                name  = p["name"],
                value = f"가격: `{p['price']:,}원`",
                inline = True
            )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="💰 잔액 확인", style=discord.ButtonStyle.secondary, custom_id="shop_balance", emoji="💰")
    async def balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id  = str(interaction.user.id)
        guild_id = str(interaction.guild_id)
        balance  = db.get_balance(user_id, guild_id)

        embed = discord.Embed(
            title       = "💰 내 잔액",
            description = f"현재 잔액: **`{balance:,}원`**",
            color       = discord.Color.gold()
        )
        embed.set_footer(text="충전하기 버튼으로 문상 충전 가능")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════
#  상품 선택 드롭다운
# ══════════════════════════════════════════════════

class ProductSelectView(discord.ui.View):
    def __init__(self, products: list):
        super().__init__(timeout=30)
        self.add_item(ProductSelect(products))


class ProductSelect(discord.ui.Select):
    def __init__(self, products: list):
        options = [
            discord.SelectOption(
                label = p["name"],
                value = str(p["id"]),
                description = f"{p['price']:,}원"
            )
            for p in products[:25]
        ]
        super().__init__(placeholder="상품을 선택하세요...", options=options)
        self.products = {str(p["id"]): p for p in products}

    async def callback(self, interaction: discord.Interaction):
        product = self.products.get(self.values[0])
        if not product:
            return await interaction.response.send_message("❌ 상품을 찾을 수 없어요.", ephemeral=True)

        user_id  = str(interaction.user.id)
        guild_id = str(interaction.guild_id)
        balance  = db.get_balance(user_id, guild_id)

        embed = discord.Embed(
            title       = f"🛒 {product['name']} 구매 확인",
            description = (
                f"상품: **{product['name']}**\n"
                f"가격: `{product['price']:,}원`\n"
                f"현재 잔액: `{balance:,}원`\n"
                f"구매 후 잔액: `{balance - product['price']:,}원`\n\n"
                f"구매하시겠습니까?"
            ),
            color = discord.Color.orange()
        )
        await interaction.response.send_message(
            embed = embed,
            view  = BuyConfirmView(product),
            ephemeral = True
        )


# ══════════════════════════════════════════════════
#  ShopCog
# ══════════════════════════════════════════════════

class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.shop_channels: dict[int, int] = {}

    shop_group = app_commands.Group(name="shop", description="자판기 관리")

    @shop_group.command(name="setup", description="자판기 채널을 설정합니다")
    @app_commands.describe(channel="자판기 패널을 올릴 채널")
    @is_admin()
    async def shop_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.shop_channels[interaction.guild_id] = channel.id
        embed = discord.Embed(
            title       = "✅ 자판기 설정 완료",
            description = (
                f"자판기 채널: {channel.mention}\n\n"
                f"패널 올리기: `/shop panel`\n"
                f"상품 추가: 웹 관리 패널에서 설정"
            ),
            color = discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop_group.command(name="panel", description="자판기 패널을 채널에 전송합니다")
    @is_admin()
    async def shop_panel(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        if gid not in self.shop_channels:
            return await interaction.response.send_message(
                "❌ 먼저 `/shop setup` 으로 채널을 설정해주세요!", ephemeral=True
            )

        ch = interaction.guild.get_channel(self.shop_channels[gid])
        if not ch:
            return await interaction.response.send_message("❌ 채널을 찾을 수 없어요!", ephemeral=True)

        guild_id = str(gid)
        products = db.get_products(guild_id)

        embed = discord.Embed(
            title       = "🏪 자판기",
            description = "버튼을 눌러 충전하거나 상품을 구매하세요!",
            color       = discord.Color.blurple()
        )

        if products:
            for p in products:
                embed.add_field(
                    name  = f"🎭 {p['name']}",
                    value = f"`{p['price']:,}원`",
                    inline = True
                )
        else:
            embed.add_field(name="상품 없음", value="관리자가 상품을 등록 중이에요!", inline=False)

        embed.set_footer(text="충전은 문화상품권(컬쳐랜드) 사용 가능")
        await ch.send(embed=embed, view=ShopView(gid))
        await interaction.response.send_message(
            f"✅ {ch.mention} 에 자판기 패널을 전송했어요!", ephemeral=True
        )

    @shop_group.command(name="balance", description="특정 유저의 잔액을 확인합니다")
    @app_commands.describe(member="잔액 확인할 멤버")
    @is_admin()
    async def shop_balance_admin(self, interaction: discord.Interaction, member: discord.Member):
        balance = db.get_balance(str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"💰 {member.mention} 잔액: `{balance:,}원`", ephemeral=True
        )

    @shop_group.command(name="give", description="유저에게 포인트를 지급합니다")
    @app_commands.describe(member="지급할 멤버", amount="지급할 금액")
    @is_admin()
    async def shop_give(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        db.update_balance(str(member.id), str(interaction.guild_id), amount)
        balance = db.get_balance(str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ {member.mention} 에게 `{amount:,}원` 지급 완료!\n현재 잔액: `{balance:,}원`",
            ephemeral=True
        )

    @shop_group.command(name="take", description="유저의 포인트를 차감합니다")
    @app_commands.describe(member="차감할 멤버", amount="차감할 금액")
    @is_admin()
    async def shop_take(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        db.update_balance(str(member.id), str(interaction.guild_id), -amount)
        balance = db.get_balance(str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ {member.mention} 에서 `{amount:,}원` 차감 완료!\n현재 잔액: `{balance:,}원`",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
