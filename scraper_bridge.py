import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from PySide6.QtCore import QObject, QThread, Signal, Slot

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'


def clean_folder_name(title: str) -> str:
    keywords_to_remove = ['BlackToon', '블랙툰', '무료웹툰', '웹툰미리보기']
    for keyword in keywords_to_remove:
        title = title.replace(keyword, '')
    title = title.strip(' -')
    return re.sub(r'[\\/*?:"<>|]', '', title).strip() or 'webtoon_episode'


def _sanitize_navigable_url(base_url: str, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.lower().startswith('javascript:') or raw == '#':
        return None
    full = urljoin(base_url, raw)
    parsed = urlparse(full)
    if parsed.scheme.lower() not in {'http', 'https'}:
        return None
    return full


def _extract_url_from_script(base_url: str, script: str) -> Optional[str]:
    if not script:
        return None
    patterns = [
        r"['\"](https?://[^'\"]+)['\"]",
        r"['\"]([^'\"]+\.(?:html|htm|php)(?:\?[^'\"]*)?)['\"]",
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r"location\.assign\(\s*['\"]([^'\"]+)['\"]\s*\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, script, re.IGNORECASE)
        if match:
            return _sanitize_navigable_url(base_url, match.group(1))
    return None


@dataclass
class EpisodePayload:
    url: str
    title: str
    image_urls: List[str]
    next_url: Optional[str] = None
    prev_url: Optional[str] = None
    has_next: bool = False
    has_prev: bool = False
    image_headers: Dict[str, str] = field(default_factory=dict)


class ScraperWorker(QObject):
    resolved = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    cancelled = Signal()
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @Slot(object)
    def extract(self, request):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.failed.emit('이미 브라우저 추출 작업이 진행 중입니다. 먼저 취소하거나 완료를 기다려 주세요.')
                return
            self._cancel.clear()
            self._thread = threading.Thread(target=self._extract_impl, args=(request,), daemon=True)
            self._thread.start()
            self.busy_changed.emit(True)

    @Slot()
    def cancel(self):
        self._cancel.set()

    @Slot()
    def shutdown(self):
        self._cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _emit_finished_state(self):
        self.busy_changed.emit(False)

    def _sleep_with_cancel(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False
            time.sleep(0.05)
        return True

    def _goto_with_retry(self, page, url: str):
        attempts = [
            {'wait_until': 'domcontentloaded', 'timeout': 30000},
            {'wait_until': 'load', 'timeout': 45000},
            {'wait_until': 'commit', 'timeout': 45000},
        ]
        last_error = None
        for attempt in attempts:
            if self._cancel.is_set():
                raise RuntimeError('__cancelled__')
            try:
                page.goto(url, **attempt)
                return
            except Exception as exc:
                last_error = exc
                self.progress.emit(f"접속 재시도 중... ({attempt['wait_until']})")
                if not self._sleep_with_cancel(0.8):
                    raise RuntimeError('__cancelled__')
        raise last_error or RuntimeError('페이지 접속 실패')

    def _first_handle(self, page, selectors: List[str]):
        for selector in selectors:
            try:
                handle = page.query_selector(selector)
                if handle is not None:
                    return handle
            except Exception:
                continue
        return None

    def _navigate_by_button(self, page, direction: int):
        selectors = (
            ['#page_nexts', '#page_next', 'a[rel="next"]', '.view-pager a.next']
            if direction == 1
            else ['#page_prevs', '#page_prev', 'a[rel="prev"]', '.view-pager a.prev']
        )

        handle = self._first_handle(page, selectors)
        if handle is None:
            raise RuntimeError('해당 방향의 회차 버튼을 찾지 못했습니다.')

        current_url = page.url
        href = None
        onclick = None
        try:
            href = handle.get_attribute('href')
        except Exception:
            pass
        try:
            onclick = handle.get_attribute('onclick')
        except Exception:
            pass

        direct_url = _sanitize_navigable_url(current_url, href) or _extract_url_from_script(current_url, onclick or '')
        if direct_url:
            self.progress.emit('다음/이전 화로 이동 중...')
            self._goto_with_retry(page, direct_url)
            return

        self.progress.emit('다음/이전 화 버튼 클릭 중...')
        navigated = False

        try:
            with page.expect_navigation(wait_until='domcontentloaded', timeout=20000):
                handle.click()
            navigated = True
        except Exception:
            try:
                with page.expect_navigation(wait_until='domcontentloaded', timeout=20000):
                    page.evaluate('(el) => el.click()', handle)
                navigated = True
            except Exception:
                try:
                    page.evaluate('(el) => el.click()', handle)
                    page.wait_for_timeout(1200)
                    page.wait_for_load_state('domcontentloaded', timeout=8000)
                    navigated = page.url != current_url
                except Exception:
                    navigated = False

        if not navigated:
            final_url = _extract_url_from_script(current_url, href or '') or _extract_url_from_script(current_url, onclick or '')
            if final_url:
                self._goto_with_retry(page, final_url)
                return
            if page.url == current_url or page.url.lower().startswith('javascript:'):
                raise RuntimeError('다음/이전 화 이동에 실패했습니다.')

    def _extract_payload_from_page(self, context, page, source_url: str) -> EpisodePayload:
        raw_title = page.title() or 'webtoon_episode'
        safe_title = clean_folder_name(raw_title)

        self.progress.emit('이미지 로딩을 위해 스크롤 중...')
        last_height = 0
        stable_count = 0
        for _ in range(220):
            if self._cancel.is_set():
                raise RuntimeError('__cancelled__')
            page.evaluate('window.scrollBy(0, Math.max(window.innerHeight * 0.85, 800));')
            if not self._sleep_with_cancel(0.18):
                raise RuntimeError('__cancelled__')
            current_height = page.evaluate('document.body.scrollHeight')
            is_bottom = page.evaluate('window.scrollY + window.innerHeight >= document.body.scrollHeight - 4')
            if current_height == last_height and is_bottom:
                stable_count += 1
            else:
                stable_count = 0
            last_height = current_height
            if stable_count >= 4:
                break

        page.evaluate('window.scrollTo(0, 0)')
        self._sleep_with_cancel(0.2)

        self.progress.emit('이미지 후보 분석 중...')
        candidates = page.evaluate(
            """
            () => {
                const attrs = ['data-original','data-lazy-src','data-src','data-url','src'];
                const nodes = Array.from(document.images || []);
                return nodes.map((img, index) => {
                    let chosen = '';
                    for (const key of attrs) {
                        const v = img.getAttribute(key);
                        if (v && /^https?:/i.test(v)) { chosen = v; break; }
                    }
                    if (!chosen && img.currentSrc && /^https?:/i.test(img.currentSrc)) {
                        chosen = img.currentSrc;
                    }
                    const rect = img.getBoundingClientRect();
                    return {
                        index,
                        src: chosen,
                        currentSrc: img.currentSrc || '',
                        width: img.naturalWidth || 0,
                        height: img.naturalHeight || 0,
                        top: (window.scrollY || 0) + rect.top,
                        className: img.className || '',
                        id: img.id || '',
                    };
                });
            }
            """
        )

        filtered = []
        seen = set()
        for row in candidates:
            src = (row.get('currentSrc') or row.get('src') or '').strip()
            if not src:
                continue
            src = urljoin(source_url, src)
            if src in seen:
                continue
            seen.add(src)

            width = int(row.get('width') or 0)
            height = int(row.get('height') or 0)
            marker = f"{row.get('id','')} {row.get('className','')} {src}".lower()

            if any(bad in marker for bad in ['thumb', 'thumbnail', 'avatar', 'logo', 'icon', 'banner']):
                continue
            if width < 180 or height < 180:
                continue
            if width * height < 120_000:
                continue

            filtered.append((float(row.get('top') or 0.0), src, width, height))

        filtered.sort(key=lambda item: item[0])
        image_urls = [src for _, src, _, _ in filtered]
        if not image_urls:
            raise RuntimeError('다운로드할 이미지를 찾지 못했습니다.')

        def _extract_nav(selectors):
            handle = self._first_handle(page, selectors)
            if handle is None:
                return None, False
            href = None
            onclick = None
            try:
                href = handle.get_attribute('href')
            except Exception:
                pass
            try:
                onclick = handle.get_attribute('onclick')
            except Exception:
                pass
            resolved = _sanitize_navigable_url(page.url, href) or _extract_url_from_script(page.url, onclick or '')
            return resolved, True

        next_url, has_next = _extract_nav(['#page_nexts', '#page_next', 'a[rel="next"]', '.view-pager a.next'])
        prev_url, has_prev = _extract_nav(['#page_prevs', '#page_prev', 'a[rel="prev"]', '.view-pager a.prev'])

        cookies = context.cookies()
        cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies if c.get('name'))
        headers = {'User-Agent': USER_AGENT, 'Referer': page.url}
        if cookie_header:
            headers['Cookie'] = cookie_header

        return EpisodePayload(
            url=page.url,
            title=safe_title,
            image_urls=image_urls,
            next_url=next_url,
            prev_url=prev_url,
            has_next=has_next,
            has_prev=has_prev,
            image_headers=headers,
        )

    def _extract_impl(self, request):
        if sync_playwright is None:
            self.failed.emit('Playwright가 설치되어 있지 않습니다. pip install playwright 후 playwright install을 실행하세요.')
            self._emit_finished_state()
            return

        if isinstance(request, dict):
            url = str(request.get('url', '')).strip()
            nav_direction = int(request.get('nav_direction', 0) or 0)
        else:
            url = str(request).strip()
            nav_direction = 0

        if not url:
            self.failed.emit('유효한 URL이 없습니다.')
            self._emit_finished_state()
            return

        try:
            with sync_playwright() as p:
                self.progress.emit('브라우저 시작 중...')
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled', '--disable-infobars'],
                )
                try:
                    context = browser.new_context(
                        user_agent=USER_AGENT,
                        viewport={'width': 1440, 'height': 2200},
                        device_scale_factor=1,
                    )
                    context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                        "window.navigator.chrome = { runtime: {} };"
                    )
                    page = context.new_page()
                    page.set_default_navigation_timeout(45000)

                    self.progress.emit(f'페이지 접속 중: {url}')
                    self._goto_with_retry(page, url)
                    if not self._sleep_with_cancel(1.0):
                        raise RuntimeError('__cancelled__')

                    if nav_direction in (-1, 1):
                        self._navigate_by_button(page, nav_direction)
                        if not self._sleep_with_cancel(1.0):
                            raise RuntimeError('__cancelled__')

                    payload = self._extract_payload_from_page(context, page, url)
                    self.resolved.emit(payload)
                finally:
                    browser.close()
        except Exception as exc:
            if str(exc) == '__cancelled__' or self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        finally:
            self._emit_finished_state()


class ScraperController(QObject):
    request_extract = Signal(object)
    request_cancel = Signal()

    resolved = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    cancelled = Signal()
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = ScraperWorker()
        self.worker.moveToThread(self.thread)
        self.request_extract.connect(self.worker.extract)
        self.request_cancel.connect(self.worker.cancel)
        self.worker.resolved.connect(self.resolved)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.progress)
        self.worker.cancelled.connect(self.cancelled)
        self.worker.busy_changed.connect(self.busy_changed)
        self.thread.start()

    def shutdown(self):
        self.worker.shutdown()
        self.thread.quit()
        self.thread.wait(3000)