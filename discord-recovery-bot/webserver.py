"""
webserver.py ─ FastAPI 웹서버
  - OAuth2 콜백 (인증 버튼 → 토큰 저장 → 역할 부여)
  - 관리 패널 (로그인, 대시보드, 백업, 자판기, 멤버, 초대, 설정)
  - REST API (봇·패널 공통 사용)
"""

import asyncio
import logging
import httpx
import json
import os
import secrets

from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import database as db
from config import (
    CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, BOT_TOKEN
)

log = logging.getLogger(__name__)

# ── FastAPI 앱 ────────────────────────────────────
app = FastAPI(title="RecoveryBot Panel")

# 템플릿 / 정적 파일
BASE_DIR  = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

static_dir = os.path.join(BASE_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ── 세션 (간이 메모리 세션) ────────────────────────
sessions: dict[str, dict] = {}   # token → {user_id, username, guilds, ...}

ADMIN_OAUTH_REDIRECT = REDIRECT_URI.replace("/callback", "/admin/callback")

# ══════════════════════════════════════════════════
#  헬퍼
# ══════════════════════════════════════════════════

def get_session(request: Request) -> dict | None:
    token = request.cookies.get("session")
    return sessions.get(token)


def require_session(request: Request) -> dict:
    s = get_session(request)
    if not s:
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
    return s


def _html(name: str, request: Request, ctx: dict = None):
    ctx = ctx or {}
    s   = get_session(request)
    ctx.update({"request": request, "user": s or {}, "client_id": CLIENT_ID,
                "redirect_uri": ADMIN_OAUTH_REDIRECT})
    return templates.TemplateResponse(name, ctx)


# ══════════════════════════════════════════════════
#  공개 라우트
# ══════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(
        "<html><body style='background:#1a1c1f;color:#fff;"
        "font-family:sans-serif;display:flex;justify-content:center;"
        "align-items:center;height:100vh;margin:0'>"
        "<div style='text-align:center'>"
        "<div style='font-size:64px;margin-bottom:16px'>🛡️</div>"
        "<h2 style='font-size:24px'>RecoveryBot 서버 정상 작동 중</h2>"
        "<p style='color:#8e9297;margin-top:8px'>웹서버가 실행 중입니다.</p>"
        "<a href='/admin' style='display:inline-block;margin-top:24px;"
        "background:#5865F2;color:#fff;padding:12px 28px;border-radius:8px;"
        "text-decoration:none;font-weight:700'>📊 관리 패널 열기</a>"
        "</div></body></html>"
    )


# ── 인증 콜백 (디스코드 봇 인증 버튼용) ─────────────

@app.get("/callback")
async def oauth_callback(request: Request):
    """신규 멤버 인증 버튼 → access_token 저장 → 역할 부여"""
    code     = request.query_params.get("code")
    guild_id = request.query_params.get("state")

    if not code:
        return HTMLResponse(_error_page("인증 코드가 없습니다."), status_code=400)

    async with httpx.AsyncClient() as client:
        # 1. code → token
        tr = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if tr.status_code != 200:
            log.error("토큰 교환 실패: %s", tr.text)
            return HTMLResponse(_error_page("토큰 교환 실패"), status_code=400)

        td            = tr.json()
        access_token  = td.get("access_token")
        refresh_token = td.get("refresh_token", "")
        expires_in    = td.get("expires_in", 604800)

        # 2. 유저 정보
        ur = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ur.status_code != 200:
            return HTMLResponse(_error_page("유저 정보 조회 실패"), status_code=400)

        ud       = ur.json()
        user_id  = ud["id"]
        username = ud["username"]

        # 3. DB 저장
        db.save_token(user_id, username, access_token, refresh_token, expires_in, guild_id or "")

        # 4. 서버 참가
        if guild_id and BOT_TOKEN:
            jr = await client.put(
                f"https://discord.com/api/guilds/{guild_id}/members/{user_id}",
                headers={"Authorization": f"Bot {BOT_TOKEN}"},
                json={"access_token": access_token},
            )
            log.info("서버 참가: status=%s user=%s", jr.status_code, username)

    return HTMLResponse(_success_page(username))


# ══════════════════════════════════════════════════
#  관리 패널 라우트
# ══════════════════════════════════════════════════

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    if get_session(request):
        return RedirectResponse("/admin")
    return _html("login.html", request)


