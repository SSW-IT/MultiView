import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from PySide6.QtCore import QObject, QThread, Signal, Slot

from scraper_bridge import EpisodePayload

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MAX_DOWNLOAD_FILE_BYTES = 80 * 1024 * 1024
CHUNK_SIZE = 64 * 1024


@dataclass
class DownloadReport:
    save_dir: str
    failed_items: List[Tuple[str, str]]
    cancelled: bool = False


@dataclass
class SeriesJob:
    start_url: str
    base_dir: str


@dataclass
class SeriesDownloadReport:
    start_url: str
    root_dir: str
    series_title: str
    episode_count: int
    failed_episodes: List[str] = field(default_factory=list)
    cancelled: bool = False


def _sanitize_name(title: str) -> str:
    title = title or 'webtoon_episode'
    for keyword in ['BlackToon', '블랙툰', '무료웹툰', '웹툰미리보기']:
        title = title.replace(keyword, '')
    title = title.strip(' -')
    title = re.sub(r'[\\/*?:"<>|]', '', title)
    return title.strip() or 'webtoon_episode'


def _natural_key(text: str):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', text)]


def _guess_series_title(title: str) -> str:
    raw = _sanitize_name(title)
    patterns = [
        r'\s*[-_–—|:]?\s*\d+\s*화\b.*$',
        r'\s*[-_–—|:]?\s*\d+\s*회\b.*$',
        r'\s*[-_–—|:]?\s*ep\.?\s*\d+\b.*$',
        r'\s*[-_–—|:]?\s*episode\s*\d+\b.*$',
        r'\s*[-_–—|:]?\s*chapter\s*\d+\b.*$',
    ]
    for pattern in patterns:
        candidate = re.sub(pattern, '', raw, flags=re.IGNORECASE)
        candidate = candidate.strip(' -_–—|:')
        if candidate and candidate != raw:
            return candidate
    return raw


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


