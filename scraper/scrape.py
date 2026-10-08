#!/usr/bin/env python3
"""병무청 모집병 공지 수집기.

12시간마다 실행되어 docs/data.json 을 갱신한다.
- 게시판 목록 9개(각 1페이지)를 읽는다.
- 제목에 '모집'이 있는 새 게시글만 상세를 가져와 일정을 파싱한다.
- 이미 가져온 글은 캐시하고, 접수 마감 전인 글만 다시 확인한다.
"""
import argparse
import calendar
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

KST = ZoneInfo("Asia/Seoul")
BASE = "https://www.mma.go.kr"
UA = "mma-schedule-board/1.0 (personal, non-commercial, 12h interval)"
DELAY = 1.5                 # 요청 간 간격(초)
DETAIL_MAX_AGE_DAYS = 90    # 이보다 오래된 게시글은 상세를 가져오지 않음
KEEP_DAYS_AFTER = 45        # 마지막 일정 이후 이 기간이 지나면 제거
REFRESH_HOURS = 6           # 접수 마감 전인 글의 재확인 간격
NOTICES_PER_BOARD = 15

# (branch, kind, gesipan_id, mc)
BOARDS = [
    ("army", "plan", 93, "usr0000150"),
    ("navy", "plan", 94, "usr0000151"),
    ("airforce", "plan", 95, "usr0000152"),
    ("marine", "plan", 92, "usr0000385"),
    ("army", "notice", 69, "usr0000127"),
    ("navy", "notice", 70, "usr0000128"),
    ("airforce", "notice", 71, "usr0000129"),
    ("marine", "notice", 72, "usr0000130"),
    (None, "score", 91, "mma0003717"),
]

LABELS = [
    ("apply", r"접수\s*기간\s*[:：]"),
    ("first", r"1차\s*선발[^:：■○]{0,15}?발표\s*[:：]"),
    ("exam", r"모집\s*신검[^:：■○]{0,8}?[:：]"),
    ("practical", r"실기\s*평가[^:：■○]{0,8}?[:：]"),
    ("final", r"최종\s*선발[^:：■○]{0,15}?발표\s*[:：]"),
    ("enlist", r"입영\s*(?:일자|일시)[^:：■○]{0,10}?[:：]"),
]
STOP = re.compile(r"[■○△※*/]| - ")
DOW = r"(?:\(\s*[월화수목금토일]\s*\))?"
FULL = re.compile(
    r"(?:(\d{4})|['’‘`](\d{2}))\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*" + DOW +
    r"\s*(?:(\d{1,2})\s*:\s*(\d{2}))?")
PART = re.compile(
    r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*" + DOW + r"\s*(?:(\d{1,2})\s*:\s*(\d{2}))?")
MONTH = re.compile(r"(?:(\d{4})|['’‘`](\d{2}))\s*년\s*(\d{1,2})\s*월")
RANGE_SEP = re.compile(r"\s*[~∼～]\s*")


def clean(s):
    return " ".join((s or "").split())


def now_kst():
    return datetime.now(KST)


def iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def parse_iso(s):
    return datetime.fromisoformat(s) if s else None


# ---------------------------------------------------------------- 날짜 파싱

def _mk(y, mo, d, hh, mm, end=False):
    if hh is not None:
        return datetime(y, int(mo), int(d), int(hh), int(mm), tzinfo=KST), False
    if end:
        return datetime(y, int(mo), int(d), 23, 59, 59, tzinfo=KST), True
    return datetime(y, int(mo), int(d), 0, 0, tzinfo=KST), True


def parse_range(seg):
    m = FULL.search(seg)
    if not m:
        return None
    y = int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))
    start, s_dateonly = _mk(y, m.group(3), m.group(4), m.group(5), m.group(6))
    end, e_dateonly = None, False
    rest = seg[m.end():]
    sep = RANGE_SEP.match(rest)
    if sep:
        rest = rest[sep.end():]
        m2 = FULL.match(rest)
        if m2:
            y2 = int(m2.group(1)) if m2.group(1) else 2000 + int(m2.group(2))
            end, e_dateonly = _mk(y2, m2.group(3), m2.group(4), m2.group(5), m2.group(6), end=True)
        else:
            m3 = PART.match(rest)
            if m3:
                end, e_dateonly = _mk(y, m3.group(1), m3.group(2), m3.group(3), m3.group(4), end=True)
                if end < start:
                    end = end.replace(year=y + 1)
    ev = {"start": iso(start), "end": iso(end), "dateOnly": s_dateonly and (end is None or e_dateonly)}
    return ev


