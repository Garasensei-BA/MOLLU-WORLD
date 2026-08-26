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

TIMEOUT = 10


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
# 날짜
# ============================================================

FULL_DATE_PATTERN = re.compile(
    r"(20\d{2})\s*"
    r"(?:[.\-/년]\s*)"
    r"(\d{1,2})\s*"
    r"(?:[.\-/월]\s*)"
    r"(\d{1,2})\s*일?"
)


SHORT_DATE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*[./\-]\s*(\d{1,2})"
    r"(?!\d)"
)


def extract_dates(text, default_year=None):

    results = []

    if not text:
        return results


    # 2026-10-06
    for match in FULL_DATE_PATTERN.finditer(text):

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if (
            2020 <= year <= 2100
            and 1 <= month <= 12
            and 1 <= day <= 31
        ):

            results.append(
                f"{year:04d}-{month:02d}-{day:02d}"
            )


    # 10.06 / 10/06
    if default_year is not None:

        for match in SHORT_DATE_PATTERN.finditer(text):

            month = int(match.group(1))
            day = int(match.group(2))

            if (
                1 <= month <= 12
                and 1 <= day <= 31
            ):

                results.append(
                    f"{default_year:04d}-"
                    f"{month:02d}-"
                    f"{day:02d}"
                )


    return sorted(set(results))


# ============================================================
# 몰루로그용 HTML 토큰 파서
#
# 핵심:
# 기존에는 모든 텍스트를 한 줄로 합쳤기 때문에
# 9월 이후 일정이 제대로 묶이지 않았음.
#
# 이번에는 링크와 텍스트의 순서를 보존한다.
# ============================================================

class MollulogParser(HTMLParser):

    def __init__(self):

        super().__init__()

        self.tokens = []

        self.current_link = None


    def handle_starttag(self, tag, attrs):

        attrs = dict(attrs)

        if tag == "a" and attrs.get("href"):

            self.current_link = {
                "url": attrs["href"],
                "text": []
            }


    def handle_endtag(self, tag):

        if tag == "a" and self.current_link:

            text = " ".join(
                self.current_link["text"]
            ).strip()

            if text:

                self.tokens.append({
                    "type": "link",
                    "text": text,
                    "url": self.current_link["url"],
                })

            self.current_link = None


    def handle_data(self, data):

        text = " ".join(
            data.split()
        ).strip()

        if not text:
            return


        if self.current_link:

            self.current_link["text"].append(
                text
            )

        else:

            self.tokens.append({
                "type": "text",
                "text": text,
            })


# ============================================================
# 일반 HTML 파서
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

            self.links.append(
                self.current_link
            )


    def handle_endtag(self, tag):

        if tag == "a":

            self.current_link = None


    def handle_data(self, data):

        text = " ".join(
            data.split()
        ).strip()

        if not text:
            return


        self.texts.append(text)


        if self.current_link:

            self.current_link["text"].append(
                text
            )


# ============================================================
# 웹 요청
# ============================================================

def fetch(url):

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.8",
            "Connection":
                "close",
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
# 제목 정리
# ============================================================

