#!/usr/bin/env python3

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed


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

# 사이트 하나가 너무 오래 걸리면 포기
TIMEOUT = 10

# 수집량 제한
MAX_GALLERY_POSTS = 40
MAX_OFFICIAL_POSTS = 60


# ============================================================
# 수집할 사이트
# ============================================================

SOURCES = {
    "mollulog": {
        "name": "몰루로그",
        "url": "https://mollulog.net/futures",
        "kind": "future",
    },

    "bluearchive_gallery": {
        "name": "블루 아카이브 갤러리",
        "url": "https://gall.dcinside.com/mgallery/board/lists/?id=projectmx",
        "kind": "community",
    },

    "nexon_forum": {
        "name": "블루 아카이브 공식 포럼",
        "url": "https://forum.nexon.com/bluearchive/",
        "kind": "official",
    },
}


# ============================================================
# 공식 포럼 게시판
# ============================================================

OFFICIAL_BOARDS = [
    "https://forum.nexon.com/bluearchive/board_list?board=1043",
    "https://forum.nexon.com/bluearchive/board_list?board=1076",
    "https://forum.nexon.com/bluearchive/board_list?board=1039",
]


# ============================================================
# 미래시 관련 키워드
# ============================================================

KEYWORDS = (
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
    "일정",
    "점검",
    "로드맵",
    "공지",
)


# ============================================================
# 날짜 정규식
# ============================================================

DATE_PATTERNS = [

    re.compile(
        r"(20\d{2})\s*[.\-/년]\s*"
        r"(\d{1,2})\s*[.\-/월]\s*"
        r"(\d{1,2})\s*일?"
    ),

    re.compile(
        r"(20\d{2})\s*년\s*"
        r"(\d{1,2})\s*월\s*"
        r"(\d{1,2})\s*일"
    ),
]


# ============================================================
# HTML 파서
# ============================================================

class Parser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.texts = []
        self.links = []

        self.current_link = None


    def handle_starttag(self, tag, attrs):

        if tag != "a":
            return

        attrs = dict(attrs)

        href = attrs.get("href")

        if not href:
            return

        self.current_link = {
            "url": href,
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
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Connection": "close",
        },
    )

    with urlopen(request, timeout=TIMEOUT) as response:

        encoding = (
            response.headers.get_content_charset()
            or "utf-8"
        )

        return response.read().decode(
            encoding,
            errors="replace"
        )


# ============================================================
# 날짜 추출
# ============================================================

def extract_dates(text):

    found = set()

    for pattern in DATE_PATTERNS:

        for match in pattern.finditer(text):

            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))

            if (
                2020 <= year <= 2100
                and 1 <= month <= 12
                and 1 <= day <= 31
            ):

                found.add(
                    f"{year:04d}-{month:02d}-{day:02d}"
                )

    return sorted(found)


# ============================================================
# 중복 제거
# ============================================================