def extract_events(text):
    events = {}
    for key, pat in LABELS:
        for m in re.finditer(pat, text):
            seg = text[m.end(): m.end() + 140]
            cut = STOP.search(seg)
            if cut:
                seg = seg[: cut.start()]
            ev = parse_range(seg)
            if ev:
                events[key] = ev
                break
            if key == "enlist":
                mm = MONTH.search(seg)
                if mm:
                    y = int(mm.group(1)) if mm.group(1) else 2000 + int(mm.group(2))
                    events[key] = {"month": f"{y:04d}-{int(mm.group(3)):02d}"}
                    break
    return events


def last_event_time(events):
    times = []
    for ev in events.values():
        if "month" in ev:
            y, mo = map(int, ev["month"].split("-"))
            times.append(datetime(y, mo, calendar.monthrange(y, mo)[1], 23, 59, tzinfo=KST))
        else:
            times += [parse_iso(ev.get("start")), parse_iso(ev.get("end"))]
    times = [t for t in times if t]
    return max(times) if times else None


# ---------------------------------------------------------------- HTML 파싱

def parse_list(html, gesipan_id, mc):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.board_notice")
    if table is None:
        raise ValueError("게시판 표(table.board_notice)를 찾지 못함")
    out = {}
    for tr in table.find_all("tr"):
        a = tr.find("a", href=re.compile(r"boardView\.do"))
        if not a:
            continue
        no = parse_qs(urlparse(a["href"]).query).get("gsgeul_no", [None])[0]
        if not no or no in out:
            continue
        posted = None
        for td in tr.find_all("td"):
            t = td.get_text(strip=True)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
                posted = t
        th = tr.find("th")
        title = clean(a.get_text(" ", strip=True))
        title = re.sub(r"\s*\.\.\.$", "…", title)
        out[no] = {
            "no": no,
            "title": title,
            "posted": posted,
            "pinned": bool(th and "공지" in th.get_text()),
            "url": f"{BASE}/board/boardView.do?gesipan_id={gesipan_id}&gsgeul_no={no}&mc={mc}",
        }
    return list(out.values())


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    view = soup.select_one("table.notice_view")
    if view is None:
        raise ValueError("본문 표(table.notice_view)를 찾지 못함")
    title_td = view.find("td")
    body = view.select_one("td.con_text")
    meta = clean(view.get_text(" ", strip=True))
    mod = re.search(r"최종\s*수정일\s*:\s*(\d{4}-\d{2}-\d{2})", meta)
    atts = [
        {"name": clean(a.get_text(" ", strip=True)), "url": urljoin(BASE, a["href"])}
        for a in view.select('a[href*="boardFileDown"]')
    ]
    return {
        "title": clean(title_td.get_text(" ", strip=True)) if title_td else "",
        "text": clean(body.get_text(" ", strip=True)) if body else "",
        "modified": mod.group(1) if mod else None,
        "attachments": atts,
    }


# ---------------------------------------------------------------- 수집

class Fetcher:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
        self.last = 0.0

    def get(self, url):
        wait = DELAY - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        err = None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=25)
                self.last = time.time()
                r.raise_for_status()
                r.encoding = "utf-8"
                return r.text
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(3 * (attempt + 1))
        raise err


def norm_title(t):
    return re.sub(r"[\s'’‘「」『』\"·.…]", "", t)


