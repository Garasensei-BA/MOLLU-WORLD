#!/usr/bin/env python3

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape


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

TIMEOUT = 12


# ============================================================
# 수집 대상
# ============================================================

SOURCES = {

    "mollulog": {
        "name": "몰루로그",
        "url": "https://mollulog.net/futures",
        "kind": "future",
    },

    "bluearchive_gallery": {
        "name": "블루 아카이브 갤러리",
        "url": (
            "https://gall.dcinside.com/"
            "mgallery/board/lists/?id=projectmx"
        ),
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
# 미래시 키워드
# ============================================================

KEYWORDS = [

    "미래시",
    "일섭",
    "일섭정보",
    "일섭 정보",
    "한섭",
    "픽업",
    "모집",
    "이벤트",
    "총력전",
    "대결전",
    "종합전술시험",
    "제약해제결전",
    "연합작전",
    "업데이트",
    "일정",
    "점검",
    "로드맵",
    "페스",
    "복각",

]


# ============================================================
# 날짜 정규식
# ============================================================

FULL_DATE_RE = re.compile(
    r"(?<!\d)"
    r"(20\d{2})"
    r"\s*[-./년]\s*"
    r"(\d{1,2})"
    r"\s*[-./월]\s*"
    r"(\d{1,2})"
    r"\s*일?"
)


SHORT_DATE_RE = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})"
    r"\s*[-./]\s*"
    r"(\d{1,2})"
    r"(?!\d)"
)


DATE_ONLY_RE = re.compile(
    r"^20\d{2}-\d{2}-\d{2}$"
)


# ============================================================
# 몰루로그에서 자주 나오는 콘텐츠 종류
# ============================================================

CONTENT_TYPES = {

    "메인 스토리",
    "이벤트",
    "복각 이벤트",
    "이벤트 상설화",
    "미니 스토리",
    "미니 이벤트",
    "캠페인",
    "종합전술시험",
    "픽업 모집",
    "대결전",
    "총력전",
    "제약해제결전",
    "연합작전",
    "공식 방송",
    "리콜렉트 모집",

}


BAD_TITLES = {

    "의견을 남겨보세요",
    "학생",
    "픽업 학생",
    "픽업 대상 외 모집 가능 학생",
    "컨텐츠 필터",
    "이미지",
    "신규",
    "복각",
    "배포",
    "한정 신규",
    "한정 복각",
    "페스 신규",
    "페스 복각",
    "리콜렉트",

}


# ============================================================
# 문자열 정리
# ============================================================

