#!/usr/bin/env python3

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from html.parser import HTMLParser


# ============================================================
# 기본 설정
# ============================================================

ROOT = Path(__file__).resolve().parent

CONFIG_FILE = ROOT / "future-sources.json"
OUTPUT_FILE = ROOT / "future-data.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)

TIMEOUT = 30


# ============================================================
# 수집할 사이트
# ============================================================

SOURCES = {
    "mollulog": {
        "name": "몰루로그",
        "url": "https://mollulog.net/futures",
        "kind": "future"
    },

    "bluearchive_gallery": {
        "name": "블루 아카이브 갤러리",
        "url": "https://gall.dcinside.com/mgallery/board/lists/?id=projectmx",
        "kind": "community"
    },

    "nexon_forum": {
        "name": "블루 아카이브 공식 포럼",
        "url": "https://forum.nexon.com/bluearchive/",
        "kind": "official"
    }
}


# ============================================================
# HTML 파서
# ============================================================

class SimpleParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.texts = []
        self.links = []

        self.current_link = None

    def handle_starttag(self, tag, attrs):

        attrs = dict(attrs)

        if tag == "a" and attrs.get("href"):

            self.current_link = {
                "url": attrs["href"],
                "text": []
            }

            self.links.append(self.current_link)

    def handle_endtag(self, tag):

        if tag == "a":
            self.current_link = None

    def handle_data(self, data):

        text = " ".join(data.split())

        if not text:
            return

        self.texts.append(text)

        if self.current_link is not None:
            self.current_link["text"].append(text)


# ============================================================
# 웹페이지 가져오기
# ============================================================

def fetch(url):

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"
        }
    )

    with urlopen(request, timeout=TIMEOUT) as response:

        encoding = response.headers.get_content_charset()

        if not encoding:
            encoding = "utf-8"

        return response.read().decode(
            encoding,
            errors="replace"
        )


# ============================================================
# 날짜 추출
# ============================================================

DATE_PATTERN = re.compile(
    r"(20\d{2})[.\-/년\s]+"
    r"(\d{1,2})[.\-/월\s]+"
    r"(\d{1,2})"
)


def extract_dates(text):

    results = []

    for match in DATE_PATTERN.finditer(text):

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if 1 <= month <= 12 and 1 <= day <= 31:

            results.append(
                f"{year:04d}-{month:02d}-{day:02d}"
            )

    return sorted(set(results))


# ============================================================
# 몰루로그
# ============================================================

def collect_mollulog():

    source = SOURCES["mollulog"]

    html = fetch(source["url"])

    parser = SimpleParser()

    parser.feed(html)

    events = []

    current_date = None
    current_text = []

    for text in parser.texts:

        dates = extract_dates(text)

        if dates:

            if current_date and current_text:

                title = " ".join(current_text).strip()

                if title:

                    events.append({
                        "date": current_date,
                        "title": title,
                        "url": source["url"],
                        "source": source["name"]
                    })

            current_date = dates[0]
            current_text = []

            continue

        if current_date:

            current_text.append(text)

    if current_date and current_text:

        title = " ".join(current_text).strip()

        if title:

            events.append({
                "date": current_date,
                "title": title,
                "url": source["url"],
                "source": source["name"]
            })

    return clean_events(events)


# ============================================================
# 블루 아카이브 갤러리
# ============================================================

def collect_gallery():

    source = SOURCES["bluearchive_gallery"]

    html = fetch(source["url"])

    parser = SimpleParser()

    parser.feed(html)

    keywords = [
        "미래시",
        "일섭",
        "일섭정보",
        "한섭",
        "픽업",
        "모집",
        "이벤트",
        "총력전",
        "대결전",
        "종합전술시험",
        "제약해제결전",
        "업데이트",
        "일정"
    ]

    posts = []

    seen = set()

    for link in parser.links:

        title = " ".join(
            link["text"]
        ).strip()

        if not title:
            continue

        if not any(
            keyword in title
            for keyword in keywords
        ):
            continue

        url = urljoin(
            source["url"],
            link["url"]
        )

        # 갤러리 게시글만 대략적으로 통과
        if "board/view" not in url:
            continue

        if url in seen:
            continue

        seen.add(url)

        posts.append({
            "title": title,
            "url": url,
            "source": source["name"],
            "dates": extract_dates(title)
        })

    return posts[:100]