def needs_refresh(rec, now):
    fetched = parse_iso(rec.get("fetched_at"))
    if fetched and now - fetched < timedelta(hours=REFRESH_HOURS):
        return False
    apply_end = parse_iso((rec.get("events", {}).get("apply") or {}).get("end"))
    return bool(apply_end and apply_end > now)


def is_recent(posted, now):
    if not posted:
        return True
    d = datetime.strptime(posted, "%Y-%m-%d").replace(tzinfo=KST)
    return now - d <= timedelta(days=DETAIL_MAX_AGE_DAYS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/data.json")
    args = ap.parse_args()
    out = Path(args.out)
    now = now_kst()

    old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    cache = {r["id"]: r for r in old.get("recruits", [])}
    seen_empty = dict(old.get("_seen_empty", {}))

    f = Fetcher()
    errors, notices, candidates = [], [], []
    lists_ok = 0

    for branch, kind, gid, mc in BOARDS:
        url = f"{BASE}/board/boardList.do?gesipan_id={gid}&mc={mc}"
        try:
            items = parse_list(f.get(url), gid, mc)
            lists_ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"목록 {gid}: {e}")
            continue
        if kind in ("notice", "score"):
            for it in items[:NOTICES_PER_BOARD]:
                notices.append({**it, "branch": branch, "board": kind, "listUrl": url})
        if kind != "score":
            for it in items:
                if "모집" in it["title"] and is_recent(it["posted"], now):
                    candidates.append((branch, kind, gid, it))

    if lists_ok == 0:
        print("모든 게시판 목록 수집 실패:\n" + "\n".join(errors), file=sys.stderr)
        sys.exit(1)

    recruits = {}
    for branch, kind, gid, it in candidates:
        rid = f"{gid}-{it['no']}"
        prev = cache.get(rid)
        if prev and not needs_refresh(prev, now):
            recruits[rid] = prev
            continue
        if not prev and rid in seen_empty:
            continue
        try:
            d = parse_detail(f.get(it["url"]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"상세 {rid}: {e}")
            if prev:
                recruits[rid] = prev
            continue
        events = extract_events(d["text"])
        if "apply" not in events:
            seen_empty[rid] = it["posted"]
            continue
        head = d["title"] + " " + d["text"][:300]
        rnd = re.search(r"(\d{2})\s*-\s*(\d{1,3})\s*회차", head)
        recruits[rid] = {
            "id": rid,
            "branch": branch,
            "source": kind,
            "title": d["title"] or it["title"],
            "url": it["url"],
            "posted": it["posted"],
            "modified": d["modified"],
            "round": f"{rnd.group(1)}-{rnd.group(2)}" if rnd else None,
            "extra": "추가모집" in head,
            "events": events,
            "attachments": d["attachments"],
            "fetched_at": iso(now),
        }

    # 목록 1페이지에서 밀려났지만 아직 유효한 일정은 유지
    for rid, r in cache.items():
        if rid not in recruits:
            last = last_event_time(r.get("events", {}))
            if last and last > now - timedelta(days=KEEP_DAYS_AFTER):
                recruits[rid] = r

    # 모집계획 게시판과 공지사항 게시판의 중복 글 제거 (모집계획 우선)
    best = {}
    for r in sorted(recruits.values(), key=lambda r: 0 if r["source"] == "plan" else 1):
        key = (r["branch"], norm_title(r["title"]))
        if key not in best:
            best[key] = r
    final = []
    for r in best.values():
        last = last_event_time(r["events"])
        if last is None or last > now - timedelta(days=KEEP_DAYS_AFTER):
            final.append(r)
    final.sort(key=lambda r: r["events"]["apply"]["start"], reverse=True)

    live_ids = {f"{gid}-{it['no']}" for _, _, gid, it in candidates}
    seen_empty = {k: v for k, v in seen_empty.items() if k in live_ids}

    data = {
        "updated_at": iso(now),
        "source": BASE,
        "errors": errors,
        "recruits": final,
        "notices": notices,
        "_seen_empty": seen_empty,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"모집 {len(final)}건, 공지 {len(notices)}건, 오류 {len(errors)}건")


if __name__ == "__main__":
    main()