def clean_events(events):

    result = []
    seen = set()

    for event in events:

        title = " ".join(
            str(event.get("title", "")).split()
        ).strip()

        if not title:
            continue

        event["title"] = title

        key = (
            event.get("date", ""),
            title,
            event.get("source", "")
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
# 몰루로그
# ============================================================

def collect_mollulog():

    source = SOURCES["mollulog"]

    html = fetch(source["url"])

    parser = Parser()
    parser.feed(html)

    events = []

    current_date = None
    current_text = []

    for text in parser.texts:

        dates = extract_dates(text)

        if dates:

            if current_date and current_text:

                title = " ".join(
                    current_text
                ).strip()

                if title and len(title) <= 300:

                    events.append({
                        "date": current_date,
                        "title": title,
                        "url": source["url"],
                        "source": source["name"],
                    })

            current_date = dates[0]

            current_text = []

        elif current_date:

            current_text.append(text)


    if current_date and current_text:

        title = " ".join(
            current_text
        ).strip()

        if title and len(title) <= 300:

            events.append({
                "date": current_date,
                "title": title,
                "url": source["url"],
                "source": source["name"],
            })


    return clean_events(events)


# ============================================================
# 게시글 목록 파싱
# ============================================================

def parse_posts(
    html,
    base_url,
    source_name,
    limit
):

    parser = Parser()
    parser.feed(html)

    posts = []

    seen = set()

    for link in parser.links:

        title = " ".join(
            link["text"]
        ).strip()

        if not title:
            continue


        # 미래시 관련 글만
        if not any(
            keyword in title
            for keyword in KEYWORDS
        ):
            continue


        url = urljoin(
            base_url,
            link["url"]
        )


        # 게시글 주소인지 확인
        if (
            "board/view" not in url
            and
            "board_view" not in url
        ):
            continue


        if url in seen:
            continue

        seen.add(url)


        posts.append({
            "title": title,
            "url": url,
            "source": source_name,
            "dates": extract_dates(title),
        })


        if len(posts) >= limit:
            break


    return posts


# ============================================================
# 블루 아카이브 갤러리
# ============================================================

def collect_gallery():

    source = SOURCES["bluearchive_gallery"]

    html = fetch(source["url"])

    return parse_posts(
        html,
        source["url"],
        source["name"],
        MAX_GALLERY_POSTS
    )


# ============================================================
# 공식 포럼 게시판 하나
# ============================================================

def collect_one_official_board(url):

    source = SOURCES["nexon_forum"]

    try:

        html = fetch(url)

        return parse_posts(
            html,
            url,
            source["name"],
            MAX_OFFICIAL_POSTS
        )

    except Exception as error:

        print(
            f"  공식 게시판 실패: "
            f"{url} -> {error}"
        )

        return []


# ============================================================
# 공식 포럼
# ============================================================

def collect_nexon_forum():

    posts = []

    seen = set()


    # 공식 게시판 3개 동시에 실행
    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        futures = [

            pool.submit(
                collect_one_official_board,
                url
            )

            for url in OFFICIAL_BOARDS
        ]


        for future in as_completed(futures):

            try:

                result = future.result()

            except Exception:

                result = []


            for post in result:

                url = post["url"]

                if url in seen:
                    continue

                seen.add(url)

                posts.append(post)


    return posts[:MAX_OFFICIAL_POSTS]


# ============================================================
# JSON 읽기
# ============================================================

def load_json(path, default):

    try:

        if path.exists():

            return json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

    except Exception:
        pass

    return default


# ============================================================
# JSON 저장
# ============================================================

def save_json(path, data):

    path.write_text(

        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )
        + "\n",

        encoding="utf-8"
    )


