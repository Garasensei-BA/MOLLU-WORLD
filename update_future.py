#!/usr/bin/env python3

import json
import re
from datetime import datetime, date
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
    },

    "bluearchive_gallery": {
        "name": "블루 아카이브 갤러리",
        "url": (
            "https://gall.dcinside.com/"
            "mgallery/board/lists/?id=projectmx"
        ),
    },

    "nexon_forum": {
        "name": "블루 아카이브 공식 포럼",
        "url": "https://forum.nexon.com/bluearchive/",
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
    "신규",
    "메인 스토리",
    "스토리",
]


# ============================================================
# 날짜 정규식
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

KOREAN_DATE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)

MONTH_DAY_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*월\s*(\d{1,2})"
)


# ============================================================
# 날짜 추출
# ============================================================

def valid_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_dates(text, default_year=None):
    """
    여러 가지 날짜 표기를 한꺼번에 인식한다.

    지원:
    2026-10-06
    2026.10.06
    2026/10/06
    2026년 10월 6일
    10/06
    10.06
    10월 6일
    """

    if not text:
        return []

    results = set()

    # --------------------------------------------------------
    # YYYY-MM-DD / YYYY.MM.DD / YYYY년 MM월 DD일
    # --------------------------------------------------------

    for match in FULL_DATE_PATTERN.finditer(text):

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if valid_date(year, month, day):
            results.add(
                f"{year:04d}-{month:02d}-{day:02d}"
            )

    # --------------------------------------------------------
    # MM월 DD일
    # --------------------------------------------------------

    if default_year is not None:

        for match in KOREAN_DATE_PATTERN.finditer(text):

            month = int(match.group(1))
            day = int(match.group(2))

            if valid_date(default_year, month, day):
                results.add(
                    f"{default_year:04d}-"
                    f"{month:02d}-{day:02d}"
                )

    # --------------------------------------------------------
    # MM월 DD
    # --------------------------------------------------------

    if default_year is not None:

        for match in MONTH_DAY_PATTERN.finditer(text):

            month = int(match.group(1))
            day = int(match.group(2))

            if valid_date(default_year, month, day):
                results.add(
                    f"{default_year:04d}-"
                    f"{month:02d}-{day:02d}"
                )

    # --------------------------------------------------------
    # MM/DD / MM.DD
    # --------------------------------------------------------

    if default_year is not None:

        for match in SHORT_DATE_PATTERN.finditer(text):

            month = int(match.group(1))
            day = int(match.group(2))

            if valid_date(default_year, month, day):
                results.add(
                    f"{default_year:04d}-"
                    f"{month:02d}-{day:02d}"
                )

    return sorted(results)


# ============================================================
# HTML 링크/텍스트 파서
# ============================================================

class HTMLDataParser(HTMLParser):

    def __init__(self):

        super().__init__()

        self.texts = []

        self.links = []

        self.current_link = None

    def handle_starttag(self, tag, attrs):

        attrs = dict(attrs)

        if tag.lower() == "a":

            href = attrs.get("href")

            if href:

                self.current_link = {
                    "url": href,
                    "text": [],
                }

                self.links.append(
                    self.current_link
                )

    def handle_endtag(self, tag):

        if tag.lower() == "a":

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
        },
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
# 문자열 정리
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = text.replace(
        "\xa0",
        " "
    )

    text = " ".join(
        text.split()
    )

    return text.strip()


# ============================================================
# 몰루로그 제목 판별
# ============================================================

def is_bad_title(text):

    text = clean_text(text)

    if not text:
        return True

    if len(text) > 200:
        return True

    if text.isdigit():
        return True

    bad = [
        "의견을 남겨보세요",
        "로그인",
        "회원가입",
        "검색",
        "메뉴",
        "이미지",
        "더보기",
        "닫기",
    ]

    if text in bad:
        return True

    return False


# ============================================================
# 몰루로그 수집
#
# 중요:
# 페이지 전체에서 날짜를 먼저 찾는다.
#
# 기존 방식처럼 "9월까지만" 고정하는 제한을 두지 않는다.
# 페이지에 10월/11월/12월이 있으면 그대로 읽는다.
# ============================================================