def clean(text):

    text = unescape(
        text or ""
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# 날짜 추출
# ============================================================

def extract_dates(
    text,
    default_year=None
):

    results = []

    text = clean(text)


    # 2026-10-06
    for match in FULL_DATE_RE.finditer(
        text
    ):

        year = int(
            match.group(1)
        )

        month = int(
            match.group(2)
        )

        day = int(
            match.group(3)
        )


        if (
            2020 <= year <= 2100
            and 1 <= month <= 12
            and 1 <= day <= 31
        ):

            results.append(
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )


    # 10/06
    if default_year:

        for match in SHORT_DATE_RE.finditer(
            text
        ):

            month = int(
                match.group(1)
            )

            day = int(
                match.group(2)
            )


            if (
                1 <= month <= 12
                and 1 <= day <= 31
            ):

                results.append(
                    f"{default_year:04d}-"
                    f"{month:02d}-"
                    f"{day:02d}"
                )


    return sorted(
        set(results)
    )


# ============================================================
# 몰루로그 HTML 블록 파서
#
# 기존 코드의 핵심 문제:
#
# 날짜 주변 18개 토큰만 탐색
#
# ↓
#
# 몰루로그 카드 구조가 복잡해질수록
# 날짜와 콘텐츠의 연결이 끊김
#
# 이번 버전:
#
# 페이지 전체를 순서대로 읽고
# "현재 날짜 ~ 다음 날짜"를 하나의 구간으로 처리
# ============================================================

class BlockParser(
    HTMLParser
):

    BLOCK_TAGS = {

        "p",
        "div",
        "section",
        "article",
        "li",
        "tr",
        "td",
        "th",

        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",

        "header",
        "main",

        "button",
        "a",

    }


    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.blocks = []

        self.stack = []

        self.buffer = []

        self.current_href = None


    def handle_starttag(
        self,
        tag,
        attrs
    ):

        attrs = dict(
            attrs
        )


        if tag in self.BLOCK_TAGS:

            self.stack.append(
                tag
            )


            if tag == "a":

                self.current_href = (
                    attrs.get(
                        "href"
                    )
                    or ""
                )


        if tag == "br":

            self.buffer.append(
                " "
            )


    def handle_endtag(
        self,
        tag
    ):

        if tag not in self.BLOCK_TAGS:
            return


        text = clean(
            " ".join(
                self.buffer
            )
        )


        if text:

            self.blocks.append({

                "text":
                    text,

                "url":
                    self.current_href
                    or "",

            })


        self.buffer = []


        if tag == "a":

            self.current_href = None


        if self.stack:

            self.stack.pop()


    def handle_data(
        self,
        data
    ):

        text = clean(
            data
        )


        if text:

            self.buffer.append(
                text
            )


    def finish(self):

        text = clean(
            " ".join(
                self.buffer
            )
        )


        if text:

            self.blocks.append({

                "text":
                    text,

                "url":
                    self.current_href
                    or "",

            })


        return self.blocks


# ============================================================
# 웹 요청
# ============================================================

def fetch(
    url
):

    request = Request(

        url,

        headers={

            "User-Agent":
                USER_AGENT,

            "Accept":
                (
                    "text/html,"
                    "application/xhtml+xml,"
                    "*/*;q=0.8"
                ),

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",

            "Cache-Control":
                "no-cache",

        }

    )


    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        encoding = (
            response.headers.get_content_charset()
            or "utf-8"
        )


        return response.read().decode(
            encoding,
            errors="replace"
        )


# ============================================================
# 제목 점수
# ============================================================

def title_score(
    text
):

    text = clean(
        text
    )


    if not text:

        return -999


    if (
        len(text) > 180
    ):

        return -999


    if (
        text in BAD_TITLES
    ):

        return -999


    if DATE_ONLY_RE.match(
        text
    ):

        return -999


    if extract_dates(
        text
    ):

        return -999


    score = 0


    if any(
        keyword in text
        for keyword in CONTENT_TYPES
    ):

        score += 2


    if any(
        keyword in text
        for keyword in KEYWORDS
    ):

        score += 1


    if (
        3 <= len(text) <= 100
    ):

        score += 2


    if (
        len(text) <= 60
    ):

        score += 1


    return score


# ============================================================
# 몰루로그 제목 선택
# ============================================================

def choose_mollulog_title(
    blocks
):

    candidates = []


    for block in blocks:

        text = clean(
            block.get(
                "text",
                ""
            )
        )


        if not text:

            continue


        if (
            text in BAD_TITLES
        ):

            continue


        if (
            "의견을 남겨보세요"
            in text
        ):

            continue


        if text.isdigit():

            continue


        # 날짜 제거
        text = FULL_DATE_RE.sub(
            " ",
            text
        )

        text = clean(
            text
        )


        if not text:

            continue


        score = title_score(
            text
        )


        if score < 0:

            continue


        candidates.append((

            score,

            text,

            block.get(
                "url",
                ""
            ),

        ))


    if not candidates:

        return (
            "",
            ""
        )


    candidates.sort(

        key=lambda item: (

            item[0],

            min(
                len(item[1]),
                100
            ),

        ),

        reverse=True

    )


    return (

        candidates[0][1],

        candidates[0][2],

    )


# ============================================================
# 몰루로그
# ============================================================

def collect_mollulog():

    source = SOURCES[
        "mollulog"
    ]


    print(
        "[몰루로그] 페이지 다운로드..."
    )


    html = fetch(
        source["url"]
    )


    parser = BlockParser()

    parser.feed(
        html
    )

    blocks = parser.finish()


    current_year = (
        datetime.now().year
    )


    # --------------------------------------------------------
    # 전체 페이지에서 날짜 위치 탐색
    # --------------------------------------------------------

    date_positions = []


    for index, block in enumerate(
        blocks
    ):

        dates = extract_dates(

            block.get(
                "text",
                ""
            ),

            current_year

        )


        for date in dates:

            date_positions.append(

                (
                    index,
                    date
                )

            )


    # 중복 제거
    unique_positions = []

    seen = set()


    for position, date in date_positions:

        key = (
            position,
            date
        )


        if key in seen:

            continue


        seen.add(
            key
        )


        unique_positions.append(

            (
                position,
                date
            )

        )


    # --------------------------------------------------------
    # 날짜 → 다음 날짜까지 하나의 카드/구간
    # --------------------------------------------------------

    events = []


    for index, (
        start,
        date
    ) in enumerate(
        unique_positions
    ):


        if (
            index + 1
            < len(unique_positions)
        ):

            end = (
                unique_positions[
                    index + 1
                ][0]
            )

        else:

            end = len(
                blocks
            )


        segment = blocks[
            start:end
        ]


        title, url = choose_mollulog_title(
            segment
        )


        # ----------------------------------------------------
        # 콘텐츠 종류만 잡힌 경우
        # 실제 이벤트명을 추가 탐색
        # ----------------------------------------------------

        if (
            title in CONTENT_TYPES
        ):

            alternatives = []


            for block in segment[1:40]:

                candidate = clean(
                    block.get(
                        "text",
                        ""
                    )
                )


                if not candidate:

                    continue


                if candidate in BAD_TITLES:

                    continue


                if (
                    "의견을 남겨보세요"
                    in candidate
                ):

                    continue


                if candidate.isdigit():

                    continue


                if extract_dates(
                    candidate
                ):

                    continue


                if candidate == title:

                    continue


                if len(candidate) > 120:

                    continue


                score = title_score(
                    candidate
                )


                if score >= 0:

                    alternatives.append((

                        score,

                        candidate,

                        block.get(
                            "url",
                            ""
                        ),

                    ))


            if alternatives:

                alternatives.sort(

                    key=lambda item: (

                        item[0],

                        len(item[1])

                    ),

                    reverse=True

                )


                best = alternatives[0]


                if (
                    best[0]
                    >= title_score(
                        title
                    )
                ):

                    title = best[1]

                    if best[2]:

                        url = best[2]


        # ----------------------------------------------------
        # 그래도 제목을 못 찾으면 날짜는 살린다.
        # ----------------------------------------------------

        if not title:

            title = (
                "몰루로그 미래시 일정"
            )


        events.append({

            "date":
                date,

            "title":
                clean(
                    title
                ),

            "url":
                (
                    urljoin(
                        source["url"],
                        url
                    )
                    if url
                    else
                    source["url"]
                ),

            "source":
                source["name"],

        })


    events = clean_events(
        events
    )


    print(
        "[몰루로그] "
        f"{len(events)}개 일정 발견"
    )


    if events:

        print(
            "[몰루로그] 범위: "
            f"{events[0]['date']} ~ "
            f"{events[-1]['date']}"
        )


    return events


# ============================================================
# 일반 링크 파서
# ============================================================

class LinkParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.links = []

        self.current = None


    def handle_starttag(
        self,
        tag,
        attrs
    ):

        if tag != "a":

            return


        attrs = dict(
            attrs
        )


        href = attrs.get(
            "href"
        )


        if href:

            self.current = {

                "url":
                    href,

                "text":
                    [],

            }


    def handle_endtag(
        self,
        tag
    ):

        if (
            tag == "a"
            and self.current
        ):

            text = clean(
                " ".join(
                    self.current[
                        "text"
                    ]
                )
            )


            if text:

                self.links.append({

                    "url":
                        self.current[
                            "url"
                        ],

                    "text":
                        text,

                })


            self.current = None


    def handle_data(
        self,
        data
    ):

        if self.current:

            self.current[
                "text"
            ].append(
                data
            )


# ============================================================
# 게시글 파싱
# ============================================================

def parse_posts(

    html,

    base_url,

    source_name,

    limit=50

):

    parser = LinkParser()

    parser.feed(
        html
    )


    posts = []

    seen = set()


    for link in parser.links:

        title = clean(
            link["text"]
        )


        if not title:

            continue


        if not any(
            keyword in title
            for keyword in KEYWORDS
        ):

            continue


        url = urljoin(

            base_url,

            link["url"]

        )


        if url in seen:

            continue


        # 너무 강한 URL 필터는 사용하지 않는다.
        # 사이트 구조가 바뀌어도 수집되도록 한다.

        if not any(
            marker in url
            for marker in (
                "board/view",
                "board_view",
                "article",
                "view?id=",
                "/view/"
            )
        ):

            continue


        seen.add(
            url
        )


        posts.append({

            "title":
                title,

            "url":
                url,

            "source":
                source_name,

            "dates":
                extract_dates(
                    title,
                    datetime.now().year
                ),

        })


        if len(posts) >= limit:

            break


    return posts


# ============================================================
# 블루 아카이브 갤러리
# ============================================================

def collect_gallery():

    source = SOURCES[
        "bluearchive_gallery"
    ]


    try:

        html = fetch(
            source["url"]
        )


        posts = parse_posts(

            html,

            source["url"],

            source["name"],

            50

        )


        print(
            "[블루아카이브 갤러리] "
            f"{len(posts)}개 게시글 발견"
        )


        return posts


    except Exception as error:

        print(
            "[블루아카이브 갤러리 실패] "
            f"{error}"
        )


        return []


# ============================================================
# 공식 포럼 게시판 하나
# ============================================================

def collect_one_official_board(
    url
):

    source = SOURCES[
        "nexon_forum"
    ]


    try:

        html = fetch(
            url
        )


        return parse_posts(

            html,

            url,

            source["name"],

            30

        )


    except Exception as error:

        print(
            "[공식 포럼 실패] "
            f"{url} -> {error}"
        )


        return []


# ============================================================
# 공식 포럼
# ============================================================

def collect_nexon_forum():

    posts = []

    seen = set()


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


        for future in as_completed(
            futures
        ):

            try:

                rows = future.result()

            except Exception:

                rows = []


            for row in rows:

                url = row[
                    "url"
                ]


                if url in seen:

                    continue


                seen.add(
                    url
                )


                posts.append(
                    row
                )


    print(
        "[공식 포럼] "
        f"{len(posts)}개 게시글 발견"
    )


    return posts[:80]


# ============================================================
# 일정 정리
# ============================================================

def clean_events(
    events
):

    result = []

    seen = set()


    for event in events:

        date = clean(
            str(
                event.get(
                    "date",
                    ""
                )
            )
        )


        title = clean(
            str(
                event.get(
                    "title",
                    ""
                )
            )
        )


        if not DATE_ONLY_RE.match(
            date
        ):

            continue


        if not title:

            continue


        try:

            datetime.strptime(
                date,
                "%Y-%m-%d"
            )


        except ValueError:

            continue


        key = (

            date,

            title,

            event.get(
                "source",
                ""
            ),

        )


        if key in seen:

            continue


        seen.add(
            key
        )


        item = dict(
            event
        )


        item["date"] = date

        item["title"] = title


        result.append(
            item
        )


    result.sort(

        key=lambda item: (

            item["date"],

            item["title"]

        )

    )


    return result


# ============================================================
# JSON
# ============================================================

def load_json(
    path,
    default
):

    try:

        if path.exists():

            return json.loads(

                path.read_text(
                    encoding="utf-8"
                )

            )


    except Exception as error:

        print(
            "[JSON 읽기 실패] "
            f"{error}"
        )


    return default


def save_json(
    path,
    data
):

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )


    temp_path.write_text(

        json.dumps(

            data,

            ensure_ascii=False,

            indent=2

        )
        + "\n",

        encoding="utf-8"

    )


    temp_path.replace(
        path
    )


