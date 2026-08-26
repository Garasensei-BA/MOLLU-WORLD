#!/usr/bin/env python3

# ============================================================
# 블루 아카이브 미래시 통합 자동 수집기
#
# 수집 대상
# 1. 몰루로그
# 2. 블루 아카이브 마이너 갤러리
# 3. 넥슨 블루 아카이브 공식 포럼
#
# 기능
# - HTML 수집
# - 본문 수집
# - 이미지 다운로드
# - 한국어 OCR
# - 날짜 추출
# - 이벤트/레이드/모집 분류
# - 출처 비교
# - 신뢰도 계산
# - 충돌 감지
# - 기존 데이터 보호
# - GitHub Actions 자동 실행 대응
# ============================================================


import json
import re
import sys
import time
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from html import unescape


# ============================================================
# 경로
# ============================================================

ROOT = Path(__file__).resolve().parent

OUTPUT_FILE = ROOT / "future-data.json"
CONFIG_FILE = ROOT / "future-sources.json"

CACHE_DIR = ROOT / ".future-cache"
IMAGE_DIR = CACHE_DIR / "images"
HTML_DIR = CACHE_DIR / "html"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
HTML_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 기본 설정
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "Chrome/131.0 Safari/537.36"
)

TIMEOUT = 30

MAX_RETRIES = 3

RETRY_WAIT = 2

MAX_IMAGE_SIZE = 15 * 1024 * 1024

MAX_IMAGES_PER_POST = 12

MAX_GALLERY_PAGES = 5

MAX_GALLERY_POSTS = 40

MAX_OFFICIAL_POSTS = 60

CACHE_HOURS = 6


# ============================================================
# 출처
# ============================================================

SOURCES = {
    "mollulog": {
        "name": "몰루로그",
        "url": "https://mollulog.net/futures",
        "type": "future",
        "trust": 0.90,
    },

    "bluearchive_gallery": {
        "name": "블루 아카이브 갤러리",
        "url": (
            "https://gall.dcinside.com/"
            "mgallery/board/lists/?id=projectmx"
        ),
        "type": "community",
        "trust": 0.65,
    },

    "nexon_forum": {
        "name": "블루 아카이브 공식 포럼",
        "url": "https://forum.nexon.com/bluearchive/",
        "type": "official",
        "trust": 1.00,
    },
}


# ============================================================
# 관련 키워드
# ============================================================

KEYWORDS = [
    "미래시",
    "픽업",
    "모집",
    "이벤트",
    "복각",
    "상설화",
    "신규",
    "한정",
    "배포",
    "메인 스토리",
    "미니 스토리",
    "캠페인",
    "총력전",
    "대결전",
    "제약해제결전",
    "종합전술시험",
    "연합작전",
    "업데이트",
    "점검",
    "로드맵",
    "공식 방송",
]


CONTENT_TYPES = {
    "event": [
        "이벤트",
        "복각",
        "상설화",
        "미니 이벤트",
    ],

    "story": [
        "메인 스토리",
        "미니 스토리",
        "스토리",
    ],

    "recruitment": [
        "픽업",
        "모집",
        "페스",
        "한정",
    ],

    "raid": [
        "총력전",
        "대결전",
        "제약해제결전",
        "연합작전",
    ],

    "test": [
        "종합전술시험",
    ],

    "campaign": [
        "캠페인",
    ],

    "update": [
        "업데이트",
        "점검",
        "로드맵",
    ],
}


# ============================================================
# 불필요한 문자열
# ============================================================

NOISE_PHRASES = [
    "window.__reactRouterContext",
    "window.__reactRouterManifest",
    "window.__reactRouterRouteModules",
    "sessionStorage.getItem",
    "react-router-scroll-positions",
    "import * as route",
    "ReadableStream",
    "TextEncoderStream",
    "console.error",
    "sessionStorage.removeItem",
    "컨텐츠 필터",
    "몰루 로그 게임 <블루 아카이브>",
    "GitHub",
    "개인정보처리방침",
    "이용약관",
]


# ============================================================
# 패키지 준비
# ============================================================

def install_package(package, import_name=None):

    if import_name is None:
        import_name = package

    try:
        __import__(import_name)
        return True

    except ImportError:

        print(
            f"[준비] {package} 설치 중..."
        )

        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            package,
        ])

        return True


install_package(
    "beautifulsoup4",
    "bs4"
)

install_package(
    "Pillow",
    "PIL"
)

install_package(
    "pytesseract"
)

from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageFilter
import pytesseract


# ============================================================
# Tesseract
# ============================================================

def ensure_tesseract():

    if shutil.which("tesseract"):
        return True

    print(
        "[준비] Tesseract OCR 설치 중..."
    )

    try:

        subprocess.run(
            [
                "sudo",
                "apt-get",
                "update",
                "-qq",
            ],
            check=True,
            timeout=180,
        )

        subprocess.run(
            [
                "sudo",
                "apt-get",
                "install",
                "-y",
                "-qq",
                "tesseract-ocr",
                "tesseract-ocr-kor",
            ],
            check=True,
            timeout=180,
        )

        return shutil.which(
            "tesseract"
        ) is not None

    except Exception as error:

        print(
            "[경고] Tesseract 설치 실패:",
            error,
        )

        return False


OCR_AVAILABLE = ensure_tesseract()


# ============================================================
# 현재 시간
# ============================================================

def now_iso():

    return (
        datetime.now()
        .astimezone()
        .isoformat(
            timespec="seconds"
        )
    )


# ============================================================
# 문자열 정리
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = unescape(
        text
    )

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def remove_noise(text):

    if not text:
        return ""

    text = clean_text(
        text
    )

    for phrase in NOISE_PHRASES:

        index = text.find(
            phrase
        )

        if index >= 0:

            text = text[:index]

    return clean_text(
        text
    )


