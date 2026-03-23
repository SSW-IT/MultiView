import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QPoint, Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from bookmark import AppState, Bookmark
from loader import (
    ImageItem,
    LoadRequest,
    LoaderController,
    ResolveRequest,
    SourcePackage,
    SourceResolverController,
    adjacent_sources,
)
from scraper_bridge import ScraperController, EpisodePayload
from downloader import DownloadReport, DownloaderController, SeriesDownloadReport, SeriesDownloaderController


@dataclass
class ViewState:
    top_index: int = 0
    offset_in_item: int = 0


class DraggableScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._last_pos = QPoint()
        self.setWidgetResizable(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = event.position().toPoint()
            delta = pos - self._last_pos
            self._last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImageCell(QLabel):
    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.item: Optional[ImageItem] = None
        self.loaded_level = 'none'
        self.source_size = (800, 1200)
        self.last_render_key: Optional[Tuple[int, int, str, str, int]] = None
        self._original_pixmap = QPixmap()
        self.display_max_width: Optional[int] = None
        self.allow_upscale = True
        self._padding = 0
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setStyleSheet('background: transparent; border: none; padding: 0px; margin: 0px; color: #d7deea;')
        self.setMinimumHeight(120)

    def set_placeholder(self, text: str = '로딩 중...'):
        self.loaded_level = 'none'
        self.last_render_key = None
        self._original_pixmap = QPixmap()
        super().setPixmap(QPixmap())
        self.setText(text)
        self.setStyleSheet('background: transparent; border: none; padding: 8px 0px; margin: 0px; color: #9aa5b8;')
        self.setFixedHeight(160)

    def set_error(self, text: str):
        self.loaded_level = 'error'
        self._original_pixmap = QPixmap()
        super().setPixmap(QPixmap())
        self.setText(text)
        self.setStyleSheet('background: #3a1f24; border: 1px solid #cc5d68; padding: 8px; border-radius: 8px; color: #ffe7ea;')
        self.setMinimumHeight(160)
        self.setMaximumHeight(16777215)

    def set_display_pixmap(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        self.setText('')
        self.setStyleSheet('background: transparent; border: none; padding: 0px; margin: 0px;')
        self._update_scaled_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._original_pixmap.isNull():
            self._update_scaled_pixmap()

    def _update_scaled_pixmap(self):
        if self._original_pixmap.isNull():
            return
        avail_w = max(80, self.width())
        if self.display_max_width is not None:
            avail_w = min(avail_w, max(80, int(self.display_max_width)))
        target_w = avail_w if self.allow_upscale else min(avail_w, self._original_pixmap.width())
        if target_w == self._original_pixmap.width():
            scaled = self._original_pixmap
        else:
            scaled = self._original_pixmap.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
        super().setPixmap(scaled)
        self.setFixedSize(scaled.width(), scaled.height())


class SettingsDialog(QDialog):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.setWindowTitle('설정')
        form = QFormLayout(self)
        self.theme = QComboBox()
        self.theme.addItems(['dark', 'light'])
        self.theme.setCurrentText(state.get_setting('theme', 'dark'))
        self.preload_rows = QSpinBox()
        self.preload_rows.setRange(1, 60)
        self.preload_rows.setValue(int(state.get_setting('preload_rows', 6)))
        self.release_rows = QSpinBox()
        self.release_rows.setRange(2, 120)
        self.release_rows.setValue(int(state.get_setting('release_rows', 18)))
        self.wheel_step = QSpinBox()
        self.wheel_step.setRange(20, 600)
        self.wheel_step.setValue(int(state.get_setting('wheel_step', 120)))
        self.refresh_debounce_ms = QSpinBox()
        self.refresh_debounce_ms.setRange(10, 500)
        self.refresh_debounce_ms.setValue(int(state.get_setting('refresh_debounce_ms', 40)))
        self.reader_max_width = QSpinBox()
        self.reader_max_width.setRange(400, 2200)
        self.reader_max_width.setValue(int(state.get_setting('reader_max_width', 1100)))
        self.zoom_percent = QSpinBox()
        self.zoom_percent.setRange(30, 200)
        self.zoom_percent.setValue(int(state.get_setting('zoom_percent', 100)))
        self.slide_step_px = QSpinBox()
        self.slide_step_px.setRange(5, 120)
        self.slide_step_px.setValue(int(state.get_setting('slide_step_px', 28)))

        form.addRow('테마', self.theme)
        form.addRow('프리로드 범위', self.preload_rows)
        form.addRow('유지 범위', self.release_rows)
        form.addRow('휠 이동량', self.wheel_step)
        form.addRow('리프레시 딜레이(ms)', self.refresh_debounce_ms)
        form.addRow('리더 최대 폭(px)', self.reader_max_width)
        form.addRow('기본 확대(%)', self.zoom_percent)
        form.addRow('슬라이드쇼 픽셀/틱', self.slide_step_px)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return {
            'theme': self.theme.currentText(),
            'preload_rows': self.preload_rows.value(),
            'release_rows': self.release_rows.value(),
            'wheel_step': self.wheel_step.value(),
            'refresh_debounce_ms': self.refresh_debounce_ms.value(),
            'reader_max_width': self.reader_max_width.value(),
            'zoom_percent': self.zoom_percent.value(),
            'slide_step_px': self.slide_step_px.value(),
        }


class RecentDialog(QDialog):
    def __init__(self, entries: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle('최근 열기')
        self.selected_entry: Optional[dict] = None
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for entry in entries:
            title = entry.get('title') or entry.get('path')
            self.list_widget.addItem(f"[{entry.get('kind')}] {title}")
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.entries = entries

    def accept_selection(self):
        idx = self.list_widget.currentRow()
        if idx >= 0:
            self.selected_entry = self.entries[idx]
            self.accept()


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('도움말')
        self.resize(780, 720)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setHtml(self._build_html())
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _build_html(self) -> str:
        return """
        <h2>Multi Image Viewer 도움말</h2>
        <p>웹툰 URL 보기와 로컬 폴더/ZIP 감상을 모두 지원하는 세로 스크롤 뷰어입니다.</p>
        <p>정주행 다운로드는 입력한 첫 화 URL부터 마지막 화까지 자동으로 저장하며, 다운로드 중에도 다른 작품을 계속 볼 수 있습니다.</p>

        <h3>1. 열기</h3>
        <ul>
          <li><b>Ctrl+L</b>: 로컬 이미지 / ZIP / 폴더 열기</li>
          <li><b>Ctrl+Shift+R</b>: 웹툰 주소 열기</li>
          <li><b>Ctrl+Shift+D</b>: 시작 URL부터 최종화까지 정주행 다운로드</li>
          <li><b>Ctrl+Shift+Q</b>: 정주행 다운로드 관리자 열기</li>
          <li><b>Ctrl+Alt+D</b>: 현재 화만 다운로드</li>
          <li>드래그 &amp; 드롭으로 폴더, ZIP, 이미지 파일을 바로 열 수 있습니다.</li>
        </ul>

        <h3>2. 읽기 / 확대</h3>
        <ul>
          <li><b>마우스 휠</b> / <b>↑ ↓</b>: 위아래 스크롤</li>
          <li><b>PageDown / Enter</b>: 다음 화면</li>
          <li><b>PageUp</b>: 이전 화면</li>
          <li><b>Ctrl + 마우스 휠</b>: 확대 / 축소</li>
          <li><b>Ctrl+=</b>, <b>Ctrl+-</b>, <b>Ctrl+0</b>: 확대, 축소, 초기화</li>
          <li><b>Ctrl+F</b>: 맞춤 전환 (리더폭 / 창폭)</li>
          <li><b>F11</b>: 전체 화면</li>
        </ul>

        <h3>3. 회차 이동</h3>
        <ul>
          <li><b>[ / ]</b>: 이전 화 / 다음 화</li>
          <li>툴바의 <b>◀ 이전 화 / 다음 화 ▶</b> 버튼으로도 이동할 수 있습니다.</li>
          <li>로컬 폴더에서는 같은 부모 폴더 안의 다른 화 폴더 또는 ZIP을 자동 탐색합니다.</li>
          <li>작품 루트 폴더를 열었을 때는 그 안의 1화, 2화 같은 하위 폴더를 회차 목록으로 인식합니다.</li>
          <li><b>자동 다음 화</b>가 켜져 있으면 맨 아래에서 한 번 더 내릴 때 다음 화로 넘어갑니다.</li>
          <li><b>Alt+N</b>: 자동 다음 화 켜기 / 끄기</li>
        </ul>

        <h3>4. 슬라이드쇼 / 이동</h3>
        <ul>
          <li><b>Space</b>: 슬라이드쇼 시작 / 중지</li>
          <li>슬라이드쇼 중 <b>↑</b>는 느리게, <b>↓</b>는 빠르게</li>
          <li><b>Ctrl+G</b>: 현재 작품 내 특정 비율 위치로 이동</li>
          <li><b>Ctrl+J</b>: 로컬 작품에서 회차 번호를 입력해 해당 화로 이동</li>
        </ul>

        <h3>5. 부가 기능</h3>
        <ul>
          <li><b>Ctrl+S</b>: 현재 위치를 책갈피로 저장</li>
          <li><b>Ctrl+B</b>: 책갈피 열기</li>
          <li><b>Ctrl+H</b>: 최근 열기 목록</li>
          <li><b>Ctrl+,</b>: 설정</li>
          <li><b>Ctrl+.</b>: 현재 로딩 / 다운로드 취소</li>
          <li>정주행 다운로드 관리자는 <b>비모달</b>이므로 다운로드 중에도 다른 웹툰을 계속 볼 수 있습니다.</li>
        </ul>

        <h3>6. 로컬 웹툰 폴더 구조 권장</h3>
        <ul>
          <li>작품명/1화, 작품명/2화, 작품명/3화 ...</li>
          <li>또는 작품명/001화.zip, 작품명/002화.zip ...</li>
          <li>화 폴더 이름에 숫자가 있으면 자연 정렬로 다음 화를 계산합니다.</li>
        </ul>
        """


class SeriesDownloadPanel(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('정주행 다운로드 관리자')
        self.resize(880, 680)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.summary_label = QLabel('대기 중')
        self.summary_label.setStyleSheet('font-size: 14px; font-weight: 700;')
        root.addWidget(self.summary_label)

        self.current_label = QLabel('현재 작업: 없음')
        root.addWidget(self.current_label)

        self.current_episode_label = QLabel('현재 회차: 없음')
        root.addWidget(self.current_episode_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        summary_row = QHBoxLayout()
        self.remaining_label = QLabel('남은 작업 수: 0')
        self.completed_label = QLabel('완료 목록: 0')
        self.failed_label = QLabel('실패 로그: 0')
        summary_row.addWidget(self.remaining_label)
        summary_row.addStretch(1)
        summary_row.addWidget(self.completed_label)
        summary_row.addWidget(self.failed_label)
        root.addLayout(summary_row)

        lists_row = QHBoxLayout()

        queue_col = QVBoxLayout()
        queue_col.addWidget(QLabel('큐 목록'))
        self.queue_list = QListWidget()
        queue_col.addWidget(self.queue_list)
        lists_row.addLayout(queue_col, 1)

        done_col = QVBoxLayout()
        done_col.addWidget(QLabel('완료 목록'))
        self.completed_list = QListWidget()
        done_col.addWidget(self.completed_list)
        lists_row.addLayout(done_col, 1)

        root.addLayout(lists_row, 1)

        root.addWidget(QLabel('실패 회차 로그'))
        self.failed_browser = QTextBrowser()
        self.failed_browser.setOpenExternalLinks(False)
        root.addWidget(self.failed_browser, 1)

        button_row = QHBoxLayout()
        self.cancel_button = QPushButton('현재 정주행 취소')
        self.hide_button = QPushButton('창 숨기기')
        self.clear_button = QPushButton('기록 지우기')
        button_row.addWidget(self.cancel_button)
        button_row.addStretch(1)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.hide_button)
        root.addLayout(button_row)

        self.hide_button.clicked.connect(self.hide)
        self.clear_button.clicked.connect(self.clear_history)

    def clear_history(self):
        self.completed_list.clear()
        self.failed_browser.clear()
        self._update_counters()

    def append_queue(self, text: str):
        self.queue_list.addItem(text)
        self._update_counters()

    def remove_queue_item(self, text: str):
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            if item is not None and item.text() == text:
                self.queue_list.takeItem(i)
                break
        self._update_counters()

    def set_current_job(self, text: str):
        self.current_label.setText(f'현재 작업: {text}')

    def set_current_episode(self, text: str):
        self.current_episode_label.setText(f'현재 회차: {text}')

    def set_progress(self, done: int, total: int):
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)

    def add_completed(self, text: str):
        self.completed_list.addItem(text)
        self._update_counters()

    def add_failed_log(self, text: str):
        current = self.failed_browser.toPlainText().strip()
        combined = (current + "\n" + text).strip() if current else text
        self.failed_browser.setPlainText(combined)
        self.failed_browser.verticalScrollBar().setValue(self.failed_browser.verticalScrollBar().maximum())
        self._update_counters()

    def set_summary(self, text: str):
        self.summary_label.setText(text)

    def _update_counters(self):
        self.remaining_label.setText(f'남은 작업 수: {self.queue_list.count()}')
        self.completed_label.setText(f'완료 목록: {self.completed_list.count()}')
        failed_count = len([line for line in self.failed_browser.toPlainText().splitlines() if line.strip()])
        self.failed_label.setText(f'실패 로그: {failed_count}')

    def closeEvent(self, event):
        self.hide()
        event.ignore()


class ChapterToast(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('chapterToast')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.SubWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._fade_out)
        self.anim = QPropertyAnimation(self, b'windowOpacity', self)
        self.anim.setDuration(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        self.label = QLabel('')
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet('background: transparent; color: white; font-size: 16px; font-weight: 700;')
        layout.addWidget(self.label)
        self.setStyleSheet('#chapterToast { background: rgba(24, 24, 24, 220); border: 1px solid rgba(255,255,255,45); border-radius: 14px; }')

    def show_message(self, text: str):
        self.anim.stop()
        self.hide_timer.stop()
        self.label.setText(text)
        self.adjustSize()
        if self.parentWidget() is not None:
            parent = self.parentWidget()
            x = max(16, parent.width() - self.width() - 24)
            self.move(x, 20)
        self.setWindowOpacity(0.96)
        self.show()
        self.raise_()
        self.hide_timer.start(1500)

    def _fade_out(self):
        self.anim.stop()
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._finish_hide)
        self.anim.start()

    def _finish_hide(self):
        try:
            self.anim.finished.disconnect(self._finish_hide)
        except Exception:
            pass
        self.hide()
        self.setWindowOpacity(0.96)


class MainWindow(QMainWindow):
    MAX_FULL_CACHE = 32
    MAX_THUMB_CACHE = 96

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Multi Image Viewer - Advanced Python')
        self.resize(1280, 920)
        self.setAcceptDrops(True)

        self.state = AppState()
        self.resolver = SourceResolverController()
        self.loader = LoaderController()
        self.scraper = ScraperController()
        self.downloader = DownloaderController()
        self.series_downloader = SeriesDownloaderController()

        self.loader.thumb_loaded.connect(self.on_thumb_loaded)
        self.loader.full_loaded.connect(self.on_full_loaded)
        self.loader.failed.connect(self.on_load_failed)
        self.loader.progress.connect(self.on_load_progress)
        self.loader.cancel_ack.connect(self.on_loader_cancel_ack)

        self.resolver.resolved.connect(self.on_resolve_completed)
        self.resolver.failed.connect(self.on_resolve_failed)
        self.resolver.cancel_ack.connect(self.on_resolve_cancel_ack)

        self.scraper.resolved.connect(self.on_scraper_resolved)
        self.scraper.failed.connect(self.on_scraper_failed)
        self.scraper.progress.connect(self.update_status)
        self.scraper.cancelled.connect(self.on_scraper_cancelled)

        self.downloader.progress.connect(self.on_download_progress)
        self.downloader.finished.connect(self.on_download_finished)
        self.downloader.fatal_error.connect(self.on_download_failed)
        self.downloader.failed_item.connect(self.on_download_failed_item)
        self.downloader.cancelled.connect(self.on_download_cancelled)

        self.series_downloader.queue_added.connect(self.on_series_queue_added)
        self.series_downloader.job_started.connect(self.on_series_job_started)
        self.series_downloader.episode_started.connect(self.on_series_episode_started)
        self.series_downloader.episode_image_progress.connect(self.on_series_episode_progress)
        self.series_downloader.episode_finished.connect(self.on_series_episode_finished)
        self.series_downloader.info.connect(self.on_series_info)
        self.series_downloader.finished.connect(self.on_series_finished)
        self.series_downloader.fatal_error.connect(self.on_series_failed)
        self.series_downloader.cancelled.connect(self.on_series_cancelled)

        self.current_source: Optional[SourcePackage] = None
        self.previous_source_path: Optional[str] = None
        self.pending_restore_state: Optional[ViewState] = None
        self.active_scraper_mode: Optional[str] = None
        self.scrape_progress: Optional[QProgressDialog] = None
        self.download_progress: Optional[QProgressDialog] = None
        self.pending_download_dir: Optional[str] = None
        self.pending_scraper_restore_state: Optional[ViewState] = None
        self.download_failures: List[Tuple[str, str]] = []
        self.series_progress: Optional[QProgressDialog] = None
        self.series_download_summaries: List[str] = []
        self.series_panel: Optional[SeriesDownloadPanel] = None
        self.series_pending_jobs: List[str] = []
        self.series_current_job: Optional[str] = None

        self.columns = 1
        self.sort_mode = 'name_asc'
        self.fit_mode = self.state.get_setting('fit_mode', 'width')
        self.reader_max_width = int(self.state.get_setting('reader_max_width', 1100))
        self.zoom_percent = int(self.state.get_setting('zoom_percent', 100))
        self.auto_next_enabled = bool(self.state.get_setting('auto_next_enabled', True))
        self.generation = 0
        self.resolve_token = 0
        self.cancelled_generations: Set[int] = set()
        self.cancelled_resolve_tokens: Set[int] = set()

        self.items: List[ImageItem] = []
        self.item_heights: List[int] = []
        self.loaded_levels: Dict[int, str] = {}
        self.size_hints: Dict[int, Tuple[int, int]] = {}
        self.active_cells: Dict[int, ImageCell] = {}
        self.active_range: Tuple[int, int] = (-1, -1)
        self.full_cache: OrderedDict[int, Tuple[QImage, Tuple[int, int]]] = OrderedDict()
        self.thumb_cache: OrderedDict[int, Tuple[QImage, Tuple[int, int]]] = OrderedDict()
        self.requested_full: Set[Tuple[int, int, int, int]] = set()
        self.requested_thumb: Set[Tuple[int, int, int, int]] = set()
        self.full_indexes: Set[int] = set()
        self.thumb_indexes: Set[int] = set()

        self.slide_show_enabled = False
        self.slide_speed_level = 1
        self.slide_timer = QTimer(self)
        self.slide_timer.timeout.connect(self.slide_step)
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self.refresh_visible_window)

        self.setup_ui()
        self.chapter_toast = ChapterToast(self)
        self.series_panel = SeriesDownloadPanel(self)
        self.series_panel.cancel_button.clicked.connect(self.series_downloader.request_cancel)
        self.apply_theme(self.state.get_setting('theme', 'dark'))
        self.setup_shortcuts()
        self.update_zoom_label()
        self.update_status('준비됨')

    def setup_ui(self):
        self.central = QWidget()
        self.setCentralWidget(self.central)
        root = QVBoxLayout(self.central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_toolbar = QToolBar('빠른 실행', self)
        self.top_toolbar.setMovable(False)
        self.top_toolbar.setFloatable(False)
        self.top_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)
        self.top_toolbar.addAction('로컬 열기', self.open_local)
        self.top_toolbar.addAction('웹툰 주소 열기', self.open_remote_browser)
        self.top_toolbar.addAction('현재 화 다운로드', self.download_remote_browser)
        self.top_toolbar.addAction('정주행 다운로드', self.download_remote_series_browser)
        self.top_toolbar.addAction('정주행 관리자', self.show_series_download_panel)
        self.top_toolbar.addSeparator()
        
        # 💡 [추가] 직관적인 UI 이전/다음 화 버튼 배치
        self.top_toolbar.addAction('◀ 이전 화', partial(self.open_adjacent_source, -1))
        self.top_toolbar.addAction('다음 화 ▶', partial(self.open_adjacent_source, 1))
        self.top_toolbar.addSeparator()

        self.auto_next_action = self.top_toolbar.addAction(self._auto_next_action_text(), self.toggle_auto_next)
        self.top_toolbar.addSeparator()
        self.top_toolbar.addAction('회차 이동', self.goto_local_episode)
        self.top_toolbar.addSeparator()

        self.fit_action = self.top_toolbar.addAction(self._fit_action_text(), self.toggle_fit_mode)
        self.top_toolbar.addSeparator()
        self.top_toolbar.addAction('−', self.zoom_out)
        self.zoom_label = QLabel('100%')
        self.zoom_label.setStyleSheet('padding: 0 8px; font-weight: 700;')
        self.top_toolbar.addWidget(self.zoom_label)
        self.top_toolbar.addAction('+', self.zoom_in)
        self.top_toolbar.addAction('초기화', self.reset_zoom)

        self.scroll_area = DraggableScrollArea()
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet('QAbstractScrollArea { background: #0d1320; border: none; }')
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda: self.schedule_visible_refresh())

        self.content = QWidget()
        self.content.setObjectName('contentPanel')
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self.top_spacer = QWidget()
        self.top_spacer.setFixedHeight(0)
        self.active_host = QWidget()
        self.active_layout = QVBoxLayout(self.active_host)
        self.active_layout.setContentsMargins(0, 0, 0, 0)
        self.active_layout.setSpacing(0)
        self.bottom_spacer = QWidget()
        self.bottom_spacer.setFixedHeight(0)
        self.content_layout.addWidget(self.top_spacer)
        self.content_layout.addWidget(self.active_host)
        self.content_layout.addWidget(self.bottom_spacer)
        self.content_layout.addStretch(0)
        self.scroll_area.setWidget(self.content)
        root.addWidget(self.scroll_area)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def setup_shortcuts(self):
        pairs = [
            ('Ctrl+L', self.open_local),
            ('Ctrl+R', self.open_remote),
            ('Ctrl+Shift+R', self.open_remote_browser),
            ('Ctrl+Shift+D', self.download_remote_series_browser),
            ('Ctrl+Shift+Q', self.show_series_download_panel),
            ('Ctrl+Alt+D', self.download_remote_browser),
            ('Ctrl+W', self.close_images),
            ('Ctrl+S', self.save_bookmark),
            ('Ctrl+B', self.open_bookmark),
            ('Ctrl+H', self.open_recent_dialog),
            ('Ctrl+,', self.open_settings),
            ('Ctrl+0', self.reset_zoom),
            ('Ctrl+=', self.zoom_in),
            ('Ctrl++', self.zoom_in),
            ('Ctrl+-', self.zoom_out),
            ('F11', self.toggle_fullscreen),
            ('Space', self.toggle_slideshow),
            ('PageDown', self.next_page),
            ('PageUp', self.prev_page),
            ('Return', self.next_page),
            ('[', partial(self.open_adjacent_source, -1)),
            (']', partial(self.open_adjacent_source, 1)),
            ('Ctrl+F', self.toggle_fit_mode),
            ('Alt+N', self.toggle_auto_next),
            ('Ctrl+.', self.cancel_loading),
            ('Ctrl+G', self.goto_ratio),
            ('Ctrl+J', self.goto_local_episode),
            ('F1', self.show_help),
        ]
        for key, func in pairs:
            QShortcut(QKeySequence(key), self, activated=func)

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction('로컬 열기', self.open_local)
        menu.addAction('Remote 열기', self.open_remote)
        menu.addAction('웹툰 주소 열기', self.open_remote_browser)
        menu.addAction('현재 화 다운로드', self.download_remote_browser)
        menu.addAction('정주행 다운로드', self.download_remote_series_browser)
        menu.addAction('정주행 관리자', self.show_series_download_panel)
        menu.addAction(self._auto_next_action_text(), self.toggle_auto_next)
        menu.addAction('회차 이동', self.goto_local_episode)
        menu.addSeparator()
        menu.addAction('도움말', self.show_help)
        menu.addAction('로딩 취소', self.cancel_loading)
        menu.addAction('닫기', self.close_images)
        menu.exec(self.mapToGlobal(pos))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.load_source_from_paths(paths)

    def _check_auto_next(self, step_value: int) -> bool:
        """💡 [핵심 로직] 스크롤이 맨 아래에 도달했을 때 자동으로 다음 화를 로드합니다."""
        if step_value <= 0 or not self.current_source or not self.auto_next_enabled:
            return False

        bar = self.scroll_area.verticalScrollBar()
        # 스크롤이 맨 밑에 도달했는지 확인 (여유분 5px)
        if bar.maximum() > 0 and bar.value() >= bar.maximum() - 5:
            if self.scrape_progress is not None or self.resolve_token > 0:
                return True # 이미 로딩 중이면 추가 명령 차단
            
            # 다음 화로 이동
            self.open_adjacent_source(1, auto=True)
            return True
        return False

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.adjust_zoom(10 if delta > 0 else -10)
            event.accept()
            return
        base = -int(delta / 120) * int(self.state.get_setting('wheel_step', 120))
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            base *= 2
            
        # 💡 마우스 휠로 맨 아래에서 굴리면 자동 다음 화
        if self._check_auto_next(base):
            return

        self.scroll_by(base)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'chapter_toast') and self.chapter_toast.isVisible():
            self.chapter_toast.show_message(self.chapter_toast.label.text())
        self.schedule_visible_refresh(delay_ms=80)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.slide_show_enabled:
                self.stop_slideshow()
            self.showMinimized()
            return
        if key == Qt.Key.Key_Up:
            if self.slide_show_enabled:
                self.set_slideshow_speed(self.slide_speed_level - 1)
            else:
                self.scroll_by(-int(self.state.get_setting('wheel_step', 120)))
            return
        if key == Qt.Key.Key_Down:
            if self.slide_show_enabled:
                self.set_slideshow_speed(self.slide_speed_level + 1)
            else:
                step = int(self.state.get_setting('wheel_step', 120))
                # 💡 키보드 방향키로 맨 아래에서 내리면 자동 다음 화
                if self._check_auto_next(step):
                    return
                self.scroll_by(step)
            return
        super().keyPressEvent(event)

    def apply_theme(self, theme: str):
        if theme == 'light':
            self.setStyleSheet(
                'QMainWindow, QWidget { background: #f3f3f3; color: #1f2430; } '
                '#contentPanel { background: #f8f6ef; } '
                'QMenu, QListWidget, QDialog, QLineEdit, QComboBox, QSpinBox, QTextBrowser { background: #ffffff; color: #202020; } '
                'QStatusBar { background: #ececec; color: #202020; border-top: 1px solid #c8c8c8; } '
            )
        else:
            self.setStyleSheet(
                'QMainWindow, QWidget { background: #0e1117; color: #e6edf3; } '
                '#contentPanel { background: #0b1020; } '
                'QMenu, QListWidget, QDialog, QLineEdit, QComboBox, QSpinBox, QTextBrowser { background: #161b22; color: #e6edf3; } '
                'QStatusBar { background: #0b0f14; color: #d0d7de; border-top: 1px solid #222b36; } '
                'QToolBar { border-bottom: 1px solid #202938; } '
                'QScrollBar:vertical { background: #171d25; width: 14px; margin: 0; } '
                'QScrollBar::handle:vertical { background: #657285; min-height: 32px; border-radius: 6px; } '
                'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; } '
            )

    def update_status(self, text: str):
        self.status.showMessage(text)

    def update_scroll_status(self, prefix='위치'):
        bar = self.scroll_area.verticalScrollBar()
        ratio = 0 if bar.maximum() <= 0 else int(bar.value() / max(1, bar.maximum()) * 100)
        self.update_status(f'{prefix} | {ratio}% | full {len(self.full_indexes)}/{len(self.items)}')

    def show_help(self):
        HelpDialog(self).exec()

    def open_local(self):
        selected, _ = QFileDialog.getOpenFileNames(self, '이미지/ZIP 선택', '', 'Images (*.jpg *.jpeg *.png *.bmp *.gif *.webp);;Zip (*.zip);;All (*)')
        if selected:
            self.load_source_from_paths(selected)
            return
        folder = QFileDialog.getExistingDirectory(self, '또는 폴더 선택')
        if folder:
            self.load_source_from_paths([folder])

    def open_remote(self):
        text, ok = QInputDialog.getMultiLineText(self, 'Remote 열기', '일반 URL 목록 / HTTP / UNC 경로:', '')
        if ok and text.strip():
            self.load_source_remote(text.strip())

    def _close_scrape_progress(self):
        if self.scrape_progress is not None:
            try:
                self.scrape_progress.canceled.disconnect(self.scraper.request_cancel)
            except Exception:
                pass
            self.scrape_progress.close()
            self.scrape_progress = None

    def _close_download_progress(self):
        if self.download_progress is not None:
            try:
                self.download_progress.canceled.disconnect(self.downloader.request_cancel)
            except Exception:
                pass
            self.download_progress.close()
            self.download_progress = None

    def _start_scraper(self, url: str, mode: str, restore_state: Optional[ViewState] = None, download_dir: Optional[str] = None, nav_direction: int = 0):
        self.active_scraper_mode = mode
        self.pending_scraper_restore_state = restore_state
        self.pending_download_dir = download_dir
        self._close_scrape_progress()
        self.scrape_progress = QProgressDialog('웹 페이지 분석 중...', '취소', 0, 0, self)
        self.scrape_progress.setWindowTitle('웹 주소 처리')
        self.scrape_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.scrape_progress.canceled.connect(self.scraper.request_cancel)
        self.scrape_progress.show()
        self.update_status('웹 페이지 분석 중... 취소는 Ctrl+.')
        self.scraper.request_extract.emit({'url': url, 'nav_direction': nav_direction})

    def _start_resolve(self, request: ResolveRequest, restore_state: Optional[ViewState] = None):
        if self.resolve_token > 0:
            self.cancelled_resolve_tokens.add(self.resolve_token)
            self.resolver.request_cancel.emit(self.resolve_token)
        self.pending_restore_state = restore_state
        self.resolve_token = request.token
        self.update_status('소스 분석 중...')
        self.resolver.request_resolve.emit(request)

    def load_source_from_paths(self, paths: List[str], restore_state: Optional[ViewState] = None):
        token = self.resolve_token + 1
        self._start_resolve(ResolveRequest(token=token, mode='paths', paths=paths, sort_mode=self.sort_mode), restore_state)

    def load_source_remote(self, target: str, restore_state: Optional[ViewState] = None):
        token = self.resolve_token + 1
        self._start_resolve(ResolveRequest(token=token, mode='remote', target=target, sort_mode=self.sort_mode), restore_state)

    def on_resolve_completed(self, token: int, package):
        if token != self.resolve_token or token in self.cancelled_resolve_tokens:
            return
        self.resolve_token = 0
        if not package:
            QMessageBox.information(self, '알림', '이미지 소스를 찾지 못했습니다.')
            self.update_status('열 수 있는 이미지가 없습니다.')
            return
        self.set_source(package, self.pending_restore_state)
        self.pending_restore_state = None

    def on_resolve_failed(self, token: int, error: str):
        if token != self.resolve_token or token in self.cancelled_resolve_tokens:
            return
        self.resolve_token = 0
        QMessageBox.critical(self, '소스 분석 오류', error)
        self.update_status('소스 분석 실패')

    def on_resolve_cancel_ack(self, token: int):
        self.cancelled_resolve_tokens.add(token)
        if token == self.resolve_token:
            self.resolve_token = 0

    def open_remote_browser(self):
        url, ok = QInputDialog.getText(self, '웹 주소 열기', '웹 페이지 URL:')
        if ok and url.strip():
            self._start_scraper(url.strip(), mode='view')

    def load_source_scraper(self, url: str, restore_state: Optional[ViewState] = None):
        self._start_scraper(url.strip(), mode='view', restore_state=restore_state)

    def download_remote_browser(self):
        url, ok = QInputDialog.getText(self, '웹 주소 다운로드', '다운로드할 웹 페이지 URL:')
        if not ok or not url.strip():
            return
        save_dir = QFileDialog.getExistingDirectory(self, '저장할 폴더 선택')
        if save_dir:
            self._start_scraper(url.strip(), mode='download', download_dir=save_dir)

    def show_series_download_panel(self):
        if self.series_panel is None:
            self.series_panel = SeriesDownloadPanel(self)
            self.series_panel.cancel_button.clicked.connect(self.series_downloader.request_cancel)
        self.series_panel.show()
        self.series_panel.raise_()

    def download_remote_series_browser(self):
        text, ok = QInputDialog.getMultiLineText(self, '정주행 다운로드', '첫 화 URL(여러 개면 줄바꿈으로 입력):', '')
        if not ok or not text.strip():
            return
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        if not urls:
            return
        save_dir = QFileDialog.getExistingDirectory(self, '정주행 저장 폴더 선택')
        if not save_dir:
            return
        self.show_series_download_panel()
        for url in urls:
            self.series_pending_jobs.append(url)
            if self.series_panel is not None:
                self.series_panel.append_queue(url)
            self.series_downloader.request_enqueue.emit(url, save_dir)
        self.update_series_panel_counts()
        if self.series_panel is not None:
            self.series_panel.set_summary(f'정주행 다운로드 큐 등록 완료 ({len(self.series_pending_jobs)}건 대기)')
        self.update_status(f'정주행 다운로드 {len(urls)}건 큐에 추가됨')

    def on_scraper_resolved(self, payload: EpisodePayload):
        self._close_scrape_progress()
        mode = self.active_scraper_mode
        self.active_scraper_mode = None
        if mode == 'view':
            headers_dict = getattr(payload, 'image_headers', {}) or {}
            items = [
                ImageItem('url', f'{payload.title}_{i:03d}', u, remote_url=u, remote_headers=dict(headers_dict))
                for i, u in enumerate(payload.image_urls, 1)
            ]
            package = SourcePackage('scraper', payload.url, items, sibling_base_dir=None, meta={
                'title': payload.title,
                'next_url': payload.next_url,
                'prev_url': payload.prev_url,
                'has_next': getattr(payload, 'has_next', bool(payload.next_url)),
                'has_prev': getattr(payload, 'has_prev', bool(payload.prev_url)),
            })
            restore = self.pending_scraper_restore_state
            self.pending_scraper_restore_state = None
            self.set_source(package, restore)
            self.chapter_toast.show_message(payload.title)
        elif mode == 'download':
            self.download_failures = []
            self._close_download_progress()
            self.download_progress = QProgressDialog('이미지 다운로드 중...', '취소', 0, len(payload.image_urls), self)
            self.download_progress.setWindowTitle(payload.title)
            self.download_progress.setWindowModality(Qt.WindowModality.WindowModal)
            self.download_progress.canceled.connect(self.downloader.request_cancel)
            self.download_progress.show()
            self.update_status(f'총 {len(payload.image_urls)}장 다운로드 시작...')
            self.downloader.request_download.emit(payload, self.pending_download_dir or os.getcwd())

    def on_scraper_failed(self, error: str):
        self._close_scrape_progress()
        self.active_scraper_mode = None
        self.pending_scraper_restore_state = None
        QMessageBox.critical(self, '추출 실패', '페이지 분석 중 오류가 발생했습니다.\n' + error)
        self.update_status('웹 페이지 분석 실패')

    def on_scraper_cancelled(self):
        self._close_scrape_progress()
        self.active_scraper_mode = None
        self.pending_scraper_restore_state = None
        self.update_status('웹 페이지 분석 취소됨')

    def on_download_progress(self, done: int, total: int):
        if self.download_progress is not None:
            self.download_progress.setMaximum(max(total, 0))
            self.download_progress.setValue(done)
        self.update_status(f'다운로드 {done}/{total}')

    def on_download_failed_item(self, url: str, error: str):
        self.download_failures.append((url, error))

    def on_download_finished(self, report: DownloadReport):
        self._close_download_progress()
        if report.cancelled:
            self.update_status('다운로드 취소됨')
            return
        message = '저장 완료:\n' + report.save_dir
        if report.failed_items:
            message += f'\n\n실패 {len(report.failed_items)}건은 건너뛰었습니다.'
        QMessageBox.information(self, '다운로드 완료', message)
        self.update_status('다운로드 완료됨')

    def on_download_cancelled(self, save_dir: str):
        self._close_download_progress()
        self.update_status('다운로드 취소됨')

    def on_download_failed(self, error: str):
        self._close_download_progress()
        QMessageBox.critical(self, '다운로드 오류', error)

    def update_series_panel_counts(self):
        if self.series_panel is None:
            return
        failed_count = len([line for line in self.series_panel.failed_browser.toPlainText().splitlines() if line.strip()])
        current = 1 if self.series_current_job else 0
        self.series_panel.remaining_label.setText(f'남은 작업 수: {len(self.series_pending_jobs) + current}')
        self.series_panel.completed_label.setText(f'완료 목록: {self.series_panel.completed_list.count()}')
        self.series_panel.failed_label.setText(f'실패 로그: {failed_count}')

    def _find_best_episode_match(self, candidates: List[str], episode_no: int) -> Optional[str]:
        if not candidates:
            return None
        exact_patterns = [
            re.compile(rf'(?<!\d)0*{episode_no}(?!\d)'),
            re.compile(rf'(?<!\d)0*{episode_no}\s*화\b', re.IGNORECASE),
            re.compile(rf'(?<!\d)0*{episode_no}\s*회\b', re.IGNORECASE),
        ]
        best_path = None
        best_score = -1
        for path in candidates:
            stem = os.path.splitext(os.path.basename(path))[0]
            score = 0
            if any(p.search(stem) for p in exact_patterns):
                score = 100
            nums = [int(x) for x in re.findall(r'\d+', stem)]
            if episode_no in nums:
                score = max(score, 80 if nums and nums[-1] == episode_no else 60)
            if score > best_score:
                best_score = score
                best_path = path
        return best_path if best_score > 0 else None

    def _find_internal_episode_index(self, episode_no: int) -> Optional[int]:
        if not self.items:
            return None
        target_str = str(int(episode_no))
        best_index = None
        best_score = -1
        for idx, item in enumerate(self.items):
            path_str = item.zip_entry if item.zip_entry else item.path
            if not path_str:
                continue
            parts = [part for part in str(path_str).replace('\\', '/').split('/') if part]
            if not parts:
                continue
            score = -1
            if len(parts) > 1:
                candidates = parts[:-1]
            else:
                candidates = parts
            for depth, part in enumerate(candidates):
                nums = re.findall(r'\d+', part)
                if not nums:
                    continue
                if target_str in nums:
                    score = max(score, 100 - min(depth, 10))
                else:
                    for n in nums:
                        try:
                            if int(n) == episode_no:
                                score = max(score, 90 - min(depth, 10))
                        except Exception:
                            pass
            if score > best_score:
                best_score = score
                best_index = idx
        return best_index if best_score >= 0 else None

    def goto_local_episode(self):
        if not self.current_source or self.current_source.kind not in {'folder', 'zip'}:
            QMessageBox.information(self, '회차 이동', '로컬로 연 작품에서만 회차 이동을 사용할 수 있습니다.')
            return
        episode_no, ok = QInputDialog.getInt(self, '회차 이동', '이동할 회차 번호:', 1, 1, 100000, 1)
        if not ok:
            return

        internal_index = self._find_internal_episode_index(episode_no)
        if internal_index is not None:
            self.restore_view_state(ViewState(top_index=internal_index, offset_in_item=0))
            self.chapter_toast.show_message(f'{episode_no}화 위치로 이동')
            return

        candidates = adjacent_sources(self.current_source.path, self.current_source.kind)
        target = self._find_best_episode_match(candidates, episode_no)
        if not target:
            QMessageBox.information(self, '회차 이동', f'{episode_no}화에 해당하는 폴더, ZIP, 내부 회차를 찾지 못했습니다.')
            return
        self.chapter_toast.show_message(f'{episode_no}화 로딩 중...')
        self.load_source_from_paths([target])

    def on_series_queue_added(self, start_url: str):
        if self.series_panel is not None:
            self.series_panel.set_summary('정주행 다운로드 큐에 추가됨')
            self.series_panel.show()
        self.update_series_panel_counts()
        self.update_status('정주행 다운로드 큐에 추가됨')

    def on_series_job_started(self, start_url: str, series_title: str):
        self.series_current_job = start_url
        try:
            self.series_pending_jobs.remove(start_url)
        except ValueError:
            pass
        if self.series_panel is not None:
            self.series_panel.remove_queue_item(start_url)
            self.series_panel.set_summary(f'정주행 다운로드 시작: {series_title}')
            self.series_panel.set_current_job(f'{series_title} | {start_url}')
            self.series_panel.set_current_episode('에피소드 준비 중...')
            self.series_panel.set_progress(0, 1)
        self.update_series_panel_counts()
        self.update_status(f'정주행 다운로드 시작: {series_title}')

    def on_series_episode_started(self, episode_index: int, title: str, total_images: int):
        if self.series_panel is not None:
            self.series_panel.set_current_episode(f'{episode_index}화 | {title}')
            self.series_panel.set_progress(0, max(total_images, 1))
            self.series_panel.set_summary(f'현재 작업 중: {title}')
        self.update_status(f'정주행 {episode_index}화 처리 중: {title}')

    def on_series_episode_progress(self, episode_index: int, done: int, total: int, title: str):
        if self.series_panel is not None:
            self.series_panel.set_current_episode(f'{episode_index}화 | {title}')
            self.series_panel.set_progress(done, total)
            self.series_panel.set_summary(f'{title} | {done}/{total}')
        self.update_status(f'정주행 {episode_index}화 다운로드 {done}/{total}')

    def on_series_episode_finished(self, episode_index: int, title: str, episode_dir: str, image_count: int):
        if self.series_panel is not None:
            self.series_panel.add_completed(f'{episode_index}화 | {title} | {image_count}장')
            self.series_panel.set_current_episode(f'{episode_index}화 완료 | {title}')
        self.update_series_panel_counts()
        self.update_status(f'정주행 {episode_index}화 저장 완료: {title}')

    def on_series_info(self, text: str):
        if self.series_panel is not None:
            self.series_panel.set_summary(text)
        self.update_status(text)

    def on_series_finished(self, report: SeriesDownloadReport):
        summary = f'정주행 저장 완료: {report.series_title} / {report.episode_count}화\n저장 위치: {report.root_dir}'
        if report.failed_episodes:
            summary += f'\n실패 또는 누락: {len(report.failed_episodes)}건'
        self.series_current_job = None
        self.series_download_summaries.append(summary)
        if self.series_panel is not None:
            self.series_panel.set_summary(summary)
            self.series_panel.set_current_job('없음')
            self.series_panel.set_current_episode('없음')
            self.series_panel.set_progress(1, 1)
            self.series_panel.add_completed(f'{report.series_title} | 총 {report.episode_count}화 완료')
            for item in report.failed_episodes:
                self.series_panel.add_failed_log(f'[{report.series_title}] {item}')
        self.update_series_panel_counts()
        self.update_status(f'정주행 다운로드 완료: {report.series_title}')
        QMessageBox.information(self, '정주행 다운로드 완료', summary)

    def on_series_cancelled(self, root_dir: str):
        if self.series_panel is not None:
            if self.series_current_job:
                self.series_panel.add_failed_log(f'[취소됨] {self.series_current_job}\n저장 위치: {root_dir}')
            self.series_panel.set_summary(f'정주행 다운로드 취소됨\n{root_dir}')
            self.series_panel.set_current_job('없음')
            self.series_panel.set_current_episode('없음')
            self.series_panel.set_progress(0, 1)
        self.series_current_job = None
        self.update_series_panel_counts()
        self.update_status('정주행 다운로드 취소됨')

    def on_series_failed(self, error: str):
        if self.series_panel is not None:
            self.series_panel.add_failed_log(f'[오류] {error}')
            self.series_panel.set_summary('정주행 다운로드 오류 발생')
        QMessageBox.critical(self, '정주행 다운로드 오류', error)
        self.update_series_panel_counts()
        self.update_status('정주행 다운로드 오류')

    def _clear_active_widgets(self):
        while self.active_layout.count():
            item = self.active_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.active_cells.clear()
        self.active_range = (-1, -1)

    def set_source(self, package: SourcePackage, restore_state: Optional[ViewState] = None):
        self._cancel_view_loading()
        old_path = self.current_source.path if self.current_source else self.previous_source_path
        self.current_source = package
        self.previous_source_path = package.path
        self.items = list(package.items)
        self.item_heights = [self._estimated_height_from_hint((800, 1200)) for _ in self.items]
        self.size_hints.clear()
        self.loaded_levels.clear()
        self.full_indexes.clear()
        self.thumb_indexes.clear()
        self.full_cache.clear()
        self.thumb_cache.clear()
        self.requested_full.clear()
        self.requested_thumb.clear()
        self._clear_active_widgets()
        self.top_spacer.setFixedHeight(0)
        self.bottom_spacer.setFixedHeight(0)

        if len(self.items) > 5000:
            self.columns = 1
        self.generation += 1
        self.loader.request_generation.emit(self.generation)
        entry = {'kind': package.kind, 'path': package.path}
        if package.kind == 'scraper' and package.meta.get('title'):
            entry['title'] = package.meta.get('title')
        self.state.add_recent_source(entry)
        self.update_status(f'열림: {len(self.items)}장 | source={package.kind} | fit={self.fit_mode} | zoom={self.zoom_percent}%')
        self._maybe_show_source_popup(package, old_path)
        if restore_state:
            QTimer.singleShot(10, partial(self.restore_view_state, restore_state))
        else:
            self.scroll_area.verticalScrollBar().setValue(0)
            self.schedule_visible_refresh(delay_ms=10)

    def _display_name_for_source(self, package: SourcePackage) -> str:
        base = os.path.basename(package.path.rstrip('/\\'))
        if package.kind == 'zip' and base.lower().endswith('.zip'):
            base = os.path.splitext(base)[0]
        return base or package.path

    def _maybe_show_source_popup(self, package: SourcePackage, old_path: Optional[str]):
        if package.kind not in {'folder', 'zip', 'scraper', 'files'}:
            return
        if old_path == package.path:
            return
        title = self._display_name_for_source(package)
        message = f'다음 화: {title}' if old_path else title
        self.chapter_toast.show_message(message)

    def _fit_action_text(self):
        return '맞춤: 리더폭' if self.fit_mode == 'width' else '맞춤: 창폭'

    def _auto_next_action_text(self):
        return '자동 다음 화: 켬' if self.auto_next_enabled else '자동 다음 화: 끔'

    def toggle_auto_next(self):
        self.auto_next_enabled = not self.auto_next_enabled
        self.state.update_settings({'auto_next_enabled': self.auto_next_enabled})
        if hasattr(self, 'auto_next_action') and self.auto_next_action is not None:
            self.auto_next_action.setText(self._auto_next_action_text())
        state_text = '켜짐' if self.auto_next_enabled else '꺼짐'
        self.update_status(f'자동 다음 화 {state_text}')

    def update_zoom_label(self):
        self.zoom_label.setText(f'{self.zoom_percent}%')

    def set_zoom_percent(self, value: int):
        value = max(30, min(200, int(value)))
        if value == self.zoom_percent:
            return
        self.zoom_percent = value
        self.state.update_settings({'zoom_percent': self.zoom_percent})
        self.update_zoom_label()
        self.requested_thumb.clear()
        self.requested_full.clear()
        self.schedule_visible_refresh(delay_ms=10)
        self.update_status(f'확대 비율: {self.zoom_percent}%')

    def adjust_zoom(self, delta_percent: int):
        self.set_zoom_percent(self.zoom_percent + delta_percent)

    def zoom_in(self):
        self.adjust_zoom(10)

    def zoom_out(self):
        self.adjust_zoom(-10)

    def reset_zoom(self):
        self.set_zoom_percent(100)

    def set_layout_columns(self, columns: int):
        if columns != 1 and len(self.items) > 2000:
            self.columns = 1
            self.update_status('대용량 소스는 1열만 허용됩니다.')
            return
        self.columns = max(1, min(2, columns))
        if self.columns != 1:
            self.update_status('현재 버전의 대용량 최적화는 1열에 집중되어 있습니다. 2열은 소형 소스에서만 권장됩니다.')
        self.schedule_visible_refresh(delay_ms=10)

    def close_images(self):
        self._cancel_view_loading()
        self.current_source = None
        self.previous_source_path = None
        self.items = []
        self.item_heights = []
        self.size_hints.clear()
        self.loaded_levels.clear()
        self.full_cache.clear()
        self.thumb_cache.clear()
        self.full_indexes.clear()
        self.thumb_indexes.clear()
        self._clear_active_widgets()
        self.top_spacer.setFixedHeight(0)
        self.bottom_spacer.setFixedHeight(0)
        self.update_status('이미지 닫힘')

    def _target_display_width(self) -> int:
        viewport_width = max(320, self.scroll_area.viewport().width())
        base = viewport_width - 24
        if self.fit_mode == 'width':
            base = min(base, self.reader_max_width)
        width = int(base * (self.zoom_percent / 100.0))
        return max(180, width)

    def _estimated_height_from_hint(self, size_hint: Tuple[int, int]) -> int:
        w, h = size_hint
        w = max(1, int(w or 1))
        h = max(1, int(h or 1))
        display_w = self._target_display_width()
        return max(80, int(display_w * (h / w)))

    def _cum_height_to_index(self, index: int) -> int:
        return sum(self.item_heights[:max(0, index)])

    def _find_indexes_by_scroll(self, top: int, bottom: int) -> Tuple[int, int]:
        if not self.items:
            return 0, -1
        cur = 0
        start = 0
        end = len(self.items) - 1
        found_start = False
        for idx, h in enumerate(self.item_heights):
            nxt = cur + h
            if not found_start and nxt >= top:
                start = idx
                found_start = True
            if cur <= bottom:
                end = idx
            else:
                break
            cur = nxt
        return start, end

    def visible_index_window(self):
        if not self.items:
            return 0, -1, 0, -1, 0, -1
        viewport_top = self.scroll_area.verticalScrollBar().value()
        viewport_bottom = viewport_top + self.scroll_area.viewport().height()
        visible_start, visible_end = self._find_indexes_by_scroll(viewport_top, viewport_bottom)
        preload = int(self.state.get_setting('preload_rows', 6))
        release = int(self.state.get_setting('release_rows', 18))
        load_start = max(0, visible_start - preload)
        load_end = min(len(self.items) - 1, visible_end + preload)
        release_start = max(0, visible_start - release)
        release_end = min(len(self.items) - 1, visible_end + release)
        return visible_start, visible_end, load_start, load_end, release_start, release_end

    def schedule_visible_refresh(self, delay_ms: Optional[int] = None):
        delay = int(self.state.get_setting('refresh_debounce_ms', 40)) if delay_ms is None else delay_ms
        self.update_timer.start(delay)

    def target_render_size(self):
        width = self._target_display_width()
        viewport_height = max(320, self.scroll_area.viewport().height())
        return width, max(2400, viewport_height * 4)

    def request_priority(self, index: int, thumb_only: bool, visible_start: int, visible_end: int, center_idx: int, item: ImageItem) -> int:
        source_bias = 2000 if item.source_type == 'url' else 0
        thumb_bias = 1000 if thumb_only else 0
        outside_bias = 150 if not (visible_start <= index <= visible_end) else 0
        return source_bias + thumb_bias + outside_bias + abs(index - center_idx)

    def build_load_request(self, index: int, item: ImageItem, target_width: int, target_height: int, thumb_only: bool, visible_start: int, visible_end: int, center_idx: int) -> LoadRequest:
        return LoadRequest(
            generation=self.generation,
            index=index,
            item=item,
            target_width=target_width,
            target_height=target_height,
            thumb_only=thumb_only,
            priority=self.request_priority(index, thumb_only, visible_start, visible_end, center_idx, item),
        )

    def _cache_put(self, cache: OrderedDict, idx: int, value, max_size: int):
        cache[idx] = value
        cache.move_to_end(idx)
        while len(cache) > max_size:
            old_idx, _ = cache.popitem(last=False)
            if cache is self.full_cache:
                self.full_indexes.discard(old_idx)
            else:
                self.thumb_indexes.discard(old_idx)

    def _cache_get(self, cache: OrderedDict, idx: int):
        if idx not in cache:
            return None
        value = cache[idx]
        cache.move_to_end(idx)
        return value

    def _rebuild_active_widgets(self, start: int, end: int, target_width: int):
        if self.active_range == (start, end):
            for idx, cell in self.active_cells.items():
                cell.display_max_width = target_width
                cell.setFixedWidth(target_width)
            return
        self._clear_active_widgets()
        self.active_range = (start, end)
        top_height = self._cum_height_to_index(start)
        active_height = sum(self.item_heights[start:end + 1]) if end >= start else 0
        total_height = sum(self.item_heights)
        bottom_height = max(0, total_height - top_height - active_height)
        self.top_spacer.setFixedHeight(max(0, top_height))
        self.bottom_spacer.setFixedHeight(max(0, bottom_height))
        for idx in range(start, end + 1):
            cell = ImageCell(idx)
            cell.item = self.items[idx]
            cell.display_max_width = target_width
            cell.setFixedWidth(target_width)
            if idx in self.full_cache:
                img, size_hint = self._cache_get(self.full_cache, idx)
                self._apply_to_cell(cell, img, 'full', size_hint, target_width)
            elif idx in self.thumb_cache:
                img, size_hint = self._cache_get(self.thumb_cache, idx)
                self._apply_to_cell(cell, img, 'thumb', size_hint, target_width)
            else:
                cell.set_placeholder(self.items[idx].display_name)
                cell.setFixedWidth(target_width)
                cell.setFixedHeight(self.item_heights[idx])
            self.active_cells[idx] = cell
            self.active_layout.addWidget(cell, 0, Qt.AlignmentFlag.AlignHCenter)

    def refresh_visible_window(self):
        if not self.current_source or not self.items:
            return
        if self.columns != 1:
            self.columns = 1
        visible_start, visible_end, load_start, load_end, release_start, release_end = self.visible_index_window()
        target_width, target_height = self.target_render_size()
        self._rebuild_active_widgets(release_start, release_end, target_width)
        center_idx = (visible_start + visible_end) // 2

        requests: List[LoadRequest] = []
        for idx in range(load_start, load_end + 1):
            item = self.items[idx]
            thumb_only = not (visible_start <= idx <= visible_end)
            desired_level = 'thumb' if thumb_only else 'full'
            key = (idx, target_width, target_height, self.zoom_percent)
            if desired_level == 'full' and idx in self.full_cache:
                continue
            if desired_level == 'thumb' and (idx in self.thumb_cache or idx in self.full_cache):
                continue
            if thumb_only:
                if key in self.requested_thumb:
                    continue
                self.requested_thumb.add(key)
            else:
                if key in self.requested_full:
                    continue
                self.requested_full.add(key)
            requests.append(self.build_load_request(idx, item, target_width, target_height, thumb_only, visible_start, visible_end, center_idx))

        if requests:
            requests.sort(key=lambda req: (req.priority, req.index))
            self.loader.request_enqueue.emit(requests)

        self.update_scroll_status('위치')

    def _apply_to_cell(self, cell: ImageCell, img: QImage, level: str, size_hint: Tuple[int, int], target_width: int):
        pixmap = QPixmap.fromImage(img)
        cell.set_display_pixmap(pixmap)
        cell.display_max_width = target_width
        cell.setFixedWidth(target_width)
        cell.source_size = size_hint
        cell.loaded_level = level
        cell.last_render_key = (target_width, img.height(), self.fit_mode, level, self.zoom_percent)

    def _apply_image_common(self, idx: int, img: QImage, level: str, size_hint: Tuple[int, int]):
        self.size_hints[idx] = size_hint
        estimated = self._estimated_height_from_hint(size_hint)
        self.item_heights[idx] = estimated
        self.loaded_levels[idx] = level
        if level == 'full':
            self._cache_put(self.full_cache, idx, (img, size_hint), self.MAX_FULL_CACHE)
            self.full_indexes.add(idx)
            self.thumb_indexes.add(idx)
        else:
            self._cache_put(self.thumb_cache, idx, (img, size_hint), self.MAX_THUMB_CACHE)
            self.thumb_indexes.add(idx)
        if idx in self.active_cells:
            self._apply_to_cell(self.active_cells[idx], img, level, size_hint, self._target_display_width())
        self.schedule_visible_refresh(delay_ms=5)

    def on_thumb_loaded(self, gen: int, idx: int, img: QImage, size_hint):
        if gen != self.generation or gen in self.cancelled_generations:
            return
        if idx in self.full_cache:
            return
        self._apply_image_common(idx, img, 'thumb', size_hint)

    def on_full_loaded(self, gen: int, idx: int, img: QImage, size_hint):
        if gen != self.generation or gen in self.cancelled_generations:
            return
        self._apply_image_common(idx, img, 'full', size_hint)

    def on_load_failed(self, gen: int, idx: int, error: str):
        if gen != self.generation or gen in self.cancelled_generations:
            return
        if idx in self.active_cells:
            self.active_cells[idx].set_error(f'로드 실패\n{error}')

    def on_load_progress(self, gen: int, done: int, total: int):
        if gen != self.generation or gen in self.cancelled_generations:
            return
        self.update_scroll_status(f'로딩 {done}/{total}')

    def _cancel_view_loading(self):
        cancelled_any = False
        if self.generation > 0:
            self.cancelled_generations.add(self.generation)
            self.loader.request_cancel.emit(self.generation)
            cancelled_any = True
        if self.resolve_token > 0:
            self.cancelled_resolve_tokens.add(self.resolve_token)
            self.resolver.request_cancel.emit(self.resolve_token)
            cancelled_any = True
        if self.scrape_progress is not None:
            self.scraper.request_cancel.emit()
            cancelled_any = True
        self.requested_full.clear()
        self.requested_thumb.clear()
        if cancelled_any:
            self.update_status('현재 보기 로딩 취소됨')

    def cancel_loading(self):
        self._cancel_view_loading()
        if self.download_progress is not None:
            self.downloader.request_cancel.emit()
        if self.series_current_job:
            self.series_downloader.request_cancel.emit()
        self.update_status('로딩 취소됨')

    def on_loader_cancel_ack(self, generation: int):
        self.cancelled_generations.add(generation)

    def scroll_by(self, delta: int):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + delta)))

    def toggle_slideshow(self):
        self.slide_show_enabled = not self.slide_show_enabled
        if self.slide_show_enabled:
            self.start_slideshow()
        else:
            self.stop_slideshow()

    def _slideshow_interval(self) -> int:
        return {
            1: int(self.state.get_setting('slide_interval_level_1', 120)),
            2: int(self.state.get_setting('slide_interval_level_2', 60)),
            3: int(self.state.get_setting('slide_interval_level_3', 30)),
        }[self.slide_speed_level]

    def set_slideshow_speed(self, level: int):
        new_level = max(1, min(3, int(level)))
        if new_level == self.slide_speed_level and self.slide_show_enabled:
            self.update_status(f'슬라이드쇼 속도 {self.slide_speed_level}단계')
            return
        self.slide_speed_level = new_level
        if self.slide_show_enabled:
            self.slide_timer.start(self._slideshow_interval())
        self.update_status(f'슬라이드쇼 속도 {self.slide_speed_level}단계')

    def start_slideshow(self):
        self.slide_timer.start(self._slideshow_interval())
        self.update_status(f'슬라이드쇼 속도 {self.slide_speed_level}단계')

    def stop_slideshow(self):
        self.slide_timer.stop()
        self.slide_show_enabled = False
        self.update_status('슬라이드쇼 중지')

    def slide_step(self):
        step = int(self.state.get_setting('slide_step_px', 28))
        self.scroll_by(step)

        # 💡 [추가] 슬라이드쇼 중 맨 아래 도달 시 다음 화로 이동
        if self._check_auto_next(step):
            pass

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def toggle_fit_mode(self):
        self.fit_mode = 'window' if self.fit_mode == 'width' else 'width'
        self.state.update_settings({'fit_mode': self.fit_mode})
        self.fit_action.setText(self._fit_action_text())
        self.requested_thumb.clear()
        self.requested_full.clear()
        self.schedule_visible_refresh(delay_ms=10)
        self.update_status(f'맞춤 모드: {self._fit_action_text()} | 확대 {self.zoom_percent}%')

    def current_view_state(self) -> ViewState:
        if not self.items:
            return ViewState(0, 0)
        top_scroll = self.scroll_area.verticalScrollBar().value()
        cur = 0
        for idx, h in enumerate(self.item_heights):
            if cur + h >= top_scroll:
                return ViewState(idx, max(0, top_scroll - cur))
            cur += h
        return ViewState(len(self.items) - 1, 0)

    def restore_view_state(self, state: ViewState):
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, state.top_index))
        target = self._cum_height_to_index(index) + max(0, state.offset_in_item)
        self.scroll_area.verticalScrollBar().setValue(target)
        self.schedule_visible_refresh(delay_ms=10)

    def goto_ratio(self):
        if not self.items:
            return
        value, ok = QInputDialog.getInt(self, '비율 이동', '이동할 비율(0~100):', 0, 0, 100, 1)
        if ok:
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(int(bar.maximum() * (value / 100.0)))
            self.update_status(f'{value}% 위치로 이동')

    def open_settings(self):
        dialog = SettingsDialog(self.state, self)
        if dialog.exec():
            values = dialog.values()
            self.state.update_settings(values)
            self.reader_max_width = int(values.get('reader_max_width', self.reader_max_width))
            self.zoom_percent = int(values.get('zoom_percent', self.zoom_percent))
            self.update_zoom_label()
            self.apply_theme(values['theme'])
            self.schedule_visible_refresh(10)
            self.update_status('설정 적용 완료')

    def open_recent_dialog(self):
        entries = self.state.list_recent_sources()
        if not entries:
            QMessageBox.information(self, '최근 열기', '최근 기록이 없습니다.')
            return
        dialog = RecentDialog(entries, self)
        if dialog.exec() and dialog.selected_entry:
            entry = dialog.selected_entry
            if entry['kind'] in {'folder', 'zip', 'files'}:
                if entry['kind'] == 'files':
                    self.load_source_from_paths(entry['path'].split(';'))
                else:
                    self.load_source_from_paths([entry['path']])
            elif entry['kind'] == 'scraper':
                self.load_source_scraper(entry['path'])
            else:
                self.load_source_remote(entry['path'])

    def save_bookmark(self):
        if not self.current_source:
            QMessageBox.information(self, '알림', '저장할 상태가 없습니다.')
            return
        name, ok = QInputDialog.getText(self, '책갈피 저장', '이름:')
        if not ok or not name.strip():
            return
        state = self.current_view_state()
        self.state.upsert_bookmark(Bookmark(name.strip(), self.current_source.path, self.current_source.kind, self.columns, self.fit_mode, self.sort_mode, state.top_index, state.offset_in_item))
        self.update_status(f'책갈피 저장: {name.strip()}')

    def open_bookmark(self):
        bookmarks = self.state.list_bookmarks()
        if not bookmarks:
            QMessageBox.information(self, '알림', '저장된 책갈피가 없습니다.')
            return
        names = [b.name for b in bookmarks]
        name, ok = QInputDialog.getItem(self, '책갈피 열기', '선택:', names, 0, False)
        if not ok:
            return
        bookmark = next((b for b in bookmarks if b.name == name), None)
        if not bookmark:
            return
        self.columns = bookmark.columns
        self.fit_mode = bookmark.fit_mode
        self.sort_mode = bookmark.sort_mode
        restore = ViewState(bookmark.top_index, bookmark.offset_in_item)
        if bookmark.source_kind in {'folder', 'zip', 'files'}:
            if bookmark.source_kind == 'files':
                self.load_source_from_paths(bookmark.source_path.split(';'), restore)
            else:
                self.load_source_from_paths([bookmark.source_path], restore)
        elif bookmark.source_kind == 'scraper':
            self.load_source_scraper(bookmark.source_path, restore)
        else:
            self.load_source_remote(bookmark.source_path, restore)

    def _extract_zip_internal_chapters(self) -> List[Tuple[str, int]]:
        if not self.current_source or self.current_source.kind != 'zip' or not self.items:
            return []

        chapters: List[Tuple[str, int]] = []
        seen: Set[str] = set()

        for idx, item in enumerate(self.items):
            entry = (item.zip_entry or '').replace('\\', '/').strip('/')
            if not entry:
                continue

            parts = [p for p in entry.split('/') if p]
            if len(parts) < 2:
                continue

            folder_candidates = parts[:-1]
            chapter_name = None
            for part in reversed(folder_candidates):
                if re.search(r'\d+', part):
                    chapter_name = part
                    break
            if chapter_name is None:
                chapter_name = folder_candidates[-1]

            if chapter_name not in seen:
                seen.add(chapter_name)
                chapters.append((chapter_name, idx))

        def _chapter_sort_key(row: Tuple[str, int]):
            name, _ = row
            nums = re.findall(r'\d+', name)
            episode_no = int(nums[-1]) if nums else 10**9
            return (episode_no, name.lower())

        chapters.sort(key=_chapter_sort_key)
        return chapters

    def _current_internal_chapter_pos(self, chapters: List[Tuple[str, int]]) -> Optional[int]:
        if not chapters or not self.items:
            return None

        state = self.current_view_state()
        current_idx = state.top_index
        pos = 0
        for i, (_, first_idx) in enumerate(chapters):
            if first_idx <= current_idx:
                pos = i
            else:
                break
        return pos

    # 웹툰/로컬 소스의 이전 화/다음 화 이동
    def open_adjacent_source(self, direction: int, auto: bool = False):
        if self.scrape_progress is not None or self.resolve_token > 0:
            return

        if self.current_source and self.current_source.kind == 'scraper':
            meta = self.current_source.meta or {}
            target_url = meta.get('next_url') if direction == 1 else meta.get('prev_url')
            has_target = bool(meta.get('has_next')) if direction == 1 else bool(meta.get('has_prev'))
            toast = "다음 화 로딩 중..." if direction == 1 else "이전 화 로딩 중..."
            if target_url and not str(target_url).lower().startswith('javascript:'):
                self.chapter_toast.show_message(toast)
                self.load_source_scraper(str(target_url))
            elif has_target and self.current_source.path:
                self.chapter_toast.show_message(toast)
                self._start_scraper(self.current_source.path, mode='view', nav_direction=direction)
            else:
                msg = '다음 화' if direction == 1 else '이전 화'
                if auto:
                    self.update_status(f'{msg}를 찾지 못했습니다.')
                else:
                    QMessageBox.information(self, '알림', f'{msg}를 찾지 못했습니다.')
            return

        if not self.current_source or self.current_source.kind not in {'folder', 'zip'}:
            return

        if self.current_source.kind == 'zip':
            chapters = self._extract_zip_internal_chapters()
            if len(chapters) >= 2:
                current_pos = self._current_internal_chapter_pos(chapters)
                if current_pos is not None:
                    next_pos = current_pos + direction
                    if 0 <= next_pos < len(chapters):
                        chapter_name, first_idx = chapters[next_pos]
                        self.chapter_toast.show_message(f"{'다음' if direction == 1 else '이전'} 화: {chapter_name}")
                        self.restore_view_state(ViewState(top_index=first_idx, offset_in_item=0))
                        return
                    if auto:
                        self.update_status(f"{'다음' if direction == 1 else '이전'} 화를 찾지 못했습니다.")
                        return

        siblings = adjacent_sources(self.current_source.path, self.current_source.kind)
        if not siblings:
            if not auto:
                QMessageBox.information(self, '알림', '이전/다음 화를 찾을 수 있는 형제 폴더나 ZIP이 없습니다.')
            return

        current_path = self.current_source.path
        target_path = None

        if current_path in siblings:
            idx = siblings.index(current_path) + direction
            if 0 <= idx < len(siblings):
                target_path = siblings[idx]
        elif self.current_source.kind == 'folder':
            idx = 0 if direction > 0 else len(siblings) - 1
            if 0 <= idx < len(siblings):
                target_path = siblings[idx]

        if target_path:
            self.chapter_toast.show_message("다음 화 로딩 중..." if direction == 1 else "이전 화 로딩 중...")
            self.load_source_from_paths([target_path])
        elif not auto:
            msg = '다음 화' if direction == 1 else '이전 화'
            QMessageBox.information(self, '알림', f'{msg} 파일을 찾지 못했습니다.')

    def next_page(self):
        step = self.scroll_area.viewport().height() - 40
        # 💡 [추가] 엔터키/PageDown으로 맨 아래 도달 시 다음 화 자동 이동
        if self._check_auto_next(step):
            return
        self.scroll_by(step)

    def prev_page(self):
        self.scroll_by(-(self.scroll_area.viewport().height() - 40))

    def closeEvent(self, event):
        self.state.update_settings({'fit_mode': self.fit_mode, 'reader_max_width': self.reader_max_width, 'zoom_percent': self.zoom_percent, 'auto_next_enabled': self.auto_next_enabled})
        self.loader.shutdown()
        self.resolver.shutdown()
        self.scraper.shutdown()
        self.downloader.shutdown()
        self.series_downloader.shutdown()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
