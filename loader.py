import heapq
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QEventLoop, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
VALID_REMOTE_SCHEMES = {'http', 'https'}
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REMOTE_IMAGE_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 128 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000

@dataclass
class ImageItem:
    source_type: str  # file | zip | url
    display_name: str
    path: str
    zip_entry: Optional[str] = None
    remote_url: Optional[str] = None
    remote_headers: Dict[str, str] = field(default_factory=dict)
    sort_key: str = ''
    size_hint: Tuple[int, int] = (800, 1200)

@dataclass
class SourcePackage:
    kind: str
    path: str
    items: List[ImageItem]
    sibling_base_dir: Optional[str]
    meta: dict = field(default_factory=dict)

@dataclass
class LoadRequest:
    generation: int
    index: int
    item: ImageItem
    target_width: int
    target_height: int
    thumb_only: bool = False
    priority: int = 1000

@dataclass
class ResolveRequest:
    token: int
    mode: str  # paths | remote
    paths: Optional[List[str]] = None
    target: str = ''
    sort_mode: str = 'name_asc'

class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.base_href: Optional[str] = None
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()
        if tag == 'base' and not self.base_href:
            href = attrs_dict.get('href')
            if href:
                self.base_href = href.strip()
        elif tag == 'a':
            href = attrs_dict.get('href')
            if href:
                self.hrefs.append(href.strip())

def natural_key(text: str):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', text)]

def sort_items(items: List[ImageItem], mode: str) -> List[ImageItem]:
    if mode == 'name_desc':
        return sorted(items, key=lambda x: natural_key(x.display_name), reverse=True)
    if mode == 'path_asc':
        return sorted(items, key=lambda x: natural_key(x.path))
    return sorted(items, key=lambda x: natural_key(x.display_name))

def adjacent_sources(current_path: str, current_kind: str) -> List[str]:
    """Return adjacent chapter sources around the current local source.

    Supports:
    - direct chapter folders under one parent
    - zip chapters under one parent
    - title root folder that contains only chapter folders/zips
    """
    if current_kind not in {'folder', 'zip'}:
        return []

    current_path = os.path.normpath(current_path)
    current_name = os.path.basename(current_path)
    base_dir = os.path.dirname(current_path)

    def _collect_candidates(folder: str) -> List[str]:
        if not os.path.isdir(folder):
            return []
        out: List[str] = []
        try:
            names = os.listdir(folder)
        except Exception:
            return []
        for n in names:
            full = os.path.join(folder, n)
            if os.path.isdir(full) or n.lower().endswith('.zip'):
                out.append(full)
        return sorted(out, key=lambda p: natural_key(os.path.basename(p)))

    siblings = _collect_candidates(base_dir)
    if current_path in siblings:
        return siblings

    # If the user opened a title root folder that contains chapter folders/zips,
    # return that chapter list so the caller can jump into the first/next chapter.
    root_children = _collect_candidates(current_path) if current_kind == 'folder' else []
    if root_children:
        return root_children

    # Some downloads add one more nesting level. If all siblings under the parent are
    # image folders only under a single root, climb one level and look for a folder with
    # the same name as the current chapter's parent.
    parent_dir = os.path.dirname(base_dir)
    if os.path.isdir(parent_dir):
        parent_siblings = _collect_candidates(parent_dir)
        for entry in parent_siblings:
            if os.path.basename(entry) == os.path.basename(base_dir) and os.path.isdir(entry):
                nested = _collect_candidates(entry)
                if nested:
                    return nested

    return siblings

