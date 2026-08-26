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
    "연합작전",
    "업데이트",
    "일정",
    "점검",
    "로드맵",
    "페스",
    "복각",
    "스토리",
)


# ============================================================
# 날짜 정규식
# ============================================================

DATE_RE = re.compile(
    r"(?<!\d)"
    r"(20\d{2})"
    r"[-./년]\s*"
    r"(\d{1,2})"
    r"[-./월]\s*"
    r"(\d{1,2})"
    r"\s*일?"
)


# ============================================================
# 몰루로그에서 의미 있는 분류
# ============================================================

CATEGORY_WORDS = {

    "메인 스토리",
    "미니 스토리",

    "이벤트",
    "미니 이벤트",

    "캠페인",

    "복각 이벤트",
    "이벤트 상설화",
    "이벤트 100회 무료",

    "픽업 모집",
    "페스 모집",

    "총력전",
    "대결전",
    "제약해제결전",
    "연합작전",

    "종합전술시험",

    "공식 방송",

}


# ============================================================
# 제거할 텍스트
# ============================================================

BAD_TEXT = {

    "의견을 남겨보세요",
    "컨텐츠 필터",

    "몰루 로그",
    "로그인",
    "로그인하기",
    "더 보기",

}


# ============================================================
# 문자열 정리
# ============================================================

def clean(text):

    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# ============================================================
# 날짜 추출
# ============================================================

def extract_dates(text):

    results = []

    if not text:
        return results

    for match in DATE_RE.finditer(text):

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if not (
            2020 <= year <= 2100
        ):
            continue

        if not (
            1 <= month <= 12
        ):
            continue

        if not (
            1 <= day <= 31
        ):
            continue

        results.append(
            f"{year:04d}-"
            f"{month:02d}-"
            f"{day:02d}"
        )

    return sorted(
        set(results)
    )


# ============================================================
# 웹페이지 가져오기
# ============================================================

def fetch(url):

    request = Request(

        url,

        headers={

            "User-Agent":
                USER_AGENT,

            "Accept":
                (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.8",

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
# HTML 파서
#
# 중요:
#
# 기존 코드 문제 중 하나는
# <script> 안의 JavaScript까지 텍스트로 읽어버릴 수 있다는 것.
#
# 그러면 날짜/텍스트가 엉뚱하게 섞인다.
#
# 이번 파서는 script/style/noscript/template을 완전히 무시한다.
# ============================================================

class PageParser(HTMLParser):

    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "template",
    }


    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.tokens = []

        self.skip_depth = 0

        self.link_depth = 0

        self.current_link = None


    def handle_starttag(
        self,
        tag,
        attrs
    ):

        tag = tag.lower()

        if tag in self.SKIP_TAGS:

            self.skip_depth += 1

            return


        if self.skip_depth:

            return


        if tag == "a":

            attributes = dict(
                attrs
            )

            href = attributes.get(
                "href",
                ""
            )

            self.link_depth += 1


            if self.current_link is None:

                self.current_link = {

                    "type":
                        "link",

                    "text":
                        [],

                    "url":
                        href,

                }


    def handle_endtag(
        self,
        tag
    ):

        tag = tag.lower()


        if tag in self.SKIP_TAGS:

            if self.skip_depth:

                self.skip_depth -= 1

            return


        if self.skip_depth:

            return


        if (
            tag == "a"
            and
            self.link_depth
        ):

            self.link_depth -= 1


            if (
                self.link_depth == 0
                and
                self.current_link is not None
            ):

                text = clean(
                    " ".join(
                        self.current_link[
                            "text"
                        ]
                    )
                )


                if text:

                    self.tokens.append({

                        "type":
                            "link",

                        "text":
                            text,

                        "url":
                            self.current_link[
                                "url"
                            ],

                    })


                self.current_link = None


    def handle_data(
        self,
        data
    ):

        if self.skip_depth:

            return


        text = clean(
            data
        )


        if not text:

            return


        if self.current_link is not None:

            self.current_link[
                "text"
            ].append(
                text
            )

        else:

            self.tokens.append({

                "type":
                    "text",

                "text":
                    text,

            })


# ============================================================
# 토큰에 날짜가 있는지
# ============================================================

def token_dates(token):

    return extract_dates(
        token.get(
            "text",
            ""
        )
    )


# ============================================================
# 노이즈 판별
# ============================================================

def is_noise(text):

    text = clean(
        text
    )

    if not text:

        return True


    if text in BAD_TEXT:

        return True


    if text.isdigit():

        return True


    if re.fullmatch(
        r"\d+\s*(명|회|일|일 후|일간)?",
        text
    ):

        return True


    return False


# ============================================================
# 링크 제목 판별
# ============================================================

def meaningful_link(text):

    text = clean(
        text
    )


    if is_noise(text):

        return False


    if text.startswith(
        "Image:"
    ):

        return False


    if len(text) < 2:

        return False


    if len(text) > 140:

        return False


    if extract_dates(text):

        return False


    return True


# ============================================================
# 몰루로그 날짜 블록 제목 만들기
#
# 날짜 하나부터 다음 날짜가 나오기 전까지를 하나의 블록으로 본다.
# ============================================================

def make_mollulog_title(
    block
):

    labels = []

    links = []

    other = []


    for token in block:

        text = clean(
            token.get(
                "text",
                ""
            )
        )


        if is_noise(text):

            continue


        if extract_dates(text):

            continue


        # 링크
        if token.get(
            "type"
        ) == "link":

            if meaningful_link(
                text
            ):

                if text not in links:

                    links.append(
                        text
                    )

            continue


        # 카테고리
        if text in CATEGORY_WORDS:

            if text not in labels:

                labels.append(
                    text
                )

            continue


        # 종합전술시험 등
        if re.fullmatch(
            r"\d+\s*차\s*[:：].{1,100}",
            text
        ):

            if text not in other:

                other.append(
                    text
                )

            continue


        # 특정 컨텐츠 이름
        if (
            len(text) <= 80
            and
            text in {

                "세트의 분노",
                "티페레트",
                "약사의 방황",
                "잡초는 홀로 피지 않는다",
                "이부키의 가출사건",

            }
        ):

            if text not in other:

                other.append(
                    text
                )


    parts = []


    for value in (
        labels
        + other
        + links
    ):

        if value not in parts:

            parts.append(
                value
            )


    # 혹시 아무것도 못 찾았을 때
    if not parts:

        for token in block:

            text = clean(
                token.get(
                    "text",
                    ""
                )
            )


            if is_noise(text):

                continue


            if extract_dates(text):

                continue


            parts.append(
                text
            )


            if len(parts) >= 5:

                break


    # 너무 길어지는 것 방지
    return clean(
        " · ".join(
            parts[:8]
        )
    )[:500]


# ============================================================
# 몰루로그 수집
# ============================================================

def collect_mollulog():

    source = SOURCES[
        "mollulog"
    ]


    print(
        "[몰루로그] 페이지 가져오는 중..."
    )


    html = fetch(
        source["url"]
    )


    parser = PageParser()

    parser.feed(
        html
    )


    tokens = parser.tokens


    # --------------------------------------------------------
    # 날짜 위치 찾기
    # --------------------------------------------------------

    date_indexes = []


    for index, token in enumerate(
        tokens
    ):

        dates = token_dates(
            token
        )


        if not dates:

            continue


        date_indexes.append(
            (
                index,
                dates[0]
            )
        )


    print(
        f"[몰루로그] 날짜 {len(date_indexes)}개 발견"
    )


    events = []


    # --------------------------------------------------------
    # 날짜 ~ 다음 날짜 사이를 하나의 일정 블록으로 처리
    # --------------------------------------------------------

    for position, (
        index,
        date
    ) in enumerate(
        date_indexes
    ):

        if (
            position + 1
            <
            len(date_indexes)
        ):

            next_index = (
                date_indexes[
                    position + 1
                ][0]
            )

        else:

            next_index = len(
                tokens
            )


        block = tokens[
            index + 1:
            next_index
        ]


        title = make_mollulog_title(
            block
        )


        if not title:

            title = (
                "몰루로그 미래시 일정"
            )


        events.append({

            "date":
                date,

            "title":
                title,

            "url":
                source["url"],

            "source":
                source["name"],

            "status":
                "예상",

        })


    return clean_events(
        events
    )


# ============================================================
# 게시글 추출
# ============================================================

def parse_posts(
    html,
    base_url,
    source_name,
    limit=80
):

    parser = PageParser()

    parser.feed(
        html
    )


    posts = []

    seen = set()


    for token in parser.tokens:

        if token.get(
            "type"
        ) != "link":

            continue


        title = clean(
            token.get(
                "text",
                ""
            )
        )


        if not title:

            continue


        if title.startswith(
            "Image:"
        ):

            continue


        if not any(
            keyword in title
            for keyword in KEYWORDS
        ):

            continue


        url = urljoin(

            base_url,

            token.get(
                "url",
                ""
            )

        )


        if not url.startswith(
            "http"
        ):

            continue


        # DCInside
        if (
            "gall.dcinside.com"
            in url
        ):

            if (
                "board/view"
                not in url
                and
                "board_view"
                not in url
            ):

                continue


        # 넥슨
        if (
            "forum.nexon.com"
            in url
        ):

            if "board" not in url:

                continue


        if url in seen:

            continue


        seen.add(
            url
        )


        posts.append({

            "title":
                title[:300],

            "url":
                url,

            "source":
                source_name,

            "dates":
                extract_dates(
                    title
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


    print(
        "[갤러리] 수집 중..."
    )


    html = fetch(
        source["url"]
    )


    return parse_posts(

        html,

        source["url"],

        source["name"],

        80

    )


# ============================================================
# 공식 포럼 하나
# ============================================================

def collect_one_official(
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

            50

        )


    except Exception as error:

        print(
            "[공식 포럼 실패]",
            url,
            "->",
            error
        )


        return []


# ============================================================
# 공식 포럼
# ============================================================

def collect_nexon_forum():

    posts = []

    seen = set()


    # 게시판 3개 동시에
    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        futures = [

            pool.submit(
                collect_one_official,
                url
            )

            for url in OFFICIAL_BOARDS

        ]


        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

            except Exception as error:

                print(
                    "[공식 포럼 작업 실패]",
                    error
                )

                result = []


            for post in result:

                url = post[
                    "url"
                ]


                if url in seen:

                    continue


                seen.add(
                    url
                )


                posts.append(
                    post
                )


    return posts[:120]


# ============================================================
# 이벤트 중복 제거
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


        if date:

            try:

                datetime.strptime(
                    date,
                    "%Y-%m-%d"
                )

            except ValueError:

                continue


        if not title:

            continue


        key = (

            date,

            title,

            event.get(
                "source",
                ""
            )

        )


        if key in seen:

            continue


        seen.add(
            key
        )


        event[
            "date"
        ] = date


        event[
            "title"
        ] = title


        result.append(
            event
        )


    result.sort(

        key=lambda x: (

            x.get(
                "date",
                "9999-99-99"
            ),

            x.get(
                "title",
                ""
            )

        )

    )


    return result


# ============================================================
# JSON 읽기
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
            "[JSON 읽기 실패]",
            error
        )


    return default


# ============================================================
# JSON 저장
# ============================================================

def save_json(
    path,
    data
):

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

    print()
    print(
        "=========================================="
    )
    print(
        " 블루 아카이브 미래시 빠른 자동 업데이트"
    )
    print(
        "=========================================="
    )
    print()


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

        {}

    )


    results = {}

    errors = {}


    # ========================================================
    # 3개 사이트 동시에 조사
    # ========================================================

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
    print()


    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        futures = {

            pool.submit(
                function
            ):
                source_id

            for source_id, function
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

                    "[완료]",
                    SOURCES[
                        source_id
                    ]["name"],

                    ":",

                    len(
                        results[
                            source_id
                        ]
                    ),

                    "개"

                )


            except Exception as error:

                results[
                    source_id
                ] = []


                errors[
                    source_id
                ] = str(
                    error
                )


                print(

                    "[실패]",

                    SOURCES[
                        source_id
                    ]["name"],

                    ":",

                    error

                )


    # ========================================================
    # 결과
    # ========================================================

    mollulog_events = results.get(
        "mollulog",
        []
    )


    gallery_posts = results.get(
        "bluearchive_gallery",
        []
    )


    forum_posts = results.get(
        "nexon_forum",
        []
    )


    # ========================================================
    # 메인 미래시
    #
    # 몰루로그의 날짜 일정만 메인 events에 넣는다.
    #
    # 갤러리/공식 포럼은 보조 자료로 저장한다.
    #
    # 이렇게 해야 게시판 글의 날짜가
    # 미래시 일정으로 잘못 들어가는 것을 막을 수 있다.
    # ========================================================

    all_events = clean_events(
        mollulog_events
    )


    # ========================================================
    # 출처별 증거
    # ========================================================

    evidence = [

        {

            "sourceId":
                "mollulog",

            "name":
                SOURCES[
                    "mollulog"
                ]["name"],

            "kind":
                "future",

            "url":
                SOURCES[
                    "mollulog"
                ]["url"],

            "checkedAt":
                now,

            "items":
                mollulog_events,

        },


        {

            "sourceId":
                "bluearchive_gallery",

            "name":
                SOURCES[
                    "bluearchive_gallery"
                ]["name"],

            "kind":
                "community",

            "url":
                SOURCES[
                    "bluearchive_gallery"
                ]["url"],

            "checkedAt":
                now,

            "items":
                gallery_posts,

        },


        {

            "sourceId":
                "nexon_forum",

            "name":
                SOURCES[
                    "nexon_forum"
                ]["name"],

            "kind":
                "official",

            "url":
                SOURCES[
                    "nexon_forum"
                ]["url"],

            "checkedAt":
                now,

            "items":
                forum_posts,

        },

    ]


    # ========================================================
    # 기존 JSON 구조 유지
    # ========================================================

    new_data = dict(
        old_data
    )


    new_data[
        "updatedAt"
    ] = now


    new_data[
        "server"
    ] = "KR"


    # 기간 제한을 넓게 유지
    new_data[
        "defaultRangeMonths"
    ] = 12


    new_data[
        "supportedRangeMonths"
    ] = [

        2,
        4,
        6,
        12,
        24,
        36,

    ]


    new_data[
        "events"
    ] = all_events


    new_data[
        "sourceEvidence"
    ] = evidence


    new_data[
        "errors"
    ] = errors


    validation = (

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


    new_data[
        "rules"
    ] = validation


    # ========================================================
    # 출처
    # ========================================================

    new_data[
        "sources"
    ] = [

        {

            "id":
                "mollulog",

            "name":
                "몰루로그",

            "type":
                "미래시",

            "url":
                SOURCES[
                    "mollulog"
                ]["url"],

        },


        {

            "id":
                "bluearchive_gallery",

            "name":
                "블루 아카이브 갤러리",

            "type":
                "커뮤니티",

            "url":
                SOURCES[
                    "bluearchive_gallery"
                ]["url"],

        },


        {

            "id":
                "nexon_forum",

            "name":
                "블루 아카이브 공식 포럼",

            "type":
                "공식",

            "url":
                SOURCES[
                    "nexon_forum"
                ]["url"],

        },

    ]


    new_data[
        "note"
    ] = (

        "몰루로그를 메인 미래시 일정 출처로 사용하고, "

        "블루 아카이브 갤러리와 "

        "블루 아카이브 공식 포럼을 "

        "보조 출처로 수집합니다. "

        "몰루로그의 날짜 일정은 페이지의 실제 날짜 블록을 "

        "기준으로 추출합니다."

    )


    # ========================================================
    # 저장
    # ========================================================

    save_json(

        OUTPUT_FILE,

        new_data

    )


    # ========================================================
    # 결과 출력
    # ========================================================

    dates = [

        event["date"]

        for event in all_events

        if event.get(
            "date"
        )

    ]


    if dates:

        min_date = min(
            dates
        )

        max_date = max(
            dates
        )

    else:

        min_date = "없음"

        max_date = "없음"


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


    print(
        f"몰루로그 일정: "
        f"{len(mollulog_events)}개"
    )


    print(
        f"갤러리 자료: "
        f"{len(gallery_posts)}개"
    )


    print(
        f"공식 포럼 자료: "
        f"{len(forum_posts)}개"
    )


    print(
        f"전체 일정: "
        f"{len(all_events)}개"
    )


    print(
        f"일정 범위: "
        f"{min_date} ~ {max_date}"
    )


    print(
        f"오류 출처: "
        f"{len(errors)}개"
    )


    print(
        f"저장: "
        f"{OUTPUT_FILE}"
    )


    # ========================================================
    # 12월 확인
    # ========================================================

    december = [

        event

        for event in all_events

        if event.get(
            "date",
            ""
        ).startswith(
            "2026-12-"
        )

    ]


    print()
    print(
        f"2026년 12월 일정: "
        f"{len(december)}개"
    )


    for event in december:

        print(

            "-",

            event[
                "date"
            ],

            "|",

            event[
                "title"
            ][:180]

        )


    # ========================================================
    # 안전장치
    # ========================================================

    if not december:

        print()
        print(
            "[경고]"
        )

        print(
            "2026년 12월 일정이 0개입니다."
        )

        print(
            "몰루로그 HTML 구조가 변경되었는지 확인하세요."
        )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":

    main()