# ============================================================
# HTTP
# ============================================================

def request_bytes(
    url,
    referer=None,
    use_cache=True,
):

    digest = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()

    cache_file = (
        HTML_DIR
        / f"{digest}.bin"
    )

    if use_cache and cache_file.exists():

        age = (
            time.time()
            - cache_file.stat().st_mtime
        )

        if age < CACHE_HOURS * 3600:

            try:
                return cache_file.read_bytes()
            except Exception:
                pass

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": (
            "ko-KR,ko;q=0.9,en;q=0.8"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/*,*/*;q=0.8"
        ),
    }

    if referer:
        headers["Referer"] = referer

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            request = Request(
                url,
                headers=headers,
            )

            with urlopen(
                request,
                timeout=TIMEOUT,
            ) as response:

                data = response.read()

            if len(data) > MAX_IMAGE_SIZE:

                raise ValueError(
                    "응답 파일이 너무 큽니다."
                )

            try:

                cache_file.write_bytes(
                    data
                )

            except Exception:
                pass

            return data

        except Exception as error:

            last_error = error

            print(
                f"[HTTP 재시도 "
                f"{attempt}/{MAX_RETRIES}] "
                f"{url}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_WAIT * attempt
                )

    raise last_error


def request_html(
    url,
    referer=None,
):

    data = request_bytes(
        url,
        referer=referer,
    )

    # BOM 제거
    if data.startswith(
        b"\xef\xbb\xbf"
    ):
        data = data[3:]

    return data.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# HTML 정리
# ============================================================

def parse_html(html):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # JS / CSS / 템플릿 제거
    for tag in soup([
        "script",
        "style",
        "noscript",
        "template",
        "svg",
    ]):

        tag.decompose()

    return soup


def page_text(soup):

    text = " ".join(
        soup.stripped_strings
    )

    return remove_noise(
        text
    )


# ============================================================
# 날짜
# ============================================================

FULL_DATE_PATTERNS = [

    re.compile(
        r"(20\d{2})"
        r"\s*[./\-년]\s*"
        r"(\d{1,2})"
        r"\s*[./\-월]\s*"
        r"(\d{1,2})"
        r"\s*(?:일)?"
    ),

    re.compile(
        r"(20\d{2})"
        r"\s*년\s*"
        r"(\d{1,2})"
        r"\s*월\s*"
        r"(\d{1,2})"
        r"\s*일"
    ),
]


SHORT_DATE_PATTERNS = [

    re.compile(
        r"(?<!\d)"
        r"(\d{1,2})"
        r"\s*[./\-]"
        r"(\d{1,2})"
        r"(?!\d)"
    ),

    re.compile(
        r"(?<!\d)"
        r"(\d{1,2})"
        r"\s*월\s*"
        r"(\d{1,2})"
        r"\s*일"
    ),
]


def valid_date(
    year,
    month,
    day,
):

    try:

        return datetime(
            year,
            month,
            day,
        )

    except ValueError:

        return None


def extract_dates(
    text,
    default_year=None,
):

    if not text:
        return []

    text = clean_text(
        text
    )

    results = []

    # --------------------------------------------------------
    # 연도 포함 날짜
    # --------------------------------------------------------

    for pattern in FULL_DATE_PATTERNS:

        for match in pattern.finditer(
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

            date = valid_date(
                year,
                month,
                day,
            )

            if date:

                results.append(
                    date.strftime(
                        "%Y-%m-%d"
                    )
                )

    # --------------------------------------------------------
    # 연도 없는 날짜
    # --------------------------------------------------------

    if default_year is None:

        default_year = (
            datetime.now()
            .year
        )

    for pattern in SHORT_DATE_PATTERNS:

        for match in pattern.finditer(
            text
        ):

            month = int(
                match.group(1)
            )

            day = int(
                match.group(2)
            )

            # 현재 연도
            date = valid_date(
                default_year,
                month,
                day,
            )

            if not date:
                continue

            # 현재 날짜보다 지나치게 과거면
            # 다음 해로도 한번 확인
            if (
                date.date()
                < datetime.now().date()
                - timedelta(days=120)
            ):

                next_date = valid_date(
                    default_year + 1,
                    month,
                    day,
                )

                if next_date:
                    date = next_date

            results.append(
                date.strftime(
                    "%Y-%m-%d"
                )
            )

    return sorted(
        set(results)
    )


# ============================================================
# 기간 추출
# ============================================================

def extract_date_ranges(text):

    if not text:
        return []

    dates = extract_dates(
        text
    )

    if len(dates) < 2:
        return []

    ranges = []

    for index in range(
        len(dates) - 1
    ):

        start = dates[index]
        end = dates[index + 1]

        try:

            start_dt = datetime.strptime(
                start,
                "%Y-%m-%d",
            )

            end_dt = datetime.strptime(
                end,
                "%Y-%m-%d",
            )

            days = (
                end_dt - start_dt
            ).days

            # 가까운 날짜끼리만 기간 후보로 판단
            if 0 < days <= 60:

                ranges.append({
                    "startDate": start,
                    "endDate": end,
                })

        except Exception:
            pass

    return ranges


# ============================================================
# 콘텐츠 분류
# ============================================================

def classify_content(text):

    text = text or ""

    scores = {}

    for content_type, keywords in CONTENT_TYPES.items():

        score = sum(
            1
            for keyword in keywords
            if keyword in text
        )

        if score:
            scores[content_type] = score

    if not scores:
        return "other"

    return max(
        scores,
        key=scores.get
    )


# ============================================================
# 캐릭터 이름 추출
# ============================================================

STUDENT_NAMES = [
    "니코",
    "쿠루미",
    "오토기",
    "에리카",
    "키라라",
    "츠바키",
    "우미카",
    "피나",
    "무츠키",
    "하루카",
    "아루",
    "카요코",
    "사오리",
    "나구사",
    "유카리",
    "키쿄",
    "렌게",
    "카즈사",
    "요시미",
    "나츠",
    "아이리",
    "키사키",
    "슌",
    "히카리",
    "노조미",
    "아오바",
    "이부키",
    "마코토",
    "아코",
    "사츠키",
    "치아키",
    "칸나",
    "코코로",
    "코토네",
    "리오",
    "호시노",
    "시로코",
    "미카",
    "하나코",
    "히나",
    "네루",
    "나기사",
    "아리스",
    "케이",
    "와카모",
    "세나",
    "주리",
    "니야",
    "스미레",
    "레이",
    "아카리",
    "이즈미",
]


def extract_students(text):

    found = []

    for name in STUDENT_NAMES:

        if name in text:
            found.append(name)

    return sorted(
        set(found)
    )


# ============================================================
# 이미지
# ============================================================

def image_path_for(url):

    digest = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:20]

    extension = Path(
        urlparse(url).path
    ).suffix.lower()

    if extension not in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
    ]:

        extension = ".jpg"

    return (
        IMAGE_DIR
        / f"{digest}{extension}"
    )


