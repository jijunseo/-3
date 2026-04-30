"""
recovery_engine.py ─ DB 데이터를 받아 실제 디스코드 서버를 복구하는 엔진
"""

import json
import asyncio
import logging
import discord
from typing import Optional

log = logging.getLogger(__name__)


class RecoveryEngine:
    """
    load_backup() 로 얻은 dict 를 받아
    Discord guild 에 역할 / 카테고리 / 채널 / 멤버역할 을 복구합니다.
    """

    def __init__(self, guild: discord.Guild, data: dict):
        self.guild   = guild
        self.data    = data
        self.meta    = data["meta"]
        # 복구 진행 상황 로그 (나중에 embed 로 출력)
        self.log_lines: list[str] = []

    # ══════════════════════════════════════════════════
    #  외부 진입점
    # ══════════════════════════════════════════════════

    async def run(self, restore_roles=True, restore_channels=True,
                  restore_members=True) -> list[str]:
        """
        전체 복구 실행 후 결과 로그 라인 목록을 반환합니다.
        """
        self.log_lines.clear()
        self._info(f"🔁 복구 시작 — 백업 ID `{self.meta['id']}` "
                   f"(`{self.meta['created_at']}` UTC)")

        if restore_roles:
            await self._restore_roles()

        if restore_channels:
            await self._restore_categories()
            await self._restore_channels()

        if restore_members:
            await self._restore_member_roles()

        self._info("✅ 복구 완료!")
        return self.log_lines

    # ══════════════════════════════════════════════════
    #  역할 복구
    # ══════════════════════════════════════════════════

    async def _restore_roles(self):
        self._info("── 역할 복구 시작")
        existing = {r.name: r for r in self.guild.roles}
        # position 오름차순(낮은 역할부터) 으로 생성해야 충돌 없음
        sorted_roles = sorted(self.data["roles"], key=lambda r: r["position"])

        for r in sorted_roles:
            name = r["name"]
            try:
                if name in existing:
                    # 이미 있으면 속성만 업데이트
                    role = existing[name]
                    await role.edit(
                        color       = discord.Color(r["color"]),
                        hoist       = bool(r["hoist"]),
                        mentionable = bool(r["mentionable"]),
                        permissions = discord.Permissions(r["permissions"]),
                        reason      = "RecoveryBot: 역할 복구"
                    )
                    self._info(f"  🔄 역할 업데이트: `{name}`")
                else:
                    # 없으면 새로 생성
                    await self.guild.create_role(
                        name        = name,
                        color       = discord.Color(r["color"]),
                        hoist       = bool(r["hoist"]),
                        mentionable = bool(r["mentionable"]),
                        permissions = discord.Permissions(r["permissions"]),
                        reason      = "RecoveryBot: 역할 생성"
                    )
                    self._info(f"  ✨ 역할 생성: `{name}`")
                await asyncio.sleep(0.5)   # rate-limit 방지
            except discord.Forbidden:
                self._warn(f"  ⚠️  역할 권한 없음: `{name}` (스킵)")
            except Exception as e:
                self._warn(f"  ❌ 역할 오류 `{name}`: {e}")

    # ══════════════════════════════════════════════════
    #  카테고리 복구
    # ══════════════════════════════════════════════════

    async def _restore_categories(self):
        self._info("── 카테고리 복구 시작")
        existing = {c.name.lower(): c for c in self.guild.categories}
        # old_cat_id → new CategoryChannel 매핑 (채널 복구 시 사용)
        self._cat_map: dict[str, discord.CategoryChannel] = {}

        for cat in sorted(self.data["categories"], key=lambda c: c["position"]):
            name = cat["name"]
            try:
                overwrites = await self._build_overwrites(cat["overwrites"])
                if name.lower() in existing:
                    c = existing[name.lower()]
                    self._info(f"  🔄 카테고리 재사용: `{name}`")
                else:
                    c = await self.guild.create_category(
                        name       = name,
                        overwrites = overwrites,
                        reason     = "RecoveryBot: 카테고리 복구"
                    )
                    self._info(f"  ✨ 카테고리 생성: `{name}`")
                self._cat_map[cat["cat_id"]] = c
                await asyncio.sleep(0.4)
            except discord.Forbidden:
                self._warn(f"  ⚠️  카테고리 권한 없음: `{name}` (스킵)")
            except Exception as e:
                self._warn(f"  ❌ 카테고리 오류 `{name}`: {e}")

    # ══════════════════════════════════════════════════
    #  채널 복구
    # ══════════════════════════════════════════════════

    async def _restore_channels(self):
        self._info("── 채널 복구 시작")
        existing_text  = {c.name.lower(): c for c in self.guild.text_channels}
        existing_voice = {c.name.lower(): c for c in self.guild.voice_channels}
        existing_forum = {c.name.lower(): c for c in self.guild.forums}

        for ch in sorted(self.data["channels"], key=lambda c: c["position"]):
            name     = ch["name"]
            ch_type  = ch["type"]
            cat_obj  = self._cat_map.get(ch["category_id"]) if ch.get("category_id") else None

            try:
                overwrites = await self._build_overwrites(ch["overwrites"])

                if "text" in ch_type:
                    if name.lower() in existing_text:
                        self._info(f"  🔄 텍스트채널 재사용: `#{name}`")
                    else:
                        await self.guild.create_text_channel(
                            name          = name,
                            category      = cat_obj,
                            topic         = ch["topic"] or "",
                            nsfw          = bool(ch["nsfw"]),
                            slowmode_delay= ch["slowmode"],
                            overwrites    = overwrites,
                            reason        = "RecoveryBot: 텍스트채널 복구"
                        )
                        self._info(f"  ✨ 텍스트채널 생성: `#{name}`")

                elif "voice" in ch_type:
                    if name.lower() in existing_voice:
                        self._info(f"  🔄 음성채널 재사용: `🔊{name}`")
                    else:
                        await self.guild.create_voice_channel(
                            name       = name,
                            category   = cat_obj,
                            bitrate    = ch["bitrate"],
                            user_limit = ch["user_limit"],
                            overwrites = overwrites,
                            reason     = "RecoveryBot: 음성채널 복구"
                        )
                        self._info(f"  ✨ 음성채널 생성: `🔊{name}`")

                elif "forum" in ch_type:
                    if name.lower() in existing_forum:
                        self._info(f"  🔄 포럼채널 재사용: `📋{name}`")
                    else:
                        await self.guild.create_forum(
                            name      = name,
                            category  = cat_obj,
                            overwrites= overwrites,
                            reason    = "RecoveryBot: 포럼채널 복구"
                        )
                        self._info(f"  ✨ 포럼채널 생성: `📋{name}`")

                else:
                    self._warn(f"  ⚠️  알 수 없는 채널 타입 `{ch_type}`: `{name}` (스킵)")

                await asyncio.sleep(0.5)

            except discord.Forbidden:
                self._warn(f"  ⚠️  채널 권한 없음: `{name}` (스킵)")
            except Exception as e:
                self._warn(f"  ❌ 채널 오류 `{name}`: {e}")

    # ══════════════════════════════════════════════════
    #  멤버 역할 복구
    # ══════════════════════════════════════════════════

    async def _restore_member_roles(self):
        self._info("── 멤버 역할 복구 시작")
        # 현재 서버 역할: name → Role
        role_by_name = {r.name: r for r in self.guild.roles}
        # 백업 당시 role_id → role_name 매핑 (roles 테이블에서)
        old_id_to_name = {r["role_id"]: r["name"] for r in self.data["roles"]}

        recovered = 0
        skipped   = 0

        for m in self.data["member_roles"]:
            member = self.guild.get_member(int(m["member_id"]))
            if not member:
                skipped += 1
                continue

            old_role_ids: list[str] = json.loads(m["role_ids"])
            roles_to_add: list[discord.Role] = []

            for old_id in old_role_ids:
                rname = old_id_to_name.get(old_id)
                if rname and rname in role_by_name:
                    new_role = role_by_name[rname]
                    if new_role not in member.roles:
                        roles_to_add.append(new_role)

            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason="RecoveryBot: 멤버 역할 복구")
                    recovered += 1
                    await asyncio.sleep(0.3)
                except discord.Forbidden:
                    self._warn(f"  ⚠️  멤버 역할 추가 권한 없음: `{m['member_name']}`")
                except Exception as e:
                    self._warn(f"  ❌ 멤버 역할 오류 `{m['member_name']}`: {e}")

        self._info(f"  👥 멤버 역할 복구 완료 — 복구:{recovered}명 / 서버에없음:{skipped}명")

    # ══════════════════════════════════════════════════
    #  내부 유틸
    # ══════════════════════════════════════════════════

    async def _build_overwrites(self, raw: str) -> dict:
        """JSON 문자열 → discord PermissionOverwrite dict"""
        data = json.loads(raw) if isinstance(raw, str) else raw
        result = {}
        for target_id, val in data.items():
            allow = discord.Permissions(val["allow"])
            deny  = discord.Permissions(val["deny"])
            ow    = discord.PermissionOverwrite.from_pair(allow, deny)

            if val["type"] == "role":
                obj = self.guild.get_role(int(target_id))
            else:
                obj = self.guild.get_member(int(target_id))

            if obj:
                result[obj] = ow
        return result

    def _info(self, msg: str):
        log.info(msg)
        self.log_lines.append(msg)

    def _warn(self, msg: str):
        log.warning(msg)
        self.log_lines.append(msg)
