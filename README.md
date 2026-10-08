# 모집병 일정판

병무청 누리집의 육·해·공군·해병대 모집계획/공지 게시판을 12시간마다 수집해
접수 상태(예정/접수 중/대기/종료), 남은 시간, 주요 일정을 보여주는 정적 사이트.

```
scraper/scrape.py      수집기 (requests + BeautifulSoup, html.parser)
docs/index.html        사이트 (GitHub Pages가 docs/ 를 서빙)
docs/data.json         수집 결과 (업로드한 샘플로 만든 초기값 포함)
.github/workflows/     12시간 주기 자동 실행 (00:15, 12:15 KST)
a32/run.sh             GitHub Actions가 막힐 때 A32(Termux)에서 돌리는 스크립트
```

## 1. GitHub로 운영

1. 새 저장소를 만들고 이 폴더 내용을 push
2. Settings → Pages → Source: Deploy from a branch → `main` / `/docs`
3. Settings → Actions → General → Workflow permissions: Read and write
4. Actions 탭 → `update-data` → Run workflow 로 한 번 수동 실행
   - 성공하면 `docs/data.json` 커밋이 생기고 사이트에 반영됨
   - `모든 게시판 목록 수집 실패`로 실패하면 해외 IP 차단 가능성이 큼 → 2번으로

## 2. A32(Termux)로 수집만 대신하기

사이트는 계속 GitHub Pages가 서빙하고, A32는 하루 두 번 수집 후 push만 함.

```sh
# Termux (F-Droid 버전 권장)
pkg update && pkg install python git cronie termux-services termux-api
pip install requests beautifulsoup4

git clone https://github.com/<아이디>/<저장소>.git ~/mma-schedule
cd ~/mma-schedule
# push 권한: Fine-grained PAT (해당 저장소 Contents: Read and write)
git remote set-url origin https://<아이디>:<PAT>@github.com/<아이디>/<저장소>.git
git config user.name "a32-bot"; git config user.email "a32-bot@users.noreply.github.com"

bash a32/run.sh          # 수동 1회 테스트

# 12시간 주기 등록 (Termux 재시작 후 sv-enable 적용)
sv-enable crond
(crontab -l 2>/dev/null; echo '15 0,12 * * * bash $HOME/mma-schedule/a32/run.sh >> $HOME/mma.log 2>&1') | crontab -
```

- 안드로이드 설정 → 앱 → Termux → 배터리 → 제한 없음 (안 하면 cron이 멈춤)
- A32로 전환하면 `.github/workflows/update.yml` 의 `schedule:` 두 줄을 지워 충돌 방지

## 동작 방식

- 목록 9개(각 1페이지)를 매번 읽고, 제목에 '모집'이 있는 **새 글만** 상세를 가져옴
- 접수 마감 전인 글은 정정 공고 대비로 다시 확인, 나머지는 캐시 사용
- 같은 글이 모집계획/공지사항 두 게시판에 있으면 모집계획 쪽 하나만 남김
- 본문에서 `접수기간 :`, `1차 선발…발표 :`, `모집신검 :`, `실기평가 :`, `최종 선발…발표 :`, `입영일자/일시 :` 라벨 뒤 날짜를 파싱
- 접수기간이 파싱되지 않는 글(안내·변경 공지 등)은 모집 목록에서 빠지고 공지 목록에만 표시
- 요청 간 1.5초 간격, 평상시 1회 실행당 약 10~20 요청

게시판 구조가 바뀌면 `scrape.py` 의 `BOARDS`(gesipan_id, mc)와 `LABELS` 정규식을 수정.