# ============================================================
# 넥슨 공식 포럼
# ============================================================

def collect_nexon_forum():

    source = SOURCES["nexon_forum"]

    # 공식 포럼에서 중요한 게시판들을 직접 확인
    board_urls = [
        "https://forum.nexon.com/bluearchive/board_list?board=1043",
        "https://forum.nexon.com/bluearchive/board_list?board=1076",
        "https://forum.nexon.com/bluearchive/board_list?board=1039"
    ]

    keywords = [
        "업데이트",
        "이벤트",
        "모집",
        "픽업",
        "총력전",
        "대결전",
        "종합전술시험",
        "제약해제결전",
        "점검",
        "로드맵",
        "공지"
    ]

    posts = []

    seen = set()

    for board_url in board_urls:

        try:

            html = fetch(board_url)

            parser = SimpleParser()

            parser.feed(html)

            for link in parser.links:

                title = " ".join(
                    link["text"]
                ).strip()

                if not title:
                    continue

                if not any(
                    keyword in title
                    for keyword in keywords
                ):
                    continue

                url = urljoin(
                    board_url,
                    link["url"]
                )

                if "board_view" not in url:
                    continue

                if url in seen:
                    continue

                seen.add(url)

                posts.append({
                    "title": title,
                    "url": url,
                    "source": source["name"],
                    "dates": extract_dates(title)
                })

        except Exception as error:

            print(
                f"[공식 포럼] 게시판 수집 실패: "
                f"{board_url}"
            )

            print(error)

    return posts[:150]


# ============================================================
# 중복 제거
# ============================================================

def clean_events(events):

    result = []

    seen = set()

    for event in events:

        date = event.get("date")
        title = event.get("title", "").strip()

        if not title:
            continue

        key = (
            date,
            title,
            event.get("source")
        )

        if key in seen:
            continue

        seen.add(key)

        result.append(event)

    result.sort(
        key=lambda x: (
            x.get("date") or "9999-99-99",
            x.get("title", "")
        )
    )

    return result


# ============================================================
# 설정 파일 읽기
# ============================================================