def download_image(
    url,
    referer=None,
):

    target = image_path_for(
        url
    )

    if target.exists():

        return target

    try:

        data = request_bytes(
            url,
            referer=referer,
            use_cache=False,
        )

        target.write_bytes(
            data
        )

        return target

    except Exception as error:

        print(
            "[이미지 다운로드 실패]",
            url,
            error,
        )

        return None


# ============================================================
# 이미지 OCR
# ============================================================

def preprocess_image(
    image_path
):

    image = Image.open(
        image_path
    )

    try:
        image.seek(0)
    except Exception:
        pass

    image = image.convert(
        "RGB"
    )

    width, height = image.size

    # 너무 작은 이미지는 확대
    if width < 1600:

        scale = (
            1600 / width
        )

        image = image.resize(
            (
                int(width * scale),
                int(height * scale),
            )
        )

    image = ImageOps.grayscale(
        image
    )

    image = ImageOps.autocontrast(
        image
    )

    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


def ocr_image(
    image_path
):

    if not OCR_AVAILABLE:
        return ""

    try:

        image = preprocess_image(
            image_path
        )

        # 1차 OCR
        text1 = pytesseract.image_to_string(
            image,
            lang="kor+eng",
            config="--psm 6",
        )

        # 2차 OCR
        text2 = pytesseract.image_to_string(
            image,
            lang="kor+eng",
            config="--psm 11",
        )

        text = (
            text1
            + " "
            + text2
        )

        return clean_text(
            text
        )[:10000]

    except Exception as error:

        print(
            "[OCR 실패]",
            image_path,
            error,
        )

        return ""


# ============================================================
# 페이지 이미지 수집
# ============================================================

def extract_image_urls(
    soup,
    page_url,
):

    urls = []

    for image in soup.find_all(
        "img"
    ):

        candidates = [
            image.get("src"),
            image.get("data-src"),
            image.get("data-original"),
            image.get("data-lazy-src"),
            image.get("data-url"),
        ]

        for value in candidates:

            if not value:
                continue

            value = value.strip()

            if value.startswith(
                "data:"
            ):
                continue

            absolute = urljoin(
                page_url,
                value,
            )

            if absolute not in urls:

                urls.append(
                    absolute
                )

            break

    return urls


def analyze_images(
    image_urls,
    page_url,
):

    results = []

    for image_url in image_urls[
        :MAX_IMAGES_PER_POST
    ]:

        path = download_image(
            image_url,
            referer=page_url,
        )

        if not path:
            continue

        ocr = ocr_image(
            path
        )

        if not ocr:
            continue

        dates = extract_dates(
            ocr
        )

        students = extract_students(
            ocr
        )

        if (
            dates
            or students
            or any(
                keyword in ocr
                for keyword in KEYWORDS
            )
        ):

            results.append({
                "url": image_url,
                "file": str(
                    path.relative_to(
                        ROOT
                    )
                ),
                "ocr": ocr,
                "dates": dates,
                "students": students,
            })

    return results


# ============================================================
# 몰루로그
# ============================================================

def collect_mollulog():

    url = SOURCES[
        "mollulog"
    ]["url"]

    html = request_html(
        url
    )

    soup = parse_html(
        html
    )

    # body의 직접적인 텍스트 흐름을 사용
    strings = []

    for text in soup.stripped_strings:

        text = clean_text(
            text
        )

        if not text:
            continue

        if any(
            noise in text
            for noise in NOISE_PHRASES
        ):
            continue

        strings.append(
            text
        )

    events = []

    current_date = None

    buffer = []

    for text in strings:

        dates = extract_dates(
            text
        )

        # 날짜가 발견되면 이전 블록 종료
        if dates:

            if (
                current_date
                and buffer
            ):

                title = clean_event_title(
                    " ".join(buffer)
                )

                if is_valid_event_title(
                    title
                ):

                    events.append(
                        make_event(
                            date=current_date,
                            title=title,
                            url=url,
                            source_id="mollulog",
                            source_name="몰루로그",
                            source_type="future",
                            raw_text=title,
                        )
                    )

            current_date = dates[0]
            buffer = []

            continue

        if current_date:

            buffer.append(
                text
            )

    # 마지막 블록
    if (
        current_date
        and buffer
    ):

        title = clean_event_title(
            " ".join(buffer)
        )

        if is_valid_event_title(
            title
        ):

            events.append(
                make_event(
                    date=current_date,
                    title=title,
                    url=url,
                    source_id="mollulog",
                    source_name="몰루로그",
                    source_type="future",
                    raw_text=title,
                )
            )

    # 몰루로그 이미지도 보조 수집
    image_urls = extract_image_urls(
        soup,
        url
    )

    image_evidence = analyze_images(
        image_urls,
        url
    )

    return {
        "events": clean_events(
            events
        ),
        "posts": [],
        "images": image_evidence,
    }