class DownloaderWorker(QObject):
    progress = Signal(int, int)  # done, total
    failed_item = Signal(str, str)
    finished = Signal(object)  # DownloadReport
    fatal_error = Signal(str)
    cancelled = Signal(str)  # save_dir
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @Slot(object, str)
    def download(self, payload: EpisodePayload, base_dir: str):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.fatal_error.emit('이미 다운로드 작업이 진행 중입니다.')
                return
            self._cancel.clear()
            self._thread = threading.Thread(target=self._download_impl, args=(payload, base_dir), daemon=True)
            self._thread.start()
            self.busy_changed.emit(True)

    @Slot()
    def cancel(self):
        self._cancel.set()

    @Slot()
    def shutdown(self):
        self._cancel.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _emit_finished_state(self):
        self.busy_changed.emit(False)

    def _download_one(self, task):
        img_url, filepath, headers = task
        if self._cancel.is_set():
            return None
        try:
            with requests.get(img_url, headers=headers, timeout=(5, 20), stream=True) as response:
                response.raise_for_status()
                content_length = response.headers.get('Content-Length')
                if content_length:
                    try:
                        if int(content_length) > MAX_DOWNLOAD_FILE_BYTES:
                            raise ValueError(f'파일 크기 제한 초과: {img_url}')
                    except ValueError:
                        pass
                bytes_read = 0
                tmp_path = filepath + '.part'
                with open(tmp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if self._cancel.is_set():
                            raise RuntimeError('cancelled')
                        if not chunk:
                            continue
                        bytes_read += len(chunk)
                        if bytes_read > MAX_DOWNLOAD_FILE_BYTES:
                            raise ValueError(f'파일 크기 제한 초과: {img_url}')
                        f.write(chunk)
                os.replace(tmp_path, filepath)
            return True, img_url, None
        except RuntimeError as e:
            if str(e) == 'cancelled':
                try:
                    if os.path.exists(filepath + '.part'):
                        os.remove(filepath + '.part')
                except Exception:
                    pass
                return None
            return False, img_url, str(e)
        except Exception as e:
            try:
                if os.path.exists(filepath + '.part'):
                    os.remove(filepath + '.part')
            except Exception:
                pass
            return False, img_url, str(e)

    def _download_impl(self, payload: EpisodePayload, base_dir: str):
        save_dir = ''
        try:
            save_dir = os.path.join(base_dir, payload.title)
            Path(save_dir).mkdir(parents=True, exist_ok=True)

            download_tasks = []
            for img_idx, img_url in enumerate(payload.image_urls, start=1):
                parsed_url = urlparse(img_url)
                ext = os.path.splitext(parsed.path)[1].lower()
                valid_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
                if ext not in valid_exts:
                    ext = '.jpg' # 이상한 확장자는 강제로 .jpg로 변경하여 실행을 막음 (또는 건너뛰기)
                filename = f'{payload.title}_{img_idx:03d}{ext}'
                filepath = os.path.join(save_dir, filename)
                headers = dict(getattr(payload, 'image_headers', {}) or {})
                headers.setdefault('User-Agent', USER_AGENT)
                headers.setdefault('Referer', payload.url)
                download_tasks.append((img_url, filepath, headers))

            total = len(download_tasks)
            done = 0
            failures: List[Tuple[str, str]] = []
            self.progress.emit(done, total)

            with ThreadPoolExecutor(max_workers=8) as executor:
                pending = {executor.submit(self._download_one, task): task for task in download_tasks}
                while pending:
                    if self._cancel.is_set():
                        for future in pending:
                            future.cancel()
                        wait(list(pending), timeout=0.2)
                        self.cancelled.emit(save_dir)
                        self.finished.emit(DownloadReport(save_dir=save_dir, failed_items=failures, cancelled=True))
                        self._emit_finished_state()
                        return
                    done_set, _ = wait(list(pending), timeout=0.2, return_when=FIRST_COMPLETED)
                    if not done_set:
                        continue
                    for future in done_set:
                        pending.pop(future, None)
                        try:
                            result = future.result()
                        except Exception as e:
                            result = (False, 'unknown', str(e))
                        if result is None:
                            continue
                        success, result_url, error = result
                        done += 1
                        if not success and result_url:
                            failures.append((result_url, error or '알 수 없는 오류'))
                            self.failed_item.emit(result_url, error or '알 수 없는 오류')
                        self.progress.emit(done, total)

            self.finished.emit(DownloadReport(save_dir=save_dir, failed_items=failures, cancelled=False))
        except Exception as e:
            self.fatal_error.emit(str(e))
        finally:
            self._emit_finished_state()


class DownloaderController(QObject):
    request_download = Signal(object, str)
    request_cancel = Signal()
    request_shutdown = Signal()

    progress = Signal(int, int)
    failed_item = Signal(str, str)
    finished = Signal(object)
    fatal_error = Signal(str)
    cancelled = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = DownloaderWorker()
        self.worker.moveToThread(self.thread)
        self.request_download.connect(self.worker.download)
        self.request_cancel.connect(self.worker.cancel)
        self.request_shutdown.connect(self.worker.shutdown)
        self.worker.progress.connect(self.progress)
        self.worker.failed_item.connect(self.failed_item)
        self.worker.finished.connect(self.finished)
        self.worker.fatal_error.connect(self.fatal_error)
        self.worker.cancelled.connect(self.cancelled)
        self.worker.busy_changed.connect(self.busy_changed)
        self.thread.start()

    def shutdown(self):
        self.request_shutdown.emit()
        self.thread.quit()
        self.thread.wait(3000)


class SeriesDownloaderWorker(QObject):
    queue_added = Signal(str)
    job_started = Signal(str, str)
    episode_started = Signal(int, str, int)
    episode_image_progress = Signal(int, int, int, str)
    episode_finished = Signal(int, str, str, int)
    info = Signal(str)
    finished = Signal(object)  # SeriesDownloadReport
    fatal_error = Signal(str)
    cancelled = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._queue: List[SeriesJob] = []
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    @Slot(str, str)
    def enqueue(self, start_url: str, base_dir: str):
        start_url = str(start_url or '').strip()
        base_dir = str(base_dir or '').strip()
        if not start_url:
            self.fatal_error.emit('유효한 시작 URL이 없습니다.')
            return
        if not base_dir:
            self.fatal_error.emit('유효한 저장 폴더가 없습니다.')
            return
        with self._lock:
            self._queue.append(SeriesJob(start_url=start_url, base_dir=base_dir))
        self.queue_added.emit(start_url)

    @Slot()
    def cancel_current(self):
        self._cancel.set()

    @Slot()
    def shutdown(self):
        self._shutdown.set()
        self._cancel.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.5)

    def _pop_job(self) -> Optional[SeriesJob]:
        with self._lock:
            if self._queue:
                return self._queue.pop(0)
        return None

    def _run_loop(self):
        while not self._shutdown.is_set():
            job = self._pop_job()
            if job is None:
                time.sleep(0.15)
                continue
            self._cancel.clear()
            self.busy_changed.emit(True)
            try:
                report = self._process_job(job)
                if report.cancelled:
                    self.cancelled.emit(report.root_dir)
                self.finished.emit(report)
            except Exception as exc:
                self.fatal_error.emit(str(exc))
            finally:
                self.busy_changed.emit(False)

    def _sleep_with_cancel(self, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._cancel.is_set() or self._shutdown.is_set():
                return False
            time.sleep(0.05)
        return True

    def _ensure_not_cancelled(self):
        if self._cancel.is_set() or self._shutdown.is_set():
            raise RuntimeError('__cancelled__')

    def _goto_with_retry(self, page, url: str):
        attempts = [
            {'wait_until': 'domcontentloaded', 'timeout': 30000},
            {'wait_until': 'load', 'timeout': 45000},
            {'wait_until': 'commit', 'timeout': 45000},
        ]
        last_error = None
        for attempt in attempts:
            self._ensure_not_cancelled()
            try:
                page.goto(url, **attempt)
                return
            except Exception as exc:
                last_error = exc
                self.info.emit(f"접속 재시도 중... ({attempt['wait_until']})")
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

    def _extract_nav(self, page, selectors: List[str]):
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

    def _extract_episode_payload(self, context, page, source_url: str) -> EpisodePayload:
        raw_title = page.title() or 'webtoon_episode'
        safe_title = _sanitize_name(raw_title)

        self.info.emit('이미지 로딩을 위해 스크롤 중...')
        last_height = 0
        stable_count = 0
        for _ in range(220):
            self._ensure_not_cancelled()
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

        self.info.emit('이미지 후보 분석 중...')
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
            filtered.append((float(row.get('top') or 0.0), src))

        filtered.sort(key=lambda item: item[0])
        image_urls = [src for _, src in filtered]
        if not image_urls:
            raise RuntimeError('다운로드할 이미지를 찾지 못했습니다.')

        next_url, has_next = self._extract_nav(page, ['#page_nexts', '#page_next', 'a[rel="next"]', '.view-pager a.next'])
        prev_url, has_prev = self._extract_nav(page, ['#page_prevs', '#page_prev', 'a[rel="prev"]', '.view-pager a.prev'])

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

    def _download_episode_images(self, payload: EpisodePayload, episode_dir: str, episode_index: int) -> Tuple[int, int]:
        Path(episode_dir).mkdir(parents=True, exist_ok=True)
        headers = dict(getattr(payload, 'image_headers', {}) or {})
        headers.setdefault('User-Agent', USER_AGENT)
        headers.setdefault('Referer', payload.url)
        tasks = []
        for idx, img_url in enumerate(payload.image_urls, start=1):
            parsed = urlparse(img_url)
            ext = os.path.splitext(parsed.path)[1].lower()
            valid_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
            if ext not in valid_exts:
                ext = '.jpg' # 이상한 확장자는 강제로 .jpg로 변경하여 실행을 막음 (또는 건너뛰기)
            filename = f'{payload.title}_{idx:03d}{ext}'
            filepath = os.path.join(episode_dir, filename)
            tasks.append((idx, img_url, filepath, dict(headers)))

        def _one(task):
            idx, img_url, filepath, req_headers = task
            if self._cancel.is_set():
                return idx, False, img_url, 'cancelled'
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return idx, True, img_url, None
            tmp_path = filepath + '.part'
            try:
                with requests.get(img_url, headers=req_headers, timeout=(5, 25), stream=True) as response:
                    response.raise_for_status()
                    content_length = response.headers.get('Content-Length')
                    if content_length:
                        try:
                            if int(content_length) > MAX_DOWNLOAD_FILE_BYTES:
                                raise ValueError(f'파일 크기 제한 초과: {img_url}')
                        except ValueError:
                            pass
                    bytes_read = 0
                    with open(tmp_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if self._cancel.is_set():
                                raise RuntimeError('cancelled')
                            if not chunk:
                                continue
                            bytes_read += len(chunk)
                            if bytes_read > MAX_DOWNLOAD_FILE_BYTES:
                                raise ValueError(f'파일 크기 제한 초과: {img_url}')
                            f.write(chunk)
                os.replace(tmp_path, filepath)
                return idx, True, img_url, None
            except Exception as exc:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return idx, False, img_url, str(exc)

        failures = 0
        total = len(tasks)
        done = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_one, task) for task in tasks]
            for future in as_completed(futures):
                idx, success, img_url, error = future.result()
                if self._cancel.is_set():
                    for f in futures:
                        f.cancel()
                    raise RuntimeError('__cancelled__')
                done += 1
                if not success and error != 'cancelled':
                    failures += 1
                self.episode_image_progress.emit(episode_index, done, total, payload.title)
        return total, failures

    def _process_job(self, job: SeriesJob) -> SeriesDownloadReport:
        if sync_playwright is None:
            raise RuntimeError('Playwright가 설치되어 있지 않습니다. pip install playwright 후 playwright install을 실행하세요.')

        current_url = job.start_url.strip()
        if not current_url:
            raise RuntimeError('유효한 시작 URL이 없습니다.')

        episode_count = 0
        failed_episodes: List[str] = []
        visited_urls = set()
        series_title = ''
        root_dir = job.base_dir

        with sync_playwright() as p:
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

                while current_url:
                    self._ensure_not_cancelled()
                    if current_url in visited_urls:
                        failed_episodes.append(f'순환 감지로 중단: {current_url}')
                        break
                    visited_urls.add(current_url)

                    self.info.emit(f'페이지 접속 중: {current_url}')
                    self._goto_with_retry(page, current_url)
                    if not self._sleep_with_cancel(1.0):
                        raise RuntimeError('__cancelled__')

                    payload = self._extract_episode_payload(context, page, current_url)
                    episode_count += 1

                    if not series_title:
                        series_title = _guess_series_title(payload.title)
                        root_dir = os.path.join(job.base_dir, series_title)
                        Path(root_dir).mkdir(parents=True, exist_ok=True)
                        self.job_started.emit(job.start_url, series_title)

                    self.episode_started.emit(episode_count, payload.title, len(payload.image_urls))
                    episode_dir = os.path.join(root_dir, payload.title)
                    try:
                        total_images, failures = self._download_episode_images(payload, episode_dir, episode_count)
                        self.episode_finished.emit(episode_count, payload.title, episode_dir, total_images)
                        if failures > 0:
                            failed_episodes.append(f'{payload.title} (이미지 {failures}장 실패)')
                    except RuntimeError as exc:
                        if str(exc) == '__cancelled__':
                            raise
                        failed_episodes.append(f'{payload.title} (다운로드 오류: {exc})')

                    next_url = payload.next_url
                    if next_url:
                        current_url = next_url
                        continue
                    if payload.has_next:
                        try:
                            self.info.emit('다음 화 버튼으로 이동 중...')
                            handle = self._first_handle(page, ['#page_nexts', '#page_next', 'a[rel="next"]', '.view-pager a.next'])
                            if handle is None:
                                break
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
                            direct = _sanitize_navigable_url(page.url, href) or _extract_url_from_script(page.url, onclick or '')
                            if direct:
                                current_url = direct
                            else:
                                old_url = page.url
                                try:
                                    with page.expect_navigation(wait_until='domcontentloaded', timeout=20000):
                                        handle.click()
                                except Exception:
                                    page.evaluate('(el) => el.click()', handle)
                                    page.wait_for_timeout(1200)
                                current_url = page.url
                                if not current_url or current_url == old_url:
                                    break
                        except Exception as exc:
                            failed_episodes.append(f'{payload.title} (다음 화 이동 실패: {exc})')
                            break
                    else:
                        break
            except RuntimeError as exc:
                if str(exc) == '__cancelled__':
                    return SeriesDownloadReport(
                        start_url=job.start_url,
                        root_dir=root_dir,
                        series_title=series_title or _guess_series_title('webtoon_series'),
                        episode_count=episode_count,
                        failed_episodes=failed_episodes,
                        cancelled=True,
                    )
                raise
            finally:
                browser.close()

        return SeriesDownloadReport(
            start_url=job.start_url,
            root_dir=root_dir,
            series_title=series_title or _guess_series_title('webtoon_series'),
            episode_count=episode_count,
            failed_episodes=failed_episodes,
            cancelled=False,
        )