class SourceResolverWorker(QObject):
    resolved = Signal(int, object)
    failed = Signal(int, str)
    cancel_ack = Signal(int)

    def __init__(self, timeout_sec: int = 15):
        super().__init__()
        self.timeout_sec = timeout_sec
        self._cancelled: set[int] = set()
        self._current_token: Optional[int] = None
        self._current_reply: Optional[QNetworkReply] = None
        self._manager: Optional[QNetworkAccessManager] = None
        self._max_html_bytes = MAX_HTML_BYTES

    def _get_manager(self) -> QNetworkAccessManager:
        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        return self._manager

    def _is_cancelled(self, token: int) -> bool:
        return token in self._cancelled

    def _check_cancelled(self, token: int):
        if self._is_cancelled(token):
            raise RuntimeError('__cancelled__')

    @Slot(int)
    def cancel(self, token: int):
        self._cancelled.add(token)
        if self._current_token == token and self._current_reply is not None and self._current_reply.isRunning():
            self._current_reply.abort()
        self.cancel_ack.emit(token)

    @Slot(object)
    def resolve(self, request: ResolveRequest):
        token = request.token
        self._current_token = token
        try:
            self._check_cancelled(token)
            if request.mode == 'paths':
                package = self._resolve_paths(token, request.paths or [], request.sort_mode)
            else:
                package = self._resolve_remote(token, request.target, request.sort_mode)
            if not self._is_cancelled(token):
                self.resolved.emit(token, package)
        except Exception as exc:
            if str(exc) != '__cancelled__':
                self.failed.emit(token, str(exc))
        finally:
            self._current_token = None
            self._current_reply = None

    def _image_item_from_path(self, path: str) -> ImageItem:
        return ImageItem(source_type='file', display_name=os.path.basename(path), path=path)

    def _resolve_paths(self, token: int, paths: List[str], sort_mode: str) -> Optional[SourcePackage]:
        if not paths:
            return None
        if len(paths) == 1:
            p = paths[0]
            if os.path.isdir(p):
                return self._resolve_folder(token, p, sort_mode)
            if os.path.isfile(p) and p.lower().endswith('.zip'):
                return self._resolve_zip(token, p, sort_mode)

        image_files: List[str] = []
        for i, p in enumerate(paths):
            self._check_cancelled(token)
            if os.path.isdir(p):
                return self._resolve_folder(token, p, sort_mode)
            if os.path.isfile(p):
                ext = Path(p).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    image_files.append(p)
                elif ext == '.zip':
                    return self._resolve_zip(token, p, sort_mode)
            if i % 256 == 0:
                QThread.yieldCurrentThread()
        if image_files:
            items = [self._image_item_from_path(p) for p in image_files]
            return SourcePackage(kind='files', path=';'.join(image_files), items=sort_items(items, sort_mode), sibling_base_dir=None)
        return None

    def _resolve_folder(self, token: int, folder_path: str, sort_mode: str) -> Optional[SourcePackage]:
        items: List[ImageItem] = []
        for i, name in enumerate(os.listdir(folder_path)):
            self._check_cancelled(token)
            full = os.path.join(folder_path, name)
            if os.path.isfile(full) and Path(full).suffix.lower() in IMAGE_EXTENSIONS:
                items.append(self._image_item_from_path(full))
            if i % 256 == 0:
                QThread.yieldCurrentThread()
        if not items:
            return None
        return SourcePackage(kind='folder', path=folder_path, items=sort_items(items, sort_mode), sibling_base_dir=os.path.dirname(folder_path))

    def _resolve_zip(self, token: int, zip_path: str, sort_mode: str) -> Optional[SourcePackage]:
        items: List[ImageItem] = []
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for i, info in enumerate(zf.infolist()):
                self._check_cancelled(token)
                if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_EXTENSIONS:
                    items.append(ImageItem('zip', os.path.basename(info.filename), zip_path, zip_entry=info.filename))
                if i % 256 == 0:
                    QThread.yieldCurrentThread()
        if not items:
            return None
        return SourcePackage(kind='zip', path=zip_path, items=sort_items(items, sort_mode), sibling_base_dir=os.path.dirname(zip_path))

    def _fetch_html_sync(self, token: int, url: str) -> str:
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, 'MultiImageViewer/1.0')
        reply = self._get_manager().get(request)
        reply.setReadBufferSize(self._max_html_bytes + 65536)
        self._current_reply = reply

        loop = QEventLoop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        reply.finished.connect(loop.quit)

        buffer = bytearray()
        too_large = {'value': False}

        def _header_too_large() -> None:
            header = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
            try:
                if header is not None and int(header) > self._max_html_bytes:
                    too_large['value'] = True
                    reply.abort()
            except Exception:
                pass

        def _consume_ready_read() -> None:
            while reply.bytesAvailable() > 0:
                chunk = bytes(reply.read(min(65536, reply.bytesAvailable())))
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > self._max_html_bytes:
                    too_large['value'] = True
                    reply.abort()
                    return

        def _download_guard(received: int, total: int) -> None:
            if received > self._max_html_bytes or (total > 0 and total > self._max_html_bytes):
                too_large['value'] = True
                reply.abort()

        reply.metaDataChanged.connect(_header_too_large)
        reply.readyRead.connect(_consume_ready_read)
        reply.downloadProgress.connect(_download_guard)
        timer.start(self.timeout_sec * 1000)
        loop.exec()
        timer.stop()
        _consume_ready_read()

        try:
            self._check_cancelled(token)
            if too_large['value']:
                raise RuntimeError('원격 HTML 응답이 허용 크기(5MB)를 초과했습니다.')
            if reply.error() != QNetworkReply.NetworkError.NoError:
                err = reply.errorString()
                if self._is_cancelled(token):
                    raise RuntimeError('__cancelled__')
                raise RuntimeError(err)
            return bytes(buffer).decode('utf-8', errors='ignore')
        finally:
            reply.deleteLater()

    def _extract_remote_image_urls(self, html: str, target: str) -> List[str]:
        parser = _LinkExtractor()
        parser.feed(html)
        base_url = urljoin(target, parser.base_href) if parser.base_href else target
        results: List[str] = []
        for href in parser.hrefs:
            if not href or href.startswith('#'):
                continue
            parsed_href = urlparse(href)
            if parsed_href.scheme and parsed_href.scheme not in VALID_REMOTE_SCHEMES:
                continue
            candidate = urljoin(base_url, href)
            parsed = urlparse(candidate)
            if parsed.scheme not in VALID_REMOTE_SCHEMES:
                continue
            if Path(parsed.path).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            results.append(candidate)
        return sorted(set(results), key=natural_key)

    def _resolve_remote(self, token: int, target: str, sort_mode: str) -> Optional[SourcePackage]:
        target = target.strip()
        if not target:
            return None
        if '\n' in target or ',' in target:
            raw_urls = [u.strip() for u in re.split(r'[\n,]+', target) if u.strip()]
            items = []
            for i, url in enumerate(raw_urls):
                self._check_cancelled(token)
                parsed = urlparse(url)
                if parsed.scheme in VALID_REMOTE_SCHEMES and Path(parsed.path).suffix.lower() in IMAGE_EXTENSIONS:
                    items.append(ImageItem('url', os.path.basename(parsed.path) or url, url, remote_url=url))
                if i % 256 == 0:
                    QThread.yieldCurrentThread()
            if items:
                return SourcePackage('remote_list', '\n'.join(raw_urls), sort_items(items, sort_mode), None)

        parsed = urlparse(target)
        if parsed.scheme in VALID_REMOTE_SCHEMES:
            ext = Path(parsed.path).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                return SourcePackage('remote_single', target, [ImageItem('url', os.path.basename(parsed.path) or target, target, remote_url=target)], target.rsplit('/', 1)[0] if '/' in target else None)

            html = self._fetch_html_sync(token, target)
            self._check_cancelled(token)
            urls = self._extract_remote_image_urls(html, target)
            if not urls:
                return None
            items = [ImageItem('url', os.path.basename(urlparse(u).path) or u, u, remote_url=u) for u in urls]
            base = target.rstrip('/').rsplit('/', 1)[0] if '/' in target.rstrip('/') else None
            return SourcePackage('remote_http_dir', target, sort_items(items, sort_mode), base)

        if target.startswith('\\') and os.path.isdir(target):
            return self._resolve_folder(token, target, sort_mode)
        return None