def clean_event_title(
    title
):

    title = clean_text(
        title
    )

    # 사이트 하단으로 넘어간 경우 자르기
    stop_words = [
        "컨텐츠 필터",
        "몰루 로그 게임",
        "GitHub",
        "window.",
        "sessionStorage",
        "react-router",
    ]

    for stop in stop_words:

        index = title.find(
            stop
        )

        if index >= 0:

            title = title[
                :index
            ]

    return clean_text(
        title
    )


def is_valid_event_title(
    title
):

    if not title:
        return False

    if len(title) < 3:
        return False

    # JS 코드 제거
    if (
        "window." in title
        or "sessionStorage" in title
        or "react-router" in title
    ):
        return False

    # 실제 미래시 관련성이 있어야 함
    if not any(
        keyword in title
        for keyword in KEYWORDS
    ):
        return False

    return True


# ============================================================
# 갤러리
# ============================================================

def gallery_list_url(
    page
):

    return (
        "https://gall.dcinside.com/"
        "mgallery/board/lists/"
        "?id=projectmx"
        f"&page={page}"
    )


def get_gallery_posts():

    posts = []

    seen = set()

    keywords = [
        "미래시",
        "일섭정보",
        "일섭",
        "진행 예정",
        "진행중",
        "픽업",
        "총력",
        "대결",
        "제결",
        "종전시",
        "이벤트",
        "업데이트",
    ]

    for page in range(
        1,
        MAX_GALLERY_PAGES + 1
    ):

        url = gallery_list_url(
            page
        )

        try:

            html = request_html(
                url
            )

            soup = parse_html(
                html
            )

            for link in soup.find_all(
                "a",
                href=True,
            ):

                title = clean_text(
                    link.get_text(
                        " ",
                        strip=True
                    )
                )

                href = link[
                    "href"
                ]

                if not title:
                    continue

                if not any(
                    keyword in title
                    for keyword in keywords
                ):
                    continue

                post_url = urljoin(
                    url,
                    href,
                )

                parsed = urlparse(
                    post_url
                )

                if (
                    "gall.dcinside.com"
                    not in parsed.netloc
                ):
                    continue

                if (
                    "board/view"
                    not in post_url
                ):
                    continue

                if post_url in seen:
                    continue

                seen.add(
                    post_url
                )

                posts.append({
                    "title": title,
                    "url": post_url,
                })

        except Exception as error:

            print(
                "[갤러리 목록 실패]",
                error,
            )

    return posts[
        :MAX_GALLERY_POSTS
    ]


def get_gallery_content(
    post
):

    url = post["url"]

    html = request_html(
        url
    )

    soup = parse_html(
        html
    )

    # 디시 본문 후보
    candidates = [
        ".writing_view_box",
        ".write_div",
        ".gallview_contents",
        ".inner",
    ]

    content = None

    for selector in candidates:

        found = soup.select_one(
            selector
        )

        if found:

            text = clean_text(
                found.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) > 20:

                content = found

                break

    if content is None:
        content = soup.body

    if content is None:
        return {
            "text": "",
            "images": [],
        }

    text = clean_text(
        content.get_text(
            " ",
            strip=True
        )
    )

    text = remove_noise(
        text
    )

    image_urls = extract_image_urls(
        content,
        url
    )

    images = analyze_images(
        image_urls,
        url
    )

    all_dates = extract_dates(
        post["title"]
        + " "
        + text
    )

    for image in images:

        all_dates.extend(
            image.get(
                "dates",
                []
            )
        )

    return {
        "text": text[:10000],
        "images": images,
        "dates": sorted(
            set(all_dates)
        ),
    }


def collect_gallery():

    posts = get_gallery_posts()

    results = []

    events = []

    for index, post in enumerate(
        posts,
        start=1
    ):

        print(
            f"  [갤러리 "
            f"{index}/{len(posts)}] "
            f"{post['title'][:60]}"
        )

        try:

            content = get_gallery_content(
                post
            )

            item = {
                "title": post["title"],
                "url": post["url"],
                "source": "블루 아카이브 갤러리",
                "sourceId": "bluearchive_gallery",
                "type": "community",
                "text": content[
                    "text"
                ],
                "dates": content[
                    "dates"
                ],
                "images": content[
                    "images"
                ],
            }

            results.append(
                item
            )

            for date in content[
                "dates"
            ]:

                combined = (
                    post["title"]
                    + " "
                    + content["text"]
                )

                events.append(
                    make_event(
                        date=date,
                        title=clean_event_title(
                            post["title"]
                        ),
                        url=post["url"],
                        source_id="bluearchive_gallery",
                        source_name="블루 아카이브 갤러리",
                        source_type="community",
                        raw_text=combined,
                        images=content[
                            "images"
                        ],
                    )
                )

        except Exception as error:

            print(
                "  [갤러리 게시글 실패]",
                error,
            )

    return {
        "events": clean_events(
            events
        ),
        "posts": results,
        "images": [],
    }


# ============================================================
# 공식 포럼
# ============================================================

OFFICIAL_BOARD_HINTS = [
    "공지사항",
    "업데이트",
    "개발자 편지",
    "진행 이벤트",
    "종료 이벤트",
]