def load_config():

    if not CONFIG_FILE.exists():

        return {
            "sources": [],
            "autoUpdate": {
                "validation": {}
            }
        }

    try:

        return json.loads(
            CONFIG_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {
            "sources": [],
            "autoUpdate": {
                "validation": {}
            }
        }


# ============================================================
# 기존 데이터 읽기
# ============================================================

def load_old_data():

    if not OUTPUT_FILE.exists():
        return {}

    try:

        return json.loads(
            OUTPUT_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {}


# ============================================================
# 저장
# ============================================================

def save_data(data):

    OUTPUT_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )


# ============================================================
# 메인
# ============================================================

def main():

    print("==========================================")
    print(" 블루 아카이브 미래시 자동 수집")
    print("==========================================")

    now = (
        datetime.now()
        .astimezone()
        .isoformat(
            timespec="seconds"
        )
    )

    config = load_config()
    old_data = load_old_data()

    all_events = []

    evidence = []

    errors = []

    # ========================================================
    # 1. 몰루로그
    # ========================================================

    print()
    print("[1/3] 몰루로그 수집 중...")

    try:

        mollulog_events = collect_mollulog()

        print(
            f"몰루로그: "
            f"{len(mollulog_events)}개 수집"
        )

        all_events.extend(
            mollulog_events
        )

        evidence.append({
            "sourceId": "mollulog",
            "name": "몰루로그",
            "kind": "future",
            "url": SOURCES["mollulog"]["url"],
            "checkedAt": now,
            "items": mollulog_events
        })

    except Exception as error:

        print(
            f"몰루로그 오류: {error}"
        )

        errors.append({
            "sourceId": "mollulog",
            "error": str(error)
        })


    # ========================================================
    # 2. 블루 아카이브 갤러리
    # ========================================================

    print()
    print("[2/3] 블루 아카이브 갤러리 수집 중...")

    try:

        gallery_posts = collect_gallery()

        print(
            f"블루 아카이브 갤러리: "
            f"{len(gallery_posts)}개 수집"
        )

        evidence.append({
            "sourceId": "bluearchive_gallery",
            "name": "블루 아카이브 갤러리",
            "kind": "community",
            "url": SOURCES["bluearchive_gallery"]["url"],
            "checkedAt": now,
            "items": gallery_posts
        })

    except Exception as error:

        print(
            f"갤러리 오류: {error}"
        )

        errors.append({
            "sourceId": "bluearchive_gallery",
            "error": str(error)
        })


    # ========================================================
    # 3. 넥슨 공식 포럼
    # ========================================================

    print()
    print("[3/3] 넥슨 공식 포럼 수집 중...")

    try:

        official_posts = collect_nexon_forum()

        print(
            f"넥슨 공식 포럼: "
            f"{len(official_posts)}개 수집"
        )

        evidence.append({
            "sourceId": "nexon_forum",
            "name": "블루 아카이브 공식 포럼",
            "kind": "official",
            "url": SOURCES["nexon_forum"]["url"],
            "checkedAt": now,
            "items": official_posts
        })

    except Exception as error:

        print(
            f"공식 포럼 오류: {error}"
        )

        errors.append({
            "sourceId": "nexon_forum",
            "error": str(error)
        })


    # ========================================================
    # 미래시 일정 정리
    # ========================================================

    all_events = clean_events(
        all_events
    )


    # ========================================================
    # 최종 데이터
    # ========================================================

    new_data = dict(old_data)

    new_data["updatedAt"] = now

    new_data["server"] = "KR"

    new_data["defaultRangeMonths"] = 12

    new_data["supportedRangeMonths"] = [
        2,
        4,
        6,
        12,
        24,
        36
    ]

    # 모든 출처에서 찾은 일정
    new_data["events"] = all_events

    # 출처별 원본 자료
    new_data["sourceEvidence"] = evidence

    # 오류가 발생한 출처 기록
    new_data["errors"] = errors

    new_data["rules"] = config.get(
        "autoUpdate",
        {}
    ).get(
        "validation",
        {}
    )

    new_data["sources"] = [
        {
            "id": "mollulog",
            "name": "몰루로그",
            "type": "미래시",
            "url": SOURCES["mollulog"]["url"]
        },
        {
            "id": "bluearchive_gallery",
            "name": "블루 아카이브 갤러리",
            "type": "커뮤니티",
            "url": SOURCES["bluearchive_gallery"]["url"]
        },
        {
            "id": "nexon_forum",
            "name": "블루 아카이브 공식 포럼",
            "type": "공식",
            "url": SOURCES["nexon_forum"]["url"]
        }
    ]

    new_data["note"] = (
        "몰루로그, 블루 아카이브 갤러리, "
        "블루 아카이브 공식 포럼에서 자료를 수집합니다. "
        "공식 포럼 자료는 확정 정보, "
        "몰루로그와 커뮤니티 자료는 미래시 및 예상 정보로 "
        "구분하여 사용할 수 있습니다."
    )

    save_data(
        new_data
    )


    # ========================================================
    # 결과 출력
    # ========================================================

    print()
    print("==========================================")
    print(" 업데이트 완료")
    print("==========================================")

    print()
    print(
        f"전체 일정/자료: "
        f"{len(all_events)}개"
    )

    print(
        f"출처: "
        f"{len(evidence)}곳 정상 수집"
    )

    print(
        f"오류: "
        f"{len(errors)}곳"
    )

    print()

    if all_events:

        print("수집된 일정 일부:")

        for event in all_events[:20]:

            print(
                f"- "
                f"{event.get('date', '날짜 없음')} "
                f"| "
                f"{event.get('source', '')} "
                f"| "
                f"{event.get('title', '')[:100]}"
            )

    print()
    print(
        f"저장 완료: {OUTPUT_FILE}"
    )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    main()