# ============================================================
# 메인
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        " 블루 아카이브 미래시 빠른 자동 수집"
    )

    print(
        "=========================================="
    )


    now = (
        datetime.now()
        .astimezone()
        .isoformat(
            timespec="seconds"
        )
    )


    old_data = load_json(
        OUTPUT_FILE,
        {}
    )


    config = load_json(
        CONFIG_FILE,
        {
            "autoUpdate": {
                "validation": {}
            }
        }
    )


    # --------------------------------------------------------
    # 수집 함수
    # --------------------------------------------------------

    collectors = {

        "mollulog":
            collect_mollulog,

        "bluearchive_gallery":
            collect_gallery,

        "nexon_forum":
            collect_nexon_forum,

    }


    results = {}

    errors = {}


    print()
    print(
        "3개 출처를 동시에 수집합니다..."
    )


    # --------------------------------------------------------
    # 핵심: 3개 사이트 동시 수집
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:


        futures = {

            pool.submit(
                function
            ): source_id

            for source_id, function
            in collectors.items()

        }


        for future in as_completed(futures):

            source_id = futures[future]

            try:

                results[source_id] = (
                    future.result()
                )


                print(
                    f"[완료] "
                    f"{SOURCES[source_id]['name']} "
                    f": "
                    f"{len(results[source_id])}개"
                )


            except Exception as error:

                results[source_id] = []

                errors[source_id] = str(
                    error
                )


                print(
                    f"[실패] "
                    f"{SOURCES[source_id]['name']} "
                    f": "
                    f"{error}"
                )


    # --------------------------------------------------------
    # 데이터 정리
    # --------------------------------------------------------

    all_events = []

    evidence = []


    for source_id in (
        "mollulog",
        "bluearchive_gallery",
        "nexon_forum"
    ):

        source = SOURCES[source_id]

        items = results.get(
            source_id,
            []
        )


        # 이전 자료
        old_evidence = next(

            (
                item
                for item
                in old_data.get(
                    "sourceEvidence",
                    []
                )

                if item.get(
                    "sourceId"
                ) == source_id
            ),

            None
        )


        # ----------------------------------------------------
        # 실패한 사이트는 기존 자료 보존
        # ----------------------------------------------------

        if source_id in errors:

            if old_evidence:

                evidence_items = (
                    old_evidence.get(
                        "items",
                        []
                    )
                )

            else:

                evidence_items = []


        else:

            evidence_items = items


        evidence.append({

            "sourceId":
                source_id,

            "name":
                source["name"],

            "kind":
                source["kind"],

            "url":
                source["url"],

            "checkedAt":
                now,

            "items":
                evidence_items,

        })


        # ----------------------------------------------------
        # 일정으로 사용할 자료
        # ----------------------------------------------------

        if source_id == "mollulog":

            all_events.extend(
                items
            )


        else:

            for post in items:

                for date in post.get(
                    "dates",
                    []
                ):

                    all_events.append({

                        "date":
                            date,

                        "title":
                            post["title"],

                        "url":
                            post["url"],

                        "source":
                            post["source"],

                    })


    # --------------------------------------------------------
    # 중복 제거
    # --------------------------------------------------------

    all_events = clean_events(
        all_events
    )


    # --------------------------------------------------------
    # 전부 실패했으면 기존 일정 유지
    # --------------------------------------------------------

    if (
        not all_events
        and
        old_data.get("events")
    ):

        all_events = old_data["events"]


    # --------------------------------------------------------
    # 최종 JSON
    # --------------------------------------------------------

    data = dict(
        old_data
    )


    data["updatedAt"] = now

    data["server"] = "KR"

    data["defaultRangeMonths"] = 12

    data["supportedRangeMonths"] = [
        2,
        4,
        6,
        12,
        24,
        36
    ]


    data["events"] = (
        all_events
    )


    data["sourceEvidence"] = (
        evidence
    )


    data["errors"] = (
        errors
    )


    data["rules"] = (
        config
        .get(
            "autoUpdate",
            {}
        )
        .get(
            "validation",
            {}
        )
    )


    data["sources"] = [

        {
            "id": source_id,
            "name": source["name"],
            "type":
                {
                    "future":
                        "미래시",

                    "community":
                        "커뮤니티",

                    "official":
                        "공식",
                }[
                    source["kind"]
                ],

            "url":
                source["url"],
        }

        for source_id, source
        in SOURCES.items()

    ]


    data["note"] = (
        "빠른 모드: "
        "몰루로그, 블루 아카이브 갤러리, "
        "블루 아카이브 공식 포럼을 "
        "병렬로 수집합니다. "
        "이미지 다운로드와 OCR은 "
        "실행하지 않습니다."
    )


    # --------------------------------------------------------
    # 저장
    # --------------------------------------------------------

    save_json(
        OUTPUT_FILE,
        data
    )


    # --------------------------------------------------------
    # 결과
    # --------------------------------------------------------

    print()
    print(
        "=========================================="
    )

    print(
        " 업데이트 완료"
    )

    print(
        "=========================================="
    )

    print()

    print(
        f"일정: "
        f"{len(all_events)}개"
    )

    print(
        f"정상 출처: "
        f"{3 - len(errors)}/3"
    )

    print(
        f"오류 출처: "
        f"{len(errors)}"
    )

    print()

    print(
        f"저장 완료: "
        f"{OUTPUT_FILE}"
    )

    print(
        "=========================================="
    )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":

    main()