def discover_official_boards():

    homepage = SOURCES[
        "nexon_forum"
    ]["url"]

    html = request_html(
        homepage
    )

    soup = parse_html(
        html
    )

    boards = []

    # 페이지 안의 board_list 링크 자동 발견
    for link in soup.find_all(
        "a",
        href=True,
    ):

        text = clean_text(
            link.get_text(
                " ",
                strip=True
            )
        )

        href = link[
            "href"
        ]

        if not text:
            continue

        if "board_list" not in href:
            continue

        if not any(
            hint in text
            for hint in OFFICIAL_BOARD_HINTS
        ):
            continue

        url = urljoin(
            homepage,
            href,
        )

        if url not in boards:

            boards.append(
                url
            )

    # 현재 확인된 주요 게시판도 보조로 추가
    fallback_boards = [
        "https://forum.nexon.com/bluearchive/board_list?board=1018",
        "https://forum.nexon.com/bluearchive/board_list?board=1039",
        "https://forum.nexon.com/bluearchive/board_list?board=1053",
    ]

    for url in fallback_boards:

        if url not in boards:

            boards.append(
                url
            )

    return boards


def get_official_posts():

    boards = discover_official_boards()

    posts = []

    seen = set()

    for board_url in boards:

        try:

            html = request_html(
                board_url
            )

            soup = parse_html(
                html
            )

            for link in soup.find_all(
                "a",
                href=True,
            ):

                title = clean_text(
                    link.get_text(
                        " ",
                        strip=True
                    )
                )

                href = link[
                    "href"
                ]

                if not title:
                    continue

                post_url = urljoin(
                    board_url,
                    href,
                )

                if (
                    "board_view"
                    not in post_url
                ):
                    continue

                if post_url in seen:
                    continue

                # 미래시와 관련 가능성이 높은 글
                if not (
                    contains_relevant_title(
                        title
                    )
                ):
                    continue

                seen.add(
                    post_url
                )

                posts.append({
                    "title": title,
                    "url": post_url,
                })

        except Exception as error:

            print(
                "[공식 게시판 실패]",
                board_url,
                error,
            )

    return posts[
        :MAX_OFFICIAL_POSTS
    ]


def contains_relevant_title(
    title
):

    keywords = [
        "업데이트",
        "이벤트",
        "모집",
        "픽업",
        "로드맵",
        "총력전",
        "대결전",
        "제약해제결전",
        "종합전술시험",
        "메인 스토리",
        "점검",
        "4.5주년",
        "5주년",
        "신규",
    ]

    return any(
        keyword in title
        for keyword in keywords
    )


def get_official_content(
    post
):

    url = post["url"]

    html = request_html(
        url
    )

    soup = parse_html(
        html
    )

    # 공식 포럼 본문 후보
    candidates = [
        ".board_view",
        ".boardView",
        ".view_content",
        ".view_cont",
        ".article",
        ".content",
    ]

    content = None

    for selector in candidates:

        found = soup.select_one(
            selector
        )

        if found:

            text = clean_text(
                found.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) > 30:

                content = found

                break

    if content is None:
        content = soup.body

    if content is None:

        return {
            "text": "",
            "dates": [],
            "images": [],
        }

    text = clean_text(
        content.get_text(
            " ",
            strip=True
        )
    )

    text = remove_noise(
        text
    )

    image_urls = extract_image_urls(
        content,
        url
    )

    images = analyze_images(
        image_urls,
        url
    )

    all_dates = extract_dates(
        post["title"]
        + " "
        + text
    )

    for image in images:

        all_dates.extend(
            image.get(
                "dates",
                []
            )
        )

    return {
        "text": text[:12000],
        "dates": sorted(
            set(all_dates)
        ),
        "images": images,
    }


def collect_official():

    posts = get_official_posts()

    results = []

    events = []

    for index, post in enumerate(
        posts,
        start=1
    ):

        print(
            f"  [공식 "
            f"{index}/{len(posts)}] "
            f"{post['title'][:60]}"
        )

        try:

            content = get_official_content(
                post
            )

            item = {
                "title": post["title"],
                "url": post["url"],
                "source": "블루 아카이브 공식 포럼",
                "sourceId": "nexon_forum",
                "type": "official",
                "text": content[
                    "text"
                ],
                "dates": content[
                    "dates"
                ],
                "images": content[
                    "images"
                ],
            }

            results.append(
                item
            )

            for date in content[
                "dates"
            ]:

                combined = (
                    post["title"]
                    + " "
                    + content["text"]
                )

                events.append(
                    make_event(
                        date=date,
                        title=clean_event_title(
                            post["title"]
                        ),
                        url=post["url"],
                        source_id="nexon_forum",
                        source_name="블루 아카이브 공식 포럼",
                        source_type="official",
                        raw_text=combined,
                        images=content[
                            "images"
                        ],
                    )
                )

        except Exception as error:

            print(
                "  [공식 게시글 실패]",
                error,
            )

    return {
        "events": clean_events(
            events
        ),
        "posts": results,
        "images": [],
    }


# ============================================================
# 이벤트 객체
# ============================================================

def make_event(
    date,
    title,
    url,
    source_id,
    source_name,
    source_type,
    raw_text="",
    images=None,
):

    raw_text = raw_text or title

    return {
        "date": date,
        "title": clean_event_title(
            title
        ),
        "url": url,
        "source": source_name,
        "sourceId": source_id,
        "sourceType": source_type,
        "contentType": classify_content(
            raw_text
        ),
        "students": extract_students(
            raw_text
        ),
        "confidence": SOURCES[
            source_id
        ]["trust"],
        "images": images or [],
    }


# ============================================================
# 중복 제거
# ============================================================

def normalize_title(
    text
):

    text = text or ""

    text = text.lower()

    text = re.sub(
        r"[^0-9a-z가-힣]+",
        " ",
        text
    )

    return " ".join(
        text.split()
    )


def title_tokens(
    text
):

    normalized = normalize_title(
        text
    )

    return set(
        token
        for token in normalized.split()
        if len(token) >= 2
    )