class SeriesDownloaderController(QObject):
    request_enqueue = Signal(str, str)
    request_cancel = Signal()
    request_shutdown = Signal()

    queue_added = Signal(str)
    job_started = Signal(str, str)
    episode_started = Signal(int, str, int)
    episode_image_progress = Signal(int, int, int, str)
    episode_finished = Signal(int, str, str, int)
    info = Signal(str)
    finished = Signal(object)
    fatal_error = Signal(str)
    cancelled = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = SeriesDownloaderWorker()
        self.worker.moveToThread(self.thread)
        self.request_enqueue.connect(self.worker.enqueue)
        self.request_cancel.connect(self.worker.cancel_current)
        self.request_shutdown.connect(self.worker.shutdown)
        self.worker.queue_added.connect(self.queue_added)
        self.worker.job_started.connect(self.job_started)
        self.worker.episode_started.connect(self.episode_started)
        self.worker.episode_image_progress.connect(self.episode_image_progress)
        self.worker.episode_finished.connect(self.episode_finished)
        self.worker.info.connect(self.info)
        self.worker.finished.connect(self.finished)
        self.worker.fatal_error.connect(self.fatal_error)
        self.worker.cancelled.connect(self.cancelled)
        self.worker.busy_changed.connect(self.busy_changed)
        self.thread.start()

    def shutdown(self):
        self.request_shutdown.emit()
        self.thread.quit()
        self.thread.wait(3000)