def collect_mollulog():

    source = SOURCES["mollulog"]

    print("[몰루로그] 접속 중...")

    html = fetch(
        source["url"]
    )

    parser = HTMLDataParser()

    parser.feed(html)

    current_year = datetime.now().year

    tokens = []

    # --------------------------------------------------------
    # 모든 텍스트를 순서대로 보관
    # --------------------------------------------------------

    for text in parser.texts:

        text = clean_text(text)

        if text:
            tokens.append(text)

    # --------------------------------------------------------
    # 링크 정보도 별도로 보관
    # --------------------------------------------------------

    link_items = []

    for link in parser.links:

        title = clean_text(
            " ".join(
                link["text"]
            )
        )

        if not title:
            continue

        link_items.append({
            "title": title,
            "url": urljoin(
                source["url"],
                link["url"]
            ),
        })

    events = []

    # --------------------------------------------------------
    # 날짜별로 주변 텍스트를 조사
    # --------------------------------------------------------

    for index, text in enumerate(tokens):

        dates = extract_dates(
            text,
            current_year
        )

        if not dates:
            continue

        for found_date in dates:

            candidates = []

            # 날짜 앞쪽 최대 30개 토큰 조사
            start = max(
                0,
                index - 30
            )

            previous_tokens = tokens[
                start:index
            ]

            for previous in previous_tokens:

                previous = clean_text(
                    previous
                )

                if is_bad_title(
                    previous
                ):
                    continue

                if extract_dates(
                    previous,
                    current_year
                ):
                    continue

                # 너무 일반적인 텍스트는 낮은 우선순위
                score = 1

                useful_words = [
                    "이벤트",
                    "픽업",
                    "모집",
                    "총력전",
                    "대결전",
                    "시험",
                    "업데이트",
                    "스토리",
                    "페스",
                    "복각",
                    "신규",
                ]

                if any(
                    word in previous
                    for word in useful_words
                ):
                    score += 2

                candidates.append(
                    (
                        score,
                        previous
                    )
                )

            title = ""

            # 뒤에서부터 가까운 후보 선택
            for score, candidate in reversed(
                candidates
            ):

                if not is_bad_title(
                    candidate
                ):

                    title = candidate

                    # 유용한 키워드가 들어가면 바로 사용
                    if any(
                        word in candidate
                        for word in [
                            "이벤트",
                            "픽업",
                            "모집",
                            "총력전",
                            "대결전",
                            "업데이트",
                            "스토리",
                            "페스",
                            "복각",
                        ]
                    ):
                        break

            if not title:

                title = "몰루로그 미래시 일정"

            # ------------------------------------------------
            # 해당 날짜와 가장 가까운 링크 찾기
            # ------------------------------------------------

            best_url = source["url"]

            best_distance = 999999

            for link in link_items:

                link_title = link["title"]

                if not link_title:
                    continue

                # 제목이 후보와 비슷하면 연결
                if (
                    title in link_title
                    or
                    link_title in title
                ):

                    distance = abs(
                        len(link_title)
                        - len(title)
                    )

                    if distance < best_distance:

                        best_distance = distance

                        best_url = link["url"]

            events.append({
                "date": found_date,
                "title": clean_text(title),
                "url": best_url,
                "source": source["name"],
            })

    # --------------------------------------------------------
    # 중요 보정:
    # 페이지 전체에서 발견된 모든 날짜를 보존한다.
    #
    # 제목 연결에 실패해도 날짜 자체를 버리지 않는다.
    # --------------------------------------------------------

    full_text = "\n".join(tokens)

    all_dates = extract_dates(
        full_text,
        current_year
    )

    existing_dates = {
        event["date"]
        for event in events
    }

    for found_date in all_dates:

        if found_date in existing_dates:
            continue

        events.append({
            "date": found_date,
            "title": "몰루로그 미래시 일정",
            "url": source["url"],
            "source": source["name"],
        })

    # --------------------------------------------------------
    # 몰루로그에서 읽힌 날짜 출력
    # --------------------------------------------------------

    unique_dates = sorted({
        event["date"]
        for event in events
    })

    print(
        f"[몰루로그] 날짜 {len(unique_dates)}개 발견"
    )

    if unique_dates:

        print(
            f"[몰루로그] "
            f"{unique_dates[0]} ~ "
            f"{unique_dates[-1]}"
        )

    return clean_events(events)


# ============================================================
# 게시글 수집
# ============================================================