def similarity(
    a,
    b
):

    a_tokens = title_tokens(
        a
    )

    b_tokens = title_tokens(
        b
    )

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(
        a_tokens & b_tokens
    )

    union = len(
        a_tokens | b_tokens
    )

    if union == 0:
        return 0.0

    return (
        intersection
        / union
    )


def clean_events(
    events
):

    result = []

    seen = set()

    for event in events:

        date = event.get(
            "date"
        )

        title = clean_event_title(
            event.get(
                "title",
                ""
            )
        )

        if not date:
            continue

        if not is_valid_event_title(
            title
        ):
            continue

        key = (
            date,
            normalize_title(title),
            event.get(
                "sourceId"
            ),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        event["title"] = title

        result.append(
            event
        )

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
        )
    )

    return result


# ============================================================
# 출처 비교
# ============================================================

def same_event(
    a,
    b
):

    if a["date"] != b["date"]:
        return False

    # 학생 정보가 겹치면 강한 증거
    students_a = set(
        a.get(
            "students",
            []
        )
    )

    students_b = set(
        b.get(
            "students",
            []
        )
    )

    if (
        students_a
        and students_b
        and students_a & students_b
    ):

        return True

    # 콘텐츠 종류가 같고 제목 유사
    if (
        a.get(
            "contentType"
        )
        == b.get(
            "contentType"
        )
    ):

        if similarity(
            a["title"],
            b["title"]
        ) >= 0.20:

            return True

    # 제목 자체가 유사
    if similarity(
        a["title"],
        b["title"]
    ) >= 0.35:

        return True

    return False


def merge_events(
    events
):

    groups = []

    for event in events:

        placed = False

        for group in groups:

            for existing in group:

                if same_event(
                    event,
                    existing
                ):

                    group.append(
                        event
                    )

                    placed = True

                    break

            if placed:
                break

        if not placed:

            groups.append(
                [event]
            )

    merged = []

    for group in groups:

        source_ids = sorted(
            set(
                item[
                    "sourceId"
                ]
                for item in group
            )
        )

        source_count = len(
            source_ids
        )

        # 공식 최우선
        if "nexon_forum" in source_ids:

            status = "official"

            confidence = 1.00

        elif (
            "mollulog" in source_ids
            and "bluearchive_gallery"
            in source_ids
        ):

            status = (
                "confirmed_by_multiple_sources"
            )

            confidence = 0.90

        elif "mollulog" in source_ids:

            status = "estimated"

            confidence = 0.82

        elif "bluearchive_gallery" in source_ids:

            status = "community"

            confidence = 0.65

        else:

            status = "unknown"

            confidence = 0.50

        # 서로 다른 날짜는 여기서는 같은 그룹이 아니므로
        # 날짜 충돌은 별도 conflict 함수에서 처리

        # 가장 신뢰도 높은 출처를 대표로 사용
        representative = max(
            group,
            key=lambda item: (
                item.get(
                    "confidence",
                    0
                ),
                len(
                    item.get(
                        "title",
                        ""
                    )
                ),
            )
        )

        students = sorted(
            set(
                student
                for item in group
                for student in item.get(
                    "students",
                    []
                )
            )
        )

        images = []

        for item in group:

            images.extend(
                item.get(
                    "images",
                    []
                )
            )

        # 이미지 중복 제거
        unique_images = []

        seen_images = set()

        for image in images:

            image_url = image.get(
                "url"
            )

            if not image_url:
                continue

            if image_url in seen_images:
                continue

            seen_images.add(
                image_url
            )

            unique_images.append(
                image
            )

        merged.append({
            "date": representative[
                "date"
            ],
            "title": representative[
                "title"
            ],
            "url": representative[
                "url"
            ],
            "source": representative[
                "source"
            ],

            # 기존 앱 호환용
            "confidence": confidence,

            # 새 정보
            "status": status,
            "sourceCount": source_count,
            "sources": source_ids,
            "students": students,
            "contentType": representative.get(
                "contentType",
                "other"
            ),
            "images": unique_images[:20],

            "evidence": [
                {
                    "sourceId": item[
                        "sourceId"
                    ],
                    "source": item[
                        "source"
                    ],
                    "title": item[
                        "title"
                    ],
                    "url": item[
                        "url"
                    ],
                }
                for item in group
            ],
        })

    merged.sort(
        key=lambda item: (
            item.get(
                "date",
                "9999-99-99"
            ),
            item.get(
                "title",
                ""
            ),
        )
    )

    return merged


# ============================================================
# 출처가 서로 다른 날짜를 말하는지 검사
# ============================================================

def detect_conflicts(
    events
):

    conflicts = []

    # 같은 제목/학생/콘텐츠에 대해 날짜가 다른 경우
    for i, a in enumerate(events):

        for b in events[
            i + 1:
        ]:

            if (
                a.get(
                    "sourceId"
                )
                == b.get(
                    "sourceId"
                )
            ):
                continue

            if (
                a.get(
                    "contentType"
                )
                != b.get(
                    "contentType"
                )
            ):
                continue

            title_score = similarity(
                a.get(
                    "title",
                    ""
                ),
                b.get(
                    "title",
                    ""
                ),
            )

            students_a = set(
                a.get(
                    "students",
                    []
                )
            )

            students_b = set(
                b.get(
                    "students",
                    []
                )
            )

            student_match = bool(
                students_a
                & students_b
            )

            if (
                title_score >= 0.35
                or student_match
            ):

                if (
                    a.get(
                        "date"
                    )
                    != b.get(
                        "date"
                    )
                ):

                    conflicts.append({
                        "type": "date_conflict",
                        "eventA": a,
                        "eventB": b,
                    })

    return conflicts


# ============================================================
# 기존 데이터
# ============================================================

def load_json(
    path
):

    if not path.exists():
        return {}

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as error:

        print(
            "[경고] JSON 읽기 실패:",
            path,
            error,
        )

        return {}