# ============================================================
# 메인
# ============================================================

def main():

    print()

    print(
        "=========================================="
    )

    print(
        " 블루 아카이브 미래시 빠른 업데이트"
    )

    print(
        " 3사이트 + 몰루로그 전체 날짜 파싱"
    )

    print(
        "=========================================="
    )

    print()


    old_data = load_json(

        OUTPUT_FILE,

        {}

    )


    results = {}

    errors = {}


    collectors = {

        "mollulog":
            collect_mollulog,

        "bluearchive_gallery":
            collect_gallery,

        "nexon_forum":
            collect_nexon_forum,

    }


    print(
        "3개 출처 동시 수집 시작..."
    )


    # --------------------------------------------------------
    # 3개 사이트 동시에 수집
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        futures = {

            pool.submit(
                collector
            ):
                source_id

            for source_id, collector
            in collectors.items()

        }


        for future in as_completed(
            futures
        ):

            source_id = futures[
                future
            ]


            try:

                results[
                    source_id
                ] = future.result()


                print(

                    f"[완료] "
                    f"{SOURCES[source_id]['name']} "
                    f": "
                    f"{len(results[source_id])}개"

                )


            except Exception as error:

                results[
                    source_id
                ] = []


                errors[
                    source_id
                ] = str(error)


                print(

                    f"[실패] "
                    f"{SOURCES[source_id]['name']} "
                    f": "
                    f"{error}"

                )


    # --------------------------------------------------------
    # 몰루로그 일정
    # --------------------------------------------------------

    mollulog_events = results.get(
        "mollulog",
        []
    )


    # --------------------------------------------------------
    # 몰루로그 성공 시 새 데이터 사용
    #
    # 실패했을 경우 기존 데이터 삭제 금지
    # --------------------------------------------------------

    if mollulog_events:

        events = mollulog_events

    else:

        if isinstance(
            old_data,
            dict
        ):

            events = old_data.get(
                "events",
                []
            )

        else:

            events = []


        print(
            "[경고] 몰루로그 일정 수집 실패."
        )

        print(
            "       기존 future-data.json 일정을 유지합니다."
        )


    events = clean_events(
        events
    )


    gallery = results.get(

        "bluearchive_gallery",

        []

    )


    official = results.get(

        "nexon_forum",

        []

    )


    # --------------------------------------------------------
    # 기존 JSON 구조 유지
    #
    # 프론트엔드 호환성을 위해
    # 기존 top-level 필드를 삭제하지 않는다.
    # --------------------------------------------------------

    if isinstance(
        old_data,
        dict
    ):

        data = dict(
            old_data
        )

    else:

        data = {}


    data["events"] = events


    data["updatedAt"] = (

        datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )

    )


    # --------------------------------------------------------
    # 출처 정보
    # --------------------------------------------------------

    data["sources"] = {

        "mollulog": {

            "name":
                SOURCES[
                    "mollulog"
                ]["name"],

            "url":
                SOURCES[
                    "mollulog"
                ]["url"],

            "count":
                len(
                    mollulog_events
                ),

        },


        "bluearchive_gallery": {

            "name":
                SOURCES[
                    "bluearchive_gallery"
                ]["name"],

            "url":
                SOURCES[
                    "bluearchive_gallery"
                ]["url"],

            "count":
                len(
                    gallery
                ),

        },


        "nexon_forum": {

            "name":
                SOURCES[
                    "nexon_forum"
                ]["name"],

            "url":
                SOURCES[
                    "nexon_forum"
                ]["url"],

            "count":
                len(
                    official
                ),

        },

    }


    # --------------------------------------------------------
    # 보조 출처
    # --------------------------------------------------------

    data["evidence"] = (

        gallery
        + official

    )


    # --------------------------------------------------------
    # 오류 기록
    # --------------------------------------------------------

    data["errors"] = errors


    # --------------------------------------------------------
    # 저장
    # --------------------------------------------------------

    save_json(

        OUTPUT_FILE,

        data

    )


    # --------------------------------------------------------
    # 결과 검증
    # --------------------------------------------------------

    dates = [

        event["date"]

        for event in events

    ]


    print()

    print(
        "=========================================="
    )

    print(
        " 업데이트 결과"
    )

    print(
        "=========================================="
    )


    print(
        f"총 일정: {len(events)}개"
    )


    if dates:

        print(
            f"수집 범위: "
            f"{min(dates)} ~ {max(dates)}"
        )

    else:

        print(
            "⚠ 일정이 하나도 없습니다."
        )


    # --------------------------------------------------------
    # 10월 확인
    # --------------------------------------------------------

    if "2026-10-06" in dates:

        print(
            "✅ 2026-10-06 확인"
        )

    else:

        print(
            "⚠ 2026-10-06 없음"
        )


    # --------------------------------------------------------
    # 12월 확인
    # --------------------------------------------------------

    if "2026-12-15" in dates:

        print(
            "✅ 2026-12-15 확인"
        )

    else:

        print(
            "⚠ 2026-12-15 없음"
        )


    if errors:

        print()

        print(
            "발생한 오류:"
        )


        for source, error in errors.items():

            print(
                f"- {source}: {error}"
            )


    print()

    print(
        "future-data.json 저장 완료."
    )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":

    main()