def parse_posts(
    html,
    base_url,
    source_name,
    limit=50
):

    parser = HTMLDataParser()

    parser.feed(html)

    posts = []

    seen = set()

    current_year = datetime.now().year

    for link in parser.links:

        title = clean_text(
            " ".join(
                link["text"]
            )
        )

        if not title:
            continue

        if len(title) < 2:
            continue

        # 미래시 관련 키워드 확인
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

        seen.add(url)

        dates = extract_dates(
            title,
            current_year
        )

        posts.append({
            "title": title,
            "url": url,
            "source": source_name,
            "dates": dates,
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

    print("[갤러리] 접속 중...")

    html = fetch(
        source["url"]
    )

    posts = parse_posts(
        html,
        source["url"],
        source["name"],
        60
    )

    print(
        f"[갤러리] {len(posts)}개 글 발견"
    )

    return posts


# ============================================================
# 공식 포럼 게시판 하나
# ============================================================

def collect_one_official_board(url):

    source = SOURCES[
        "nexon_forum"
    ]

    try:

        html = fetch(url)

        posts = parse_posts(
            html,
            url,
            source["name"],
            40
        )

        return posts

    except Exception as error:

        print(
            f"[공식 포럼 실패] "
            f"{url} / {error}"
        )

        return []


# ============================================================
# 넥슨 공식 포럼
# ============================================================

def collect_nexon_forum():

    print("[공식 포럼] 접속 중...")

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

                result = future.result()

            except Exception as error:

                print(
                    f"[공식 포럼 오류] "
                    f"{error}"
                )

                result = []

            for post in result:

                url = post.get(
                    "url",
                    ""
                )

                if not url:
                    continue

                if url in seen:
                    continue

                seen.add(url)

                posts.append(post)

    print(
        f"[공식 포럼] {len(posts)}개 글 발견"
    )

    return posts[:100]


# ============================================================
# 이벤트 정리
# ============================================================

def clean_events(events):

    result = []

    seen = set()

    for event in events:

        date_text = clean_text(
            str(
                event.get(
                    "date",
                    ""
                )
            )
        )

        title = clean_text(
            str(
                event.get(
                    "title",
                    ""
                )
            )
        )

        if not date_text:
            continue

        if not title:
            continue

        try:

            datetime.strptime(
                date_text,
                "%Y-%m-%d"
            )

        except ValueError:

            continue

        key = (
            date_text,
            title,
            event.get(
                "source",
                ""
            ),
        )

        if key in seen:
            continue

        seen.add(key)

        result.append({
            "date": date_text,
            "title": title,
            "url": event.get(
                "url",
                ""
            ),
            "source": event.get(
                "source",
                ""
            ),
        })

    result.sort(
        key=lambda item: (
            item.get(
                "date",
                "9999-99-99"
            ),
            item.get(
                "title",
                ""
            ),
            item.get(
                "source",
                ""
            ),
        )
    )

    return result


# ============================================================
# JSON 읽기
# ============================================================

def load_json(path, default):

    try:

        if not path.exists():
            return default

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as error:

        print(
            f"[JSON 읽기 실패] "
            f"{error}"
        )

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
        ) + "\n",
        encoding="utf-8"
    )


# ============================================================
# 기존 데이터 추출
# ============================================================

def get_old_events(old_data):

    if isinstance(
        old_data,
        dict
    ):

        events = old_data.get(
            "events",
            []
        )

        if isinstance(
            events,
            list
        ):
            return events

    if isinstance(
        old_data,
        list
    ):
        return old_data

    return []


# ============================================================
# 출처 비교용 evidence 생성
# ============================================================

def make_evidence(results):

    evidence = []

    for source_id, items in results.items():

        source = SOURCES.get(
            source_id,
            {}
        )

        source_name = source.get(
            "name",
            source_id
        )

        if not isinstance(
            items,
            list
        ):
            continue

        for item in items:

            if not isinstance(
                item,
                dict
            ):
                continue

            dates = item.get(
                "dates",
                []
            )

            # 몰루로그는 date 하나로 저장되어 있음
            if not dates and item.get(
                "date"
            ):
                dates = [
                    item["date"]
                ]

            evidence.append({
                "source": source_name,
                "title": clean_text(
                    str(
                        item.get(
                            "title",
                            ""
                        )
                    )
                ),
                "url": item.get(
                    "url",
                    ""
                ),
                "dates": dates,
            })

    return evidence


# ============================================================
# 일정 병합
# ============================================================

def merge_events(
    old_events,
    new_events
):

    combined = []

    # 기존 데이터도 유지
    combined.extend(
        old_events
    )

    # 새 데이터 추가
    combined.extend(
        new_events
    )

    return clean_events(
        combined
    )


# ============================================================
# 미래 일정만 추출
# ============================================================

def filter_future_events(events):

    today = datetime.now().date()

    result = []

    for event in events:

        try:

            event_date = datetime.strptime(
                event["date"],
                "%Y-%m-%d"
            ).date()

        except Exception:

            continue

        # 오늘 이후 일정
        if event_date >= today:

            result.append(
                event
            )

    return result


# ============================================================
# 출처별 통계
# ============================================================

def make_source_counts(events):

    counts = {}

    for event in events:

        source = event.get(
            "source",
            "알 수 없음"
        )

        counts[source] = (
            counts.get(
                source,
                0
            ) + 1
        )

    return counts


# ============================================================
# 가장 먼 미래 일정
# ============================================================

def get_latest_date(events):

    dates = []

    for event in events:

        value = event.get(
            "date",
            ""
        )

        if value:
            dates.append(value)

    if not dates:
        return None

    return max(dates)