# ============================================================
# 데이터 검증
# ============================================================

def validate_result(
    data,
    successful_sources,
):

    # 최소한 한 곳이라도 성공해야 새 데이터 인정
    if not successful_sources:

        return False

    events = data.get(
        "events",
        []
    )

    if not isinstance(
        events,
        list
    ):

        return False

    # 이벤트가 전부 사라지는 경우 보호
    if len(events) == 0:

        return False

    return True


# ============================================================
# 원자적 저장
# ============================================================

def atomic_save_json(
    data
):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=OUTPUT_FILE.parent,
            prefix=".future-data-",
            suffix=".tmp",
            delete=False,
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write(
                "\n"
            )

            temp_file = Path(
                file.name
            )

        temp_file.replace(
            OUTPUT_FILE
        )

    finally:

        if (
            temp_file
            and temp_file.exists()
        ):

            try:
                temp_file.unlink()
            except Exception:
                pass


# ============================================================
# 설정
# ============================================================

def load_config():

    return load_json(
        CONFIG_FILE
    )


# ============================================================
# 메인
# ============================================================

def main():

    print()
    print(
        "=================================================="
    )
    print(
        " 블루 아카이브 미래시 통합 수집기"
    )
    print(
        "=================================================="
    )

    started_at = now_iso()

    old_data = load_json(
        OUTPUT_FILE
    )

    config = load_config()

    all_events = []

    source_evidence = []

    errors = []

    successful_sources = []


    # ========================================================
    # 1. 몰루로그
    # ========================================================

    print()
    print(
        "[1/3] 몰루로그 수집"
    )

    try:

        result = collect_mollulog()

        events = result[
            "events"
        ]

        all_events.extend(
            events
        )

        successful_sources.append(
            "mollulog"
        )

        source_evidence.append({
            "sourceId": "mollulog",
            "name": "몰루로그",
            "kind": "future",
            "url": SOURCES[
                "mollulog"
            ]["url"],
            "checkedAt": started_at,
            "eventCount": len(events),
            "imageCount": len(
                result[
                    "images"
                ]
            ),
            "items": events,
            "images": result[
                "images"
            ],
        })

        print(
            f"  ✓ 일정 {len(events)}개"
        )

    except Exception as error:

        print(
            "  ✗ 실패:",
            error,
        )

        errors.append({
            "sourceId": "mollulog",
            "error": str(error),
        })


    # ========================================================
    # 2. 갤러리
    # ========================================================

    print()
    print(
        "[2/3] 블루 아카이브 갤러리 수집"
    )

    try:

        result = collect_gallery()

        events = result[
            "events"
        ]

        all_events.extend(
            events
        )

        successful_sources.append(
            "bluearchive_gallery"
        )

        image_count = sum(
            len(
                item.get(
                    "images",
                    []
                )
            )
            for item in result[
                "posts"
            ]
        )

        source_evidence.append({
            "sourceId": "bluearchive_gallery",
            "name": "블루 아카이브 갤러리",
            "kind": "community",
            "url": SOURCES[
                "bluearchive_gallery"
            ]["url"],
            "checkedAt": started_at,
            "postCount": len(
                result[
                    "posts"
                ]
            ),
            "eventCount": len(
                events
            ),
            "imageCount": image_count,
            "items": result[
                "posts"
            ],
        })

        print(
            f"  ✓ 게시글 "
            f"{len(result['posts'])}개"
        )

        print(
            f"  ✓ 일정 "
            f"{len(events)}개"
        )

        print(
            f"  ✓ 이미지 "
            f"{image_count}개"
        )

    except Exception as error:

        print(
            "  ✗ 실패:",
            error,
        )

        errors.append({
            "sourceId": "bluearchive_gallery",
            "error": str(error),
        })


    # ========================================================
    # 3. 공식 포럼
    # ========================================================

    print()
    print(
        "[3/3] 블루 아카이브 공식 포럼 수집"
    )

    try:

        result = collect_official()

        events = result[
            "events"
        ]

        all_events.extend(
            events
        )

        successful_sources.append(
            "nexon_forum"
        )

        image_count = sum(
            len(
                item.get(
                    "images",
                    []
                )
            )
            for item in result[
                "posts"
            ]
        )

        source_evidence.append({
            "sourceId": "nexon_forum",
            "name": "블루 아카이브 공식 포럼",
            "kind": "official",
            "url": SOURCES[
                "nexon_forum"
            ]["url"],
            "checkedAt": started_at,
            "postCount": len(
                result[
                    "posts"
                ]
            ),
            "eventCount": len(
                events
            ),
            "imageCount": image_count,
            "items": result[
                "posts"
            ],
        })

        print(
            f"  ✓ 게시글 "
            f"{len(result['posts'])}개"
        )

        print(
            f"  ✓ 일정 "
            f"{len(events)}개"
        )

        print(
            f"  ✓ 이미지 "
            f"{image_count}개"
        )

    except Exception as error:

        print(
            "  ✗ 실패:",
            error,
        )

        errors.append({
            "sourceId": "nexon_forum",
            "error": str(error),
        })


    # ========================================================
    # 기본 정리
    # ========================================================

    all_events = clean_events(
        all_events
    )

    print()
    print(
        f"수집된 원본 일정: "
        f"{len(all_events)}개"
    )

    print(
        "성공한 출처:",
        ", ".join(
            successful_sources
        )
        if successful_sources
        else "없음"
    )


    # ========================================================
    # 기존 데이터 보호
    # ========================================================

    if not successful_sources:

        print()
        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "모든 출처 수집에 실패했습니다."
        )

        print(
            "기존 future-data.json을 유지합니다."
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        return


    # ========================================================
    # 출처 비교
    # ========================================================

    merged_events = merge_events(
        all_events
    )

    conflicts = detect_conflicts(
        all_events
    )


    # ========================================================
    # 새 데이터
    # ========================================================

    new_data = dict(
        old_data
    )

    new_data[
        "updatedAt"
    ] = started_at

    new_data[
        "server"
    ] = "KR"

    # 기존 앱 호환
    new_data[
        "defaultRangeMonths"
    ] = old_data.get(
        "defaultRangeMonths",
        12
    )

    new_data[
        "supportedRangeMonths"
    ] = [
        2,
        4,
        6,
        12,
        24,
        36
    ]


    # ========================================================
    # 핵심 events
    #
    # 기존 앱이 읽는 구조 유지
    # ========================================================

    new_data[
        "events"
    ] = all_events


    # ========================================================
    # 출처 통합 결과
    # ========================================================

    new_data[
        "mergedEvents"
    ] = merged_events


    # ========================================================
    # 출처 증거
    # ========================================================

    new_data[
        "sourceEvidence"
    ] = source_evidence


    # ========================================================
    # 오류
    # ========================================================

    new_data[
        "errors"
    ] = errors


    # ========================================================
    # 충돌
    # ========================================================

    new_data[
        "conflicts"
    ] = conflicts


    # ========================================================
    # OCR 정보
    # ========================================================

    new_data[
        "ocr"
    ] = {
        "enabled": OCR_AVAILABLE,
        "language": "kor+eng",
        "imageAnalysis": True,
        "method": "Tesseract",
    }


    # ========================================================
    # 출처 정보
    # ========================================================

    new_data[
        "sources"
    ] = [
        {
            "id": "mollulog",
            "name": "몰루로그",
            "type": "미래시",
            "trust": 0.90,
            "url": SOURCES[
                "mollulog"
            ]["url"],
        },

        {
            "id": "bluearchive_gallery",
            "name": "블루 아카이브 갤러리",
            "type": "커뮤니티",
            "trust": 0.65,
            "url": SOURCES[
                "bluearchive_gallery"
            ]["url"],
        },

        {
            "id": "nexon_forum",
            "name": "블루 아카이브 공식 포럼",
            "type": "공식",
            "trust": 1.00,
            "url": SOURCES[
                "nexon_forum"
            ]["url"],
        },
    ]


    # ========================================================
    # 검증 규칙
    # ========================================================

    old_rules = old_data.get(
        "rules",
        {}
    )

    config_rules = (
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
    ] = {
        "minSources": config_rules.get(
            "minSources",
            old_rules.get(
                "minSources",
                3
            )
        ),

        "conflictLabel": config_rules.get(
            "conflictLabel",
            old_rules.get(
                "conflictLabel",
                "의견 갈림"
            )
        ),

        "separateOfficialAndEstimated": True,

        "sourcePriority": [
            "nexon_forum",
            "mollulog",
            "bluearchive_gallery",
        ],
    }


    # ========================================================
    # 상태
    # ========================================================

    new_data[
        "collectorStatus"
    ] = {
        "successfulSources": successful_sources,
        "failedSources": [
            error[
                "sourceId"
            ]
            for error in errors
        ],
        "sourceCount": len(
            successful_sources
        ),
        "eventCount": len(
            all_events
        ),
        "mergedEventCount": len(
            merged_events
        ),
        "conflictCount": len(
            conflicts
        ),
        "ocrEnabled": OCR_AVAILABLE,
        "updatedAt": started_at,
    }


    # ========================================================
    # 설명
    # ========================================================

    new_data[
        "note"
    ] = (
        "몰루로그, 블루 아카이브 갤러리, "
        "블루 아카이브 공식 포럼을 수집합니다. "
        "게시글 본문과 첨부 이미지를 수집하고 "
        "이미지는 한국어/영어 OCR로 분석합니다. "
        "여러 출처의 일정은 비교하여 mergedEvents에 저장하며 "
        "공식 포럼 정보가 가장 높은 신뢰도를 가집니다. "
        "모든 출처가 실패한 경우 기존 데이터를 보존합니다."
    )


    # ========================================================
    # 최종 검증
    # ========================================================

    if not validate_result(
        new_data,
        successful_sources
    ):

        print()
        print(
            "[보호] 새 데이터 검증 실패."
        )

        print(
            "기존 future-data.json을 유지합니다."
        )

        return


    # ========================================================
    # 저장
    # ========================================================

    atomic_save_json(
        new_data
    )


    # ========================================================
    # 결과
    # ========================================================

    print()
    print(
        "=================================================="
    )

    print(
        " 업데이트 완료"
    )

    print(
        "=================================================="
    )

    print(
        f"원본 일정: "
        f"{len(all_events)}개"
    )

    print(
        f"통합 일정: "
        f"{len(merged_events)}개"
    )

    print(
        f"출처: "
        f"{len(successful_sources)}/3"
    )

    print(
        f"충돌: "
        f"{len(conflicts)}개"
    )

    print(
        f"OCR: "
        f"{'ON' if OCR_AVAILABLE else 'OFF'}"
    )

    print(
        f"오류: "
        f"{len(errors)}개"
    )

    print()

    # ========================================================
    # 일정 출력
    # ========================================================

    if merged_events:

        print(
            "통합 일정 일부:"
        )

        for event in merged_events[
            :30
        ]:

            print(
                f"- {event['date']} "
                f"| {event['status']} "
                f"| {event['title'][:80]}"
            )

            print(
                "  출처: "
                + ", ".join(
                    event[
                        "sources"
                    ]
                )
            )

    print()

    print(
        "저장 완료:",
        OUTPUT_FILE,
    )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n중단되었습니다."
        )

        sys.exit(130)

    except Exception as error:

        print()
        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "치명적인 오류:"
        )

        print(
            error
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        # 기존 JSON은 건드리지 않음
        sys.exit(1)