class SourceResolverController(QObject):
    request_resolve = Signal(object)
    request_cancel = Signal(int)

    resolved = Signal(int, object)
    failed = Signal(int, str)
    cancel_ack = Signal(int)

    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = SourceResolverWorker()
        self.worker.moveToThread(self.thread)
        self.request_resolve.connect(self.worker.resolve)
        self.request_cancel.connect(self.worker.cancel)
        self.worker.resolved.connect(self.resolved)
        self.worker.failed.connect(self.failed)
        self.worker.cancel_ack.connect(self.cancel_ack)
        self.thread.start()

    def shutdown(self):
        self.thread.quit()
        self.thread.wait(2000)


class ImageLoaderWorker(QObject):
    thumb_loaded = Signal(int, int, QImage, tuple)
    full_loaded = Signal(int, int, QImage, tuple)
    failed = Signal(int, int, str)
    progress = Signal(int, int, int)
    cancel_ack = Signal(int)

    def __init__(self):
        super().__init__()
        self._queue: List[Tuple[int, int, int, LoadRequest]] = []
        self._sequence = 0
        self._active_generation = -1
        self._cancelled_generations: set[int] = set()
        self._stop = Event()
        self._loading_set: set[tuple[int, int, bool, int, int]] = set()
        self._drain_scheduled = False
        self._manager: Optional[QNetworkAccessManager] = None
        self._current_reply: Optional[QNetworkReply] = None
        self._current_req: Optional[LoadRequest] = None
        self._total_by_generation: Dict[int, int] = {}
        self._done_by_generation: Dict[int, int] = {}
        self._max_remote_bytes = MAX_REMOTE_IMAGE_BYTES
        self._max_zip_entry_bytes = MAX_ZIP_ENTRY_BYTES
        self._max_image_pixels = MAX_IMAGE_PIXELS

    def _get_manager(self) -> QNetworkAccessManager:
        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        return self._manager

    @Slot(int)
    def set_generation(self, generation: int):
        self._active_generation = generation
        self._queue = [entry for entry in self._queue if entry[3].generation == generation]
        heapq.heapify(self._queue)
        self._loading_set = {k for k in self._loading_set if k[0] == generation}
        self._total_by_generation[generation] = sum(1 for _, _, _, req in self._queue if req.generation == generation)
        self._done_by_generation.setdefault(generation, 0)
        self._prune_generation_state(keep={generation})

    @Slot(int)
    def cancel_generation(self, generation: int):
        self._cancelled_generations.add(generation)
        self._queue = [entry for entry in self._queue if entry[3].generation != generation]
        heapq.heapify(self._queue)
        self._loading_set = {k for k in self._loading_set if k[0] != generation}
        if self._current_req and self._current_req.generation == generation and self._current_reply and self._current_reply.isRunning():
            self._current_reply.abort()
        self._forget_generation(generation)
        self._prune_generation_state(keep={self._active_generation})
        self.cancel_ack.emit(generation)

    @Slot(list)
    def enqueue_requests(self, requests: list):
        added = 0
        for req in requests:
            key = (req.generation, req.index, req.thumb_only, req.target_width, req.target_height)
            if req.generation in self._cancelled_generations or key in self._loading_set:
                continue
            heapq.heappush(self._queue, (req.priority, req.index, self._sequence, req))
            self._sequence += 1
            self._loading_set.add(key)
            self._total_by_generation[req.generation] = self._total_by_generation.get(req.generation, 0) + 1
            self._done_by_generation.setdefault(req.generation, 0)
            added += 1
        if added:
            self._schedule_drain()

    @Slot()
    def shutdown(self):
        self._stop.set()
        self._queue.clear()
        if self._current_reply:
            self._current_reply.abort()
        self._total_by_generation.clear()
        self._done_by_generation.clear()

    def _schedule_drain(self):
        if self._drain_scheduled or self._stop.is_set():
            return
        self._drain_scheduled = True
        QTimer.singleShot(0, self._drain_queue)

    @Slot()
    def _drain_queue(self):
        self._drain_scheduled = False
        if self._stop.is_set() or self._current_reply is not None:
            return
        while self._queue:
            _, _, _, req = heapq.heappop(self._queue)
            if req.generation in self._cancelled_generations:
                self._loading_set.discard((req.generation, req.index, req.thumb_only, req.target_width, req.target_height))
                continue
            self._current_req = req
            if req.item.source_type == 'url':
                self._load_remote(req)
                return
            self._load_local(req)
            self._current_req = None
            break
        if self._queue and self._current_reply is None:
            self._schedule_drain()

    def _forget_generation(self, generation: int):
        self._total_by_generation.pop(generation, None)
        self._done_by_generation.pop(generation, None)

    def _prune_generation_state(self, keep: Optional[set[int]] = None):
        keep = set() if keep is None else {g for g in keep if g is not None and g >= 0}
        if self._current_req is not None:
            keep.add(self._current_req.generation)
        for generation in list(self._total_by_generation.keys()):
            if generation not in keep:
                self._total_by_generation.pop(generation, None)
        for generation in list(self._done_by_generation.keys()):
            if generation not in keep:
                self._done_by_generation.pop(generation, None)
        if self._active_generation >= 0:
            self._cancelled_generations = {g for g in self._cancelled_generations if g >= self._active_generation - 16}

    def _validate_image_size(self, image: Image.Image):
        if image.width <= 0 or image.height <= 0:
            raise RuntimeError('잘못된 이미지 크기입니다.')
        if image.width * image.height > self._max_image_pixels:
            raise RuntimeError(f'이미지 해상도가 허용 한도({self._max_image_pixels:,} px)를 초과했습니다.')

    def _open_and_normalize_image(self, source) -> Image.Image:
        with Image.open(source) as im:
            self._validate_image_size(im)
            return ImageOps.exif_transpose(im).convert('RGBA')

    def _read_zip_entry_bytes_limited(self, zf: zipfile.ZipFile, entry_name: str) -> bytes:
        info = zf.getinfo(entry_name)
        if info.file_size > self._max_zip_entry_bytes:
            raise RuntimeError(f'ZIP 내부 파일이 허용 크기({self._max_zip_entry_bytes // (1024 * 1024)}MB)를 초과했습니다.')
        buffer = bytearray()
        with zf.open(entry_name) as fp:
            while True:
                chunk = fp.read(64 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > self._max_zip_entry_bytes:
                    raise RuntimeError(f'ZIP 내부 파일이 허용 크기({self._max_zip_entry_bytes // (1024 * 1024)}MB)를 초과했습니다.')
        return bytes(buffer)

    def _load_local(self, req: LoadRequest):
        try:
            if req.item.source_type == 'file':
                pil_img = self._open_and_normalize_image(req.item.path)
            else:
                with zipfile.ZipFile(req.item.path, 'r') as zf:
                    assert req.item.zip_entry is not None
                    data = self._read_zip_entry_bytes_limited(zf, req.item.zip_entry)
                pil_img = self._open_and_normalize_image(io.BytesIO(data))
            self._process_and_emit(req, pil_img)
        except Exception as exc:
            self.failed.emit(req.generation, req.index, str(exc))
        finally:
            self._loading_set.discard((req.generation, req.index, req.thumb_only, req.target_width, req.target_height))
            self._emit_progress(req.generation)

    def _remote_length_too_large(self, reply: QNetworkReply) -> bool:
        header = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        try:
            return header is not None and int(header) > self._max_remote_bytes
        except Exception:
            return False

    def _load_remote(self, req: LoadRequest):
        url = req.item.remote_url or req.item.path
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, req.item.remote_headers.get('User-Agent', 'MultiImageViewer/1.0'))
        for hk, hv in (req.item.remote_headers or {}).items():
            if hk.lower() == 'user-agent':
                continue
            request.setRawHeader(hk.encode('utf-8'), str(hv).encode('utf-8'))
        reply = self._get_manager().get(request)
        self._current_reply = reply
        self._current_req = req
        reply.setReadBufferSize(self._max_remote_bytes + 65536)
        reply.setProperty('miv_req_generation', req.generation)
        reply.setProperty('miv_req_index', req.index)
        reply.setProperty('miv_req_thumb', req.thumb_only)

        def _metadata_check(reply=reply):
            if self._remote_length_too_large(reply):
                reply.abort()

        def _progress_check(received: int, total: int, reply=reply):
            if total > self._max_remote_bytes or received > self._max_remote_bytes:
                reply.abort()

        reply.metaDataChanged.connect(_metadata_check)
        reply.downloadProgress.connect(_progress_check)
        reply.finished.connect(lambda reply=reply, req=req: self._on_remote_finished(req, reply))

    def _on_remote_finished(self, req: LoadRequest, reply: QNetworkReply):
        if reply is self._current_reply:
            self._current_reply = None
            self._current_req = None
        try:
            if req.generation in self._cancelled_generations or self._stop.is_set():
                return
            if self._remote_length_too_large(reply):
                raise RuntimeError('원격 파일이 허용 크기(50MB)를 초과했습니다.')
            if reply.error() != QNetworkReply.NetworkError.NoError:
                if reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                    if req.generation in self._cancelled_generations:
                        return
                    received = int(reply.bytesAvailable())
                    if received > self._max_remote_bytes:
                        raise RuntimeError('원격 파일이 허용 크기(50MB)를 초과했습니다.')
                raise RuntimeError(reply.errorString())
            data = bytes(reply.readAll())
            if len(data) > self._max_remote_bytes:
                raise RuntimeError('원격 파일이 허용 크기(50MB)를 초과했습니다.')
            pil_img = self._open_and_normalize_image(io.BytesIO(data))
            self._process_and_emit(req, pil_img)
        except Exception as exc:
            if 'Operation canceled' not in str(exc):
                self.failed.emit(req.generation, req.index, str(exc))
        finally:
            self._loading_set.discard((req.generation, req.index, req.thumb_only, req.target_width, req.target_height))
            reply.deleteLater()
            self._emit_progress(req.generation)
            self._schedule_drain()

    def _process_and_emit(self, req: LoadRequest, image: Image.Image):
        if req.generation in self._cancelled_generations:
            return
        size_hint = (image.width, image.height)
        thumb = self._to_qimage_scaled(image, max(200, req.target_width // 3), max(240, req.target_height // 3))
        self.thumb_loaded.emit(req.generation, req.index, thumb, size_hint)
        if not req.thumb_only and req.generation not in self._cancelled_generations:
            full = self._to_qimage_full(image)
            self.full_loaded.emit(req.generation, req.index, full, size_hint)

    def _to_qimage_scaled(self, pil_image: Image.Image, target_width: int, target_height: int) -> QImage:
        # 💡 웹툰 로딩 속도 최적화 및 화질 균형
        # LANCZOS 대신 BILINEAR를 사용하고 원본 비율을 유지함
        aspect = pil_image.height / pil_image.width
        new_h = int(target_width * aspect)
        
        # 💡 고화질 유지를 위해 thumbnail 크기를 크게 유지
        image = pil_image.copy()
        image.thumbnail((max(1, target_width), max(1, new_h)), Image.Resampling.BILINEAR)
        
        raw = image.tobytes('raw', 'RGBA')
        return QImage(raw, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()

    def _to_qimage_full(self, pil_image: Image.Image) -> QImage:
        image = pil_image.copy()
        raw = image.tobytes('raw', 'RGBA')
        return QImage(raw, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()

    def _emit_progress(self, generation: int):
        if generation in self._cancelled_generations:
            self._forget_generation(generation)
            return
        done = self._done_by_generation.get(generation, 0) + 1
        self._done_by_generation[generation] = done
        total = max(self._total_by_generation.get(generation, done), done)
        self.progress.emit(generation, done, total)
        if done >= total and generation != self._active_generation:
            self._forget_generation(generation)
        self._prune_generation_state(keep={self._active_generation, generation})


class LoaderController(QObject):
    request_enqueue = Signal(list)
    request_generation = Signal(int)
    request_cancel = Signal(int)
    request_shutdown = Signal()

    thumb_loaded = Signal(int, int, QImage, tuple)
    full_loaded = Signal(int, int, QImage, tuple)
    failed = Signal(int, int, str)
    progress = Signal(int, int, int)
    cancel_ack = Signal(int)

    def __init__(self):
        super().__init__()
        self.thread = QThread()
        self.worker = ImageLoaderWorker()
        self.worker.moveToThread(self.thread)
        self.request_enqueue.connect(self.worker.enqueue_requests)
        self.request_generation.connect(self.worker.set_generation)
        self.request_cancel.connect(self.worker.cancel_generation)
        self.request_shutdown.connect(self.worker.shutdown)
        self.worker.thumb_loaded.connect(self.thumb_loaded)
        self.worker.full_loaded.connect(self.full_loaded)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.progress)
        self.worker.cancel_ack.connect(self.cancel_ack)
        self.thread.start()

    def shutdown(self):
        self.request_shutdown.emit()
        self.thread.quit()
        self.thread.wait(2000)