def clean_title(text):

    if not text:
        return ""

    text = " ".join(
        text.split()
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# 몰루로그 제목 후보 판별
# ============================================================

def is_good_mollulog_title(text):

    text = clean_title(text)

    if not text:
        return False


    # 이미지 숫자 같은 것 제거
    if text.isdigit():
        return False


    # 너무 긴 본문은 제목으로 사용하지 않음
    if len(text) > 180:
        return False


    bad_words = [

        "의견을 남겨보세요",
        "픽업 학생",
        "학생",
        "신규",
        "복각",
        "배포",
        "한정",
        "페스 신규",
        "리콜렉트",
        "이미지",

    ]


    if text in bad_words:
        return False


    return True


# ============================================================
# 몰루로그
# ============================================================

def collect_mollulog():

    source = SOURCES["mollulog"]

    html = fetch(
        source["url"]
    )


    parser = MollulogParser()

    parser.feed(html)


    tokens = parser.tokens

    events = []

    current_year = datetime.now().year


    # --------------------------------------------------------
    # 날짜가 등장하는 위치를 기준으로 주변 블록 분석
    # --------------------------------------------------------

    for index, token in enumerate(tokens):

        text = token.get(
            "text",
            ""
        )


        dates = extract_dates(
            text,
            current_year
        )


        if not dates:
            continue


        date = dates[0]


        # 현재 날짜 이전의 가까운 토큰들을 확인
        candidates = []


        start = max(
            0,
            index - 18
        )


        for previous in tokens[
            start:index
        ]:

            ptext = clean_title(
                previous.get(
                    "text",
                    ""
                )
            )


            if not ptext:
                continue


            # 링크인 경우 우선 후보
            if previous.get(
                "type"
            ) == "link":

                candidates.append(
                    (
                        2,
                        ptext,
                        previous.get(
                            "url",
                            ""
                        )
                    )
                )

            else:

                candidates.append(
                    (
                        1,
                        ptext,
                        ""
                    )
                )


        # 가장 가까운 좋은 링크/텍스트 선택
        chosen = None


        for priority, title, url in reversed(
            candidates
        ):

            if not is_good_mollulog_title(
                title
            ):
                continue


            # 날짜 자체가 제목인 경우 제외
            if extract_dates(
                title,
                current_year
            ):
                continue


            chosen = (
                title,
                url
            )

            break


        if chosen is None:
            continue


        title, url = chosen


        # ----------------------------------------------------
        # 제목이 지나치게 일반적인 경우
        # ----------------------------------------------------

        if title in (
            "캠페인",
            "이벤트",
            "복각 이벤트",
            "픽업 모집",
            "대결전",
            "총력전",
            "제약해제결전",
            "종합전술시험",
            "메인 스토리",
            "미니 스토리",
        ):

            # 바로 앞의 추가 텍스트에서 보완
            for priority, candidate, candidate_url in reversed(
                candidates
            ):

                if candidate == title:
                    continue

                if not is_good_mollulog_title(
                    candidate
                ):
                    continue

                if extract_dates(
                    candidate,
                    current_year
                ):
                    continue

                if len(candidate) > len(title):

                    title = candidate

                    if candidate_url:
                        url = candidate_url

                    break


        events.append({

            "date": date,

            "title": clean_title(
                title
            ),

            "url":
                urljoin(
                    source["url"],
                    url
                ) if url else source["url"],

            "source":
                source["name"],

        })


    # --------------------------------------------------------
    # 몰루로그 전용 보정:
    # 페이지에 실제로 표시되는 날짜들은
    # 별도로 찾아 누락 여부 확인
    # --------------------------------------------------------

    page_text = " ".join(
        token.get("text", "")
        for token in tokens
    )


    all_dates = extract_dates(
        page_text,
        current_year
    )


    known_dates = {
        event["date"]
        for event in events
    }


    # 날짜가 있는데 제목 연결에 실패한 경우
    # 해당 날짜 자체는 보존
    for date in all_dates:

        if date in known_dates:
            continue


        events.append({

            "date": date,

            "title":
                "몰루로그 미래시 일정",

            "url":
                source["url"],

            "source":
                source["name"],

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
    limit
):

    parser = SimpleParser()

    parser.feed(html)


    posts = []

    seen = set()


    for link in parser.links:

        title = clean_title(
            " ".join(
                link["text"]
            )
        )


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


    html = fetch(
        source["url"]
    )


    return parse_posts(
        html,
        source["url"],
        source["name"],
        40
    )


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

        html = fetch(url)


        return parse_posts(
            html,
            url,
            source["name"],
            30
        )


    except Exception as error:

        print(
            f"[공식 포럼 실패] "
            f"{error}"
        )

        return []


# ============================================================
# 넥슨 공식 포럼
# ============================================================

def collect_nexon_forum():

    posts = []

    seen = set()


    # 게시판 3개 동시 실행
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

                result = future.result()

            except Exception:

                result = []


            for post in result:

                url = post[
                    "url"
                ]


                if url in seen:
                    continue


                seen.add(url)

                posts.append(
                    post
                )


    return posts[:60]


# ============================================================
# 이벤트 정리
# ============================================================

def clean_events(events):

    result = []

    seen = set()


    for event in events:

        date = clean_title(
            str(
                event.get(
                    "date",
                    ""
                )
            )
        )


        title = clean_title(
            str(
                event.get(
                    "title",
                    ""
                )
            )
        )


        if not date:
            continue


        if not title:
            continue


        # 너무 이상한 날짜 제거
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
            )
        )


        if key in seen:
            continue


        seen.add(key)


        event["date"] = date

        event["title"] = title


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
            f"JSON 읽기 실패: {error}"
        )


    return default


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
        " 블루 아카이브 미래시 빠른 업데이트"
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
        {}
    )


    results = {}

    errors = {}


    # --------------------------------------------------------
    # 3사이트 동시 수집
    # --------------------------------------------------------

    collectors = {

        "mollulog":
            collect_mollulog,

        "bluearchive_gallery":
            collect_gallery,

        "nexon_forum":
            collect_nexon_forum,

    }


    print()
    print(
        "3개 출처 동시 수집 시작..."
    )


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
    # 일정 생성
    # --------------------------------------------------------

    all_events = []

    evidence = []


    for source_id in (
        "mollulog",
        "bluearchive_gallery",
        "nexon_forum"
    ):

     
