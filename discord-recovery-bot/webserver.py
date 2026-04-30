"""
webserver.py ─ FastAPI 웹서버
  - OAuth2 콜백 (인증 버튼)
  - 디스코드 관리자 로그인
  - 웹 관리 패널 API
"""

import asyncio
import logging
import os
import secrets
from datetime import datetime
from functools import wraps

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, BOT_TOKEN

log = logging.getLogger(__name__)

app = FastAPI(title="RecoveryBot 관리 패널")

# 정적 파일 & 템플릿
BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# 세션 저장소 (메모리)
sessions: dict[str, dict] = {}   # session_id → { user_id, username, guilds, ... }

DISCORD_API = "https://discord.com/api/v10"
# 관리 패널 전용 OAuth2 리다이렉트
ADMIN_REDIRECT = os.getenv("ADMIN_REDIRECT_URI", REDIRECT_URI.replace("/callback", "/admin/callback"))


# ══════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════

def get_session(request: Request) -> dict | None:
    sid = request.cookies.get("session_id")
    return sessions.get(sid) if sid else None


def require_login(request: Request) -> dict:
    s = get_session(request)
    if not s:
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
    return s


# ══════════════════════════════════════════════════
#  루트
# ══════════════════════════════════════════════════

@app.get("/")
async def root():
    return HTMLResponse(
        "<h2 style='font-family:sans-serif;text-align:center;margin-top:20%'>"
        "RecoveryBot 서버 정상 작동 중 ✅<br>"
        "<a href='/admin' style='font-size:16px;color:#7289DA'>관리 패널 →</a></h2>"
    )


# ══════════════════════════════════════════════════
#  인증 버튼 OAuth2 콜백 (/callback)
# ══════════════════════════════════════════════════