@app.get("/admin/callback")
async def admin_callback(request: Request):
    """관리자 OAuth2 콜백"""
    code  = request.query_params.get("code")
    error = request.query_params.get("error")

    if error or not code:
        return RedirectResponse("/admin/login?error=1")

    async with httpx.AsyncClient() as client:
        # code → token
        tr = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  ADMIN_OAUTH_REDIRECT,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if tr.status_code != 200:
            return RedirectResponse("/admin/login?error=1")

        td           = tr.json()
        access_token = td.get("access_token")

        # 유저 정보
        ur = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ur.status_code != 200:
            return RedirectResponse("/admin/login?error=1")
        ud       = ur.json()
        user_id  = ud["id"]
        username = ud["username"]

        # 서버 목록 (관리자 권한 있는 서버만)
        gr = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        guilds = []
        if gr.status_code == 200:
            all_guilds = gr.json()
            for g in all_guilds:
                perms = int(g.get("permissions", 0))
                if perms & 0x8:   # ADMINISTRATOR flag
                    guilds.append({"id": g["id"], "name": g["name"]})

    # 세션 저장
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "user_id":  user_id,
        "username": username,
        "guilds":   guilds,
    }

    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie("session", token, max_age=86400, httponly=True)
    return resp


@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("session")
    if token:
        sessions.pop(token, None)
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("session")
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("dashboard.html", request)


