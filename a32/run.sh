#!/data/data/com.termux/files/usr/bin/bash
# A32(Termux)용: 수집 후 GitHub에 push. cron에서 12시간마다 호출.
set -u
REPO="$HOME/mma-schedule"
cd "$REPO" || exit 1
termux-wake-lock 2>/dev/null
git pull --rebase -q || { echo "$(date) git pull 실패"; exit 1; }
if python scraper/scrape.py --out docs/data.json; then
  git add docs/data.json
  git diff --cached --quiet || git commit -qm "data(a32): $(date '+%Y-%m-%d %H:%M')"
  git push -q || echo "$(date) git push 실패"
else
  echo "$(date) 수집 실패"
fi
termux-wake-unlock 2>/dev/null