# ============================================================
# 메인
# ============================================================

def main():

    print()
    print(
        "=========================================="
    )
    print(
        " 블루 아카이브 미래시 자동 업데이트"
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

    # --------------------------------------------------------
    # 기존 데이터
    # --------------------------------------------------------

    old_events = get_old_events(
        old_data
    )

    print(
        f"[기존 데이터] "
        f"{len(old_events)}개 일정"
    )

    print()

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

    # --------------------------------------------------------
    # 3사이트 동시 수집
    # --------------------------------------------------------

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
            ): source_id

            for source_id, function
            in collectors.items()
        }

        for future in as_completed(
            futures
        ):

            source_id = futures[
                future
            ]

            source_name = SOURCES[
                source_id
            ]["name"]

            try:

                result = future.result()

                if not isinstance(
                    result,
                    list
                ):
                    result = []

                results[
                    source_id
                ] = result

                print(
                    f"[완료] "
                    f"{source_name}: "
                    f"{len(result)}개"
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
                    f"{source_name}: "
                    f"{error}"
                )

    print()

    # --------------------------------------------------------
    # 새 일정 생성
    # --------------------------------------------------------

    new_events = []

    for source_id, items in results.items():

        if source_id == "mollulog":

            for item in items:

                if not item.get(
                    "date"
                ):
                    continue

                new_events.append({
                    "date": item.get(
                        "date",
                        ""
                    ),
                    "title": item.get(
                        "title",
                        ""
                    ),
                    "url": item.get(
                        "url",
                        ""
                    ),
                    "source": item.get(
                        "source",
                        SOURCES[source_id]["name"]
                    ),
                })

        else:

            for item in items:

                dates = item.get(
                    "dates",
                    []
                )

                if not isinstance(
                    dates,
                    list
                ):
                    continue

                for found_date in dates:

                    new_events.append({
                        "date": found_date,
                        "title": item.get(
                            "title",
                            ""
                        ),
                        "url": item.get(
                            "url",
                            ""
                        ),
                        "source": item.get(
                            "source",
                            SOURCES[source_id]["name"]
                        ),
                    })

    # --------------------------------------------------------
    # 기존 + 새 데이터
    # --------------------------------------------------------

    combined_events = merge_events(
        old_events,
        new_events
    )

    print(
        f"[병합] "
        f"{len(combined_events)}개 일정"
    )

    # --------------------------------------------------------
    # 미래 일정만 유지
    # --------------------------------------------------------

    future_events = filter_future_events(
        combined_events
    )

    # --------------------------------------------------------
    # evidence
    # --------------------------------------------------------

    evidence = make_evidence(
        results
    )

    # --------------------------------------------------------
    # 출처별 통계
    # --------------------------------------------------------

    source_counts = make_source_counts(
        future_events
    )

    # --------------------------------------------------------
    # 가장 먼 일정
    # --------------------------------------------------------

    latest = get_latest_date(
        future_events
    )

    # --------------------------------------------------------
    # 최종 JSON
    # --------------------------------------------------------

    output = {
        "updated_at": now,

        "sources": {
            source_id: {
                "name": source["name"],
                "url": source["url"],
            }
            for source_id, source
            in SOURCES.items()
        },

        "events": future_events,

        "evidence": evidence,

        "source_counts": source_counts,

        "errors": errors,

        "stats": {
            "total_events":
                len(future_events),

            "evidence_count":
                len(evidence),

            "latest_date":
                latest,

            "updated_at":
                now,
        },
    }

    # --------------------------------------------------------
    # 저장
    # --------------------------------------------------------

    save_json(
        OUTPUT_FILE,
        output
    )

    # --------------------------------------------------------
    # 결과 출력
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

    print(
        f"미래 일정: "
        f"{len(future_events)}개"
    )

    print(
        f"출처 자료: "
        f"{len(evidence)}개"
    )

    print(
        f"가장 먼 일정: "
        f"{latest or '없음'}"
    )

    print()

    print(
        "출처별 일정:"
    )

    for source, count in sorted(
        source_counts.items()
    ):

        print(
            f"  {source}: "
            f"{count}개"
        )

    print()

    if latest:

        print(
            "가장 뒤쪽 일정:"
        )

        for event in future_events[-10:]:

            print(
                f"  "
                f"{event['date']} | "
                f"{event['title']} | "
                f"{event['source']}"
            )

    print()

    if errors:

        print(
            "일부 출처에서 오류가 발생함:"
        )

        for source, error in errors.items():

            print(
                f"  {source}: {error}"
            )

    else:

        print(
            "모든 출처 수집 완료"
        )

    print()

    print(
        f"저장 위치: {OUTPUT_FILE}"
    )

    print()


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    main()