@app.get("/admin/backup", response_class=HTMLResponse)
async def admin_backup(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("backup.html", request)


@app.get("/admin/members", response_class=HTMLResponse)
async def admin_members(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("members.html", request)


@app.get("/admin/shop", response_class=HTMLResponse)
async def admin_shop(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("shop.html", request)


@app.get("/admin/invite", response_class=HTMLResponse)
async def admin_invite(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("invite.html", request)


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/admin/login")
    return _html("settings.html", request)


# ══════════════════════════════════════════════════
#  REST API
# ══════════════════════════════════════════════════

# ── 대시보드 ──────────────────────────────────────

@app.get("/api/dashboard")
async def api_dashboard(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    backups  = db.list_backups(guild_id)
    products = db.get_products(guild_id)
    tokens   = db.get_token_count(guild_id)
    revenue  = db.get_total_revenue(guild_id)
    latest   = backups[0] if backups else None
    return {
        "backup_count":  len(backups),
        "product_count": len(products),
        "token_count":   tokens,
        "revenue":       revenue,
        "latest_backup": latest,
    }


# ── 백업 ──────────────────────────────────────────

@app.get("/api/backups")
async def api_list_backups(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    return db.list_backups(guild_id)


@app.delete("/api/backups/{backup_id}")
async def api_delete_backup(backup_id: int, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    db.delete_backup(backup_id)
    return {"ok": True}


@app.delete("/api/backups/all")
async def api_delete_all_backups(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    backups = db.list_backups(guild_id)
    for b in backups:
        db.delete_backup(b["id"])
    return {"deleted": len(backups)}


# ── 상품 ──────────────────────────────────────────

@app.get("/api/products")
async def api_get_products(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    return db.get_products(guild_id)


@app.post("/api/products")
async def api_add_product(request: Request):
    if not get_session(request):
        raise HTTPException(401)
    body = await request.json()
    pid  = db.add_product(body["guild_id"], body["name"], int(body["price"]), body["role_id"])
    return {"id": pid}


@app.put("/api/products/{product_id}")
async def api_update_product(product_id: int, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    body = await request.json()
    db.update_product(product_id, body["name"], int(body["price"]), body["role_id"])
    return {"ok": True}


@app.delete("/api/products/{product_id}")
async def api_delete_product(product_id: int, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    db.delete_product(product_id)
    return {"ok": True}


# ── 충전 신청 ──────────────────────────────────────

@app.get("/api/charges")
async def api_get_charges(guild_id: str, request: Request, status: str = "pending"):
    if not get_session(request):
        raise HTTPException(401)
    if status == "all":
        return db.get_all_charge_requests(guild_id)
    return db.get_charge_requests(guild_id, status)


@app.post("/api/charges/{charge_id}/approve")
async def api_approve_charge(charge_id: int, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    body   = await request.json()
    amount = int(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, detail="금액을 입력하세요")
    result = db.approve_charge(charge_id, amount)
    if not result:
        raise HTTPException(404, detail="충전 신청을 찾을 수 없습니다")
    return {"ok": True, "user_id": result["user_id"], "amount": amount}


@app.post("/api/charges/{charge_id}/reject")
async def api_reject_charge(charge_id: int, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    db.reject_charge(charge_id)
    return {"ok": True}


# ── 구매 내역 ──────────────────────────────────────

@app.get("/api/purchases")
async def api_get_purchases(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    return db.get_purchases(guild_id)


# ── 잔액 ──────────────────────────────────────────

@app.get("/api/balances")
async def api_get_balances(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    return db.get_all_balances(guild_id)


@app.post("/api/balances/give")
async def api_give_balance(request: Request):
    if not get_session(request):
        raise HTTPException(401)
    body = await request.json()
    db.update_balance(body["user_id"], body["guild_id"], int(body["amount"]))
    new_bal = db.get_balance(body["user_id"], body["guild_id"])
    return {"ok": True, "balance": new_bal}


# ── 멤버 토큰 ──────────────────────────────────────

@app.get("/api/tokens")
async def api_get_tokens(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    return db.get_all_tokens(guild_id)


# ── 멤버 재참가 ────────────────────────────────────

@app.post("/api/rejoin")
async def api_rejoin(request: Request):
    if not get_session(request):
        raise HTTPException(401)
    body     = await request.json()
    guild_id = body.get("guild_id")
    tokens   = db.get_all_tokens(guild_id)

    success = 0
    fail    = 0

    async with httpx.AsyncClient() as client:
        for t in tokens:
            try:
                r = await client.put(
                    f"https://discord.com/api/guilds/{guild_id}/members/{t['user_id']}",
                    headers={"Authorization": f"Bot {BOT_TOKEN}"},
                    json={"access_token": t["access_token"]},
                )
                if r.status_code in (200, 201, 204):
                    success += 1
                else:
                    fail += 1
                    log.warning("재참가 실패: user=%s status=%s", t["user_id"], r.status_code)
            except Exception as e:
                fail += 1
                log.error("재참가 오류: %s", e)

    return {"success": success, "fail": fail, "total": len(tokens)}


# ── 초대 로그 ──────────────────────────────────────

@app.get("/api/invites")
async def api_get_invites(guild_id: str, request: Request):
    if not get_session(request):
        raise HTTPException(401)
    top  = db.get_invite_top(guild_id, limit=20)
    logs = db.get_invite_logs(guild_id)
    return {"top": top, "logs": logs}


# ══════════════════════════════════════════════════
#  HTML 페이지 (인증 성공/실패)
# ══════════════════════════════════════════════════

def _success_page(username: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>인증 완료</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#23272A;display:flex;justify-content:center;align-items:center;min-height:100vh;font-family:'Segoe UI',sans-serif}}
.card{{background:#2C2F33;border-radius:16px;padding:48px 40px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.4);max-width:400px;width:90%}}
.icon{{font-size:64px;margin-bottom:16px}}
h1{{color:#fff;font-size:24px;margin-bottom:8px}}
p{{color:#B9BBBE;font-size:15px;line-height:1.6}}
.name{{color:#7289DA;font-weight:bold}}
.badge{{display:inline-block;background:#43B581;color:#fff;padding:6px 16px;border-radius:20px;font-size:13px;margin-top:20px}}
.close{{color:#72767D;font-size:13px;margin-top:16px}}
</style></head>
<body><div class="card">
<div class="icon">✅</div>
<h1>인증 완료!</h1>
<p><span class="name">{username}</span> 님,<br>서버 인증이 완료되었습니다!</p>
<div class="badge">✓ 멤버 역할 부여됨</div>
<p class="close">이 창을 닫고 디스코드로 돌아가세요</p>
</div></body></html>"""


def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>인증 오류</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#23272A;display:flex;justify-content:center;align-items:center;min-height:100vh;font-family:'Segoe UI',sans-serif}}
.card{{background:#2C2F33;border-radius:16px;padding:48px 40px;text-align:center;max-width:400px;width:90%}}
.icon{{font-size:64px;margin-bottom:16px}}
h1{{color:#fff;font-size:24px;margin-bottom:8px}}
p{{color:#B9BBBE;font-size:15px}}
.err{{color:#F04747;font-size:13px;margin-top:12px}}
</style></head>
<body><div class="card">
<div class="icon">❌</div>
<h1>인증 실패</h1>
<p>인증 중 오류가 발생했습니다.<br>디스코드로 돌아가서 다시 시도해주세요.</p>
<p class="err">{msg}</p>
</div></body></html>"""