def _html(title: str, icon: str, msg: str, sub: str = "", badge: str = "", badge_color: str = "#43B581") -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#23272A;display:flex;justify-content:center;align-items:center;min-height:100vh;font-family:'Segoe UI',sans-serif}}
.card{{background:#2C2F33;border-radius:16px;padding:48px 40px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.4);max-width:420px;width:90%}}
.icon{{font-size:64px;margin-bottom:16px}}
h1{{color:#fff;font-size:24px;margin-bottom:10px}}
p{{color:#B9BBBE;font-size:15px;line-height:1.6}}
.badge{{display:inline-block;background:{badge_color};color:#fff;padding:6px 18px;border-radius:20px;font-size:13px;margin-top:20px}}
.close{{color:#72767D;font-size:13px;margin-top:16px}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{msg}</p>
  {"<p>" + sub + "</p>" if sub else ""}
  {"<div class='badge'>" + badge + "</div>" if badge else ""}
  <p class="close">이 창을 닫고 디스코드로 돌아가세요</p>
</div>
</body>
</html>"""


@app.get("/callback")
async def oauth_callback(request: Request):
    code     = request.query_params.get("code")
    guild_id = request.query_params.get("state")

    if not code:
        return HTMLResponse(_html("인증 실패", "❌", "인증 코드가 없습니다.", badge_color="#F04747"), status_code=400)

    async with httpx.AsyncClient() as client:
        # 1. code → access_token
        token_res = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code, "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if token_res.status_code != 200:
            log.error("토큰 교환 실패: %s", token_res.text)
            return HTMLResponse(_html("인증 실패", "❌", "토큰 교환 실패", badge_color="#F04747"), status_code=400)

        token_data    = token_res.json()
        access_token  = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in    = token_data.get("expires_in", 604800)

        # 2. 유저 정보
        user_res = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_res.status_code != 200:
            return HTMLResponse(_html("인증 실패", "❌", "유저 정보 조회 실패", badge_color="#F04747"), status_code=400)

        user_data = user_res.json()
        user_id   = user_data["id"]
        username  = user_data["username"]

        # 3. DB 저장
        db.save_token(user_id, username, access_token, refresh_token, expires_in, guild_id or "")
        log.info("OAuth 토큰 저장: %s (%s)", username, user_id)

        # 4. 서버에 멤버 추가
        if guild_id:
            join_res = await client.put(
                f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}",
                headers={"Authorization": f"Bot {BOT_TOKEN}"},
                json={"access_token": access_token}
            )
            log.info("서버 참가 결과: %s (user=%s guild=%s)", join_res.status_code, username, guild_id)

    return HTMLResponse(_html(
        "인증 완료!", "✅",
        f"<span style='color:#7289DA;font-weight:bold'>{username}</span> 님,<br>서버 인증이 완료되었습니다!",
        badge="✓ 멤버 역할 부여됨"
    ))


# ══════════════════════════════════════════════════
#  관리 패널 로그인
# ══════════════════════════════════════════════════

@app.get("/admin/login")
async def admin_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "client_id": CLIENT_ID,
                                                      "redirect_uri": ADMIN_REDIRECT})


@app.get("/admin/callback")
async def admin_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        return RedirectResponse("/admin/login")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code, "redirect_uri": ADMIN_REDIRECT,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if token_res.status_code != 200:
            return RedirectResponse("/admin/login?error=1")

        token_data   = token_res.json()
        access_token = token_data.get("access_token", "")

        user_res = await client.get(f"{DISCORD_API}/users/@me",
                                    headers={"Authorization": f"Bearer {access_token}"})
        if user_res.status_code != 200:
            return RedirectResponse("/admin/login?error=1")

        user_data = user_res.json()

        # 관리 서버에서 봇이 있는 길드 가져오기
        guilds_res = await client.get(f"{DISCORD_API}/users/@me/guilds",
                                      headers={"Authorization": f"Bearer {access_token}"})
        guilds = guilds_res.json() if guilds_res.status_code == 200 else []

        # 관리자 권한 있는 길드만 필터
        admin_guilds = [g for g in guilds if (int(g.get("permissions", 0)) & 0x8) == 0x8]

    sid = secrets.token_hex(32)
    sessions[sid] = {
        "user_id":  user_data["id"],
        "username": user_data["username"],
        "avatar":   user_data.get("avatar"),
        "guilds":   admin_guilds,
        "token":    access_token,
    }

    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie("session_id", sid, httponly=True, max_age=86400 * 7)
    return resp


@app.get("/admin/logout")
async def admin_logout(request: Request):
    sid = request.cookies.get("session_id")
    if sid:
        sessions.pop(sid, None)
    resp = RedirectResponse("/admin/login")
    resp.delete_cookie("session_id")
    return resp


# ══════════════════════════════════════════════════
#  관리 패널 메인 페이지들
# ══════════════════════════════════════════════════

@app.get("/admin")
async def admin_index(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": s})


@app.get("/admin/backup")
async def admin_backup(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("backup.html", {"request": request, "user": s})


@app.get("/admin/members")
async def admin_members(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("members.html", {"request": request, "user": s})


@app.get("/admin/shop")
async def admin_shop(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("shop.html", {"request": request, "user": s})


@app.get("/admin/invite")
async def admin_invite(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("invite.html", {"request": request, "user": s})


@app.get("/admin/settings")
async def admin_settings(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse("settings.html", {"request": request, "user": s})


# ══════════════════════════════════════════════════
#  REST API (JSON)
# ══════════════════════════════════════════════════

def _check(request: Request) -> dict:
    s = get_session(request)
    if not s:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return s


# ─── 대시보드 API ────────────────────────────────

@app.get("/api/dashboard")
async def api_dashboard(request: Request, guild_id: str = ""):
    _check(request)
    backups  = db.list_backups(guild_id) if guild_id else []
    tokens   = db.get_token_count(guild_id) if guild_id else 0
    revenue  = db.get_total_revenue(guild_id) if guild_id else 0
    products = db.get_products(guild_id) if guild_id else []
    return {
        "backup_count":   len(backups),
        "latest_backup":  backups[0] if backups else None,
        "token_count":    tokens,
        "revenue":        revenue,
        "product_count":  len(products),
    }


# ─── 백업 API ────────────────────────────────────

@app.get("/api/backups")
async def api_backups(request: Request, guild_id: str):
    _check(request)
    return db.list_backups(guild_id)


@app.delete("/api/backups/{backup_id}")
async def api_delete_backup(backup_id: int, request: Request):
    _check(request)
    try:
        db.delete_backup(backup_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 상품 API ────────────────────────────────────

@app.get("/api/products")
async def api_products(request: Request, guild_id: str):
    _check(request)
    return db.get_products(guild_id)


@app.post("/api/products")
async def api_add_product(request: Request):
    _check(request)
    body = await request.json()
    pid = db.add_product(
        guild_id = body["guild_id"],
        name     = body["name"],
        price    = int(body["price"]),
        role_id  = str(body["role_id"])
    )
    return {"ok": True, "id": pid}


@app.put("/api/products/{product_id}")
async def api_update_product(product_id: int, request: Request):
    _check(request)
    body = await request.json()
    db.update_product(product_id, body["name"], int(body["price"]), str(body["role_id"]))
    return {"ok": True}


@app.delete("/api/products/{product_id}")
async def api_delete_product(product_id: int, request: Request):
    _check(request)
    db.delete_product(product_id)
    return {"ok": True}


# ─── 충전 신청 API ───────────────────────────────

@app.get("/api/charges")
async def api_charges(request: Request, guild_id: str, status: str = "pending"):
    _check(request)
    if status == "all":
        return db.get_all_charge_requests(guild_id)
    return db.get_charge_requests(guild_id, status)


@app.post("/api/charges/{charge_id}/approve")
async def api_approve_charge(charge_id: int, request: Request):
    _check(request)
    body   = await request.json()
    amount = int(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="금액을 입력하세요")
    result = db.approve_charge(charge_id, amount)
    if not result:
        raise HTTPException(status_code=404, detail="충전 신청을 찾을 수 없습니다")
    return {"ok": True, "user_id": result["user_id"], "amount": amount}


@app.post("/api/charges/{charge_id}/reject")
async def api_reject_charge(charge_id: int, request: Request):
    _check(request)
    db.reject_charge(charge_id)
    return {"ok": True}


# ─── 구매 내역 API ───────────────────────────────

@app.get("/api/purchases")
async def api_purchases(request: Request, guild_id: str):
    _check(request)
    return db.get_purchases(guild_id)


# ─── 잔액 API ────────────────────────────────────

@app.get("/api/balances")
async def api_balances(request: Request, guild_id: str):
    _check(request)
    return db.get_all_balances(guild_id)


@app.post("/api/balances/give")
async def api_give_balance(request: Request):
    _check(request)
    body = await request.json()
    db.update_balance(str(body["user_id"]), body["guild_id"], int(body["amount"]))
    return {"ok": True}


# ─── 초대 로그 API ───────────────────────────────

@app.get("/api/invites")
async def api_invites(request: Request, guild_id: str):
    _check(request)
    return {
        "top":  db.get_invite_top(guild_id, limit=20),
        "logs": db.get_invite_logs(guild_id),
    }


# ─── OAuth 토큰 API ──────────────────────────────

@app.get("/api/tokens")
async def api_tokens(request: Request, guild_id: str):
    _check(request)
    tokens = db.get_all_tokens(guild_id)
    # access_token 은 숨기고 기본 정보만 반환
    return [{"user_id": t["user_id"], "username": t["username"],
             "saved_at": t["saved_at"]} for t in tokens]


# ─── 멤버 강제 재참가 API ────────────────────────

@app.post("/api/rejoin")
async def api_rejoin(request: Request):
    _check(request)
    body     = await request.json()
    guild_id = body["guild_id"]
    tokens   = db.get_all_tokens(guild_id)

    success, fail = 0, 0
    async with httpx.AsyncClient() as client:
        for t in tokens:
            res = await client.put(
                f"{DISCORD_API}/guilds/{guild_id}/members/{t['user_id']}",
                headers={"Authorization": f"Bot {BOT_TOKEN}"},
                json={"access_token": t["access_token"]}
            )
            if res.status_code in (200, 201, 204):
                success += 1
            else:
                fail += 1

    return {"ok": True, "success": success, "fail": fail}
