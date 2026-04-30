# 🛡️ RecoveryBot — 디스코드 서버 자동 복구봇

서버가 터져도 **DB에 저장된 백업 하나로** 채널·역할·멤버역할을 자동으로 복구합니다.

---

## 📁 프로젝트 구조

```
discord-recovery-bot/
├── bot.py                      # 메인 진입점
├── config.py                   # 설정 (토큰, 경로 등)
├── database.py                 # SQLite DB 저장/불러오기
├── recovery_engine.py          # 실제 복구 실행 엔진
├── cogs/
│   ├── backup_cog.py           # /backup 커맨드
│   ├── restore_cog.py          # /restore 커맨드
│   ├── auto_recovery_cog.py    # 자동 감지 & 복구
│   └── auto_backup_cog.py      # 주기적 자동 백업
├── data/
│   └── backup.db               # SQLite DB (자동 생성)
├── logs/
│   └── bot.log                 # 로그 파일 (자동 생성)
├── .env.example                # 환경변수 샘플
├── requirements.txt
└── README.md
```

---

## ⚡ 빠른 시작

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정
```bash
cp .env.example .env
# .env 파일을 열어 BOT_TOKEN 입력
```

### 3. 봇 실행
```bash
python bot.py
```

---

## 🤖 디스코드 봇 만들기

1. [Discord Developer Portal](https://discord.com/developers/applications) 접속
2. **New Application** → 이름 입력
3. 왼쪽 **Bot** 탭 → **Add Bot**
4. **TOKEN** 복사 → `.env` 의 `BOT_TOKEN` 에 붙여넣기
5. **Privileged Gateway Intents** 에서 아래 3가지 **모두 활성화**:
   - ✅ `SERVER MEMBERS INTENT`
   - ✅ `MESSAGE CONTENT INTENT`
   - ✅ `PRESENCE INTENT`
6. **OAuth2 → URL Generator** 에서 권한 설정:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Administrator` (복구 시 채널/역할 생성 권한 필요)
7. 생성된 URL로 봇을 서버에 초대

---

## 📋 슬래시 커맨드 목록

### 💾 백업 (`/backup`)

| 커맨드 | 설명 |
|--------|------|
| `/backup save [label]` | 현재 서버 전체 백업 |
| `/backup list` | 백업 목록 확인 |
| `/backup info <id>` | 백업 상세 정보 |
| `/backup delete <id>` | 백업 삭제 |

### 🔁 복구 (`/restore`)

| 커맨드 | 설명 |
|--------|------|
| `/restore latest` | 최신 백업으로 복구 |
| `/restore id <id>` | 특정 백업 ID로 복구 |
| `/restore preview <id>` | 복구 미리보기 (실제 적용 없음) |

### 🛡️ 자동 복구 (`/autorecovery`)

| 커맨드 | 설명 |
|--------|------|
| `/autorecovery enable` | 자동 복구 활성화 + 임계값 설정 |
| `/autorecovery disable` | 자동 복구 비활성화 |
| `/autorecovery status` | 현재 상태 확인 |

### ⏰ 자동 백업 (`/autobackup`)

| 커맨드 | 설명 |
|--------|------|
| `/autobackup enable` | 주기 자동 백업 활성화 |
| `/autobackup disable` | 자동 백업 비활성화 |
| `/autobackup status` | 현재 상태 확인 |
| `/autobackup now [label]` | 즉시 백업 실행 |

---

## 🚨 자동 복구 동작 방식

```
서버 공격 감지 (채널/역할 대량 삭제 or 대량 밴)
        ↓
임계값 초과 여부 확인
        ↓
DB에서 최신 백업 자동 로드 (load_latest_backup)
        ↓
RecoveryEngine 실행
  ├── 역할 생성/업데이트
  ├── 카테고리 생성
  ├── 채널 생성
  └── 멤버 역할 복구
        ↓
로그 채널에 결과 전송
```

**기본 감지 임계값** (10초 안에):
- 채널 3개 이상 삭제
- 역할 3개 이상 삭제
- 5명 이상 밴

→ `/autorecovery enable` 옵션으로 임계값 자유롭게 조정 가능

---

## 💡 추천 운영 방법

```
1. 봇 초대 & 실행
2. /backup save 초기백업        ← 첫 백업
3. /autobackup enable            ← 6시간마다 자동 백업
4. /autorecovery enable          ← 공격 감지 자동 복구 ON
5. LOG_CHANNEL_ID 설정           ← 복구 로그 채널 지정
```

---

## 🔧 환경변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `BOT_TOKEN` | ✅ | — | 디스코드 봇 토큰 |
| `PREFIX` | ❌ | `!` | 텍스트 커맨드 접두사 |
| `LOG_CHANNEL_ID` | ❌ | `0` | 로그 전송 채널 ID |
| `AUTO_BACKUP_INTERVAL` | ❌ | `21600` | 자동 백업 주기(초) |
| `ADMIN_ROLE_NAME` | ❌ | `` | 커맨드 사용 가능 역할명 |

---

## ⚠️ 주의사항

- 봇은 반드시 **관리자(Administrator)** 권한을 가져야 합니다
- 멤버 수가 많을수록 복구 시간이 길어집니다 (Rate Limit 때문)
- 백업은 DB 파일(`data/backup.db`)에 저장되므로 **DB 파일을 주기적으로 별도 백업** 권장
- 봇이 재시작되면 `자동복구/자동백업` 활성화 상태가 초기화됩니다  
  → 재시작 후 `/autorecovery enable` 과 `/autobackup enable` 을 다시 입력해주세요
