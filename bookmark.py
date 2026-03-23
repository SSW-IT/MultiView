import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path.home() / '.multi_image_viewer'
APP_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = APP_DIR / 'state.json'

@dataclass
class Bookmark:
    name: str
    source_path: str
    source_kind: str
    columns: int
    fit_mode: str
    sort_mode: str
    top_index: int
    offset_in_item: int

DEFAULT_STATE: Dict[str, Any] = {
    'bookmarks': [],
    'recent_sources': [],
    'settings': {
        'theme': 'dark',
        'preload_rows': 3,
        'release_rows': 8,
        'wheel_step': 80,
        'remember_last_session': True,
        'fit_mode': 'width',
        'slide_interval_level_1': 120,
        'slide_interval_level_2': 60,
        'slide_interval_level_3': 30,
        'refresh_debounce_ms': 75,
    },
}

class AppState:
    def __init__(self) -> None:
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not STATE_FILE.exists():
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return json.loads(json.dumps(DEFAULT_STATE))

        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged.update({k: v for k, v in data.items() if k in merged and k != 'settings'})
        merged['settings'].update(data.get('settings', {}))
        return merged

    def save(self) -> None:
        STATE_FILE.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding='utf-8')

    def get_setting(self, key: str, default: Optional[Any] = None) -> Any:
        return self._state['settings'].get(key, default)

    def update_settings(self, new_settings: Dict[str, Any]) -> None:
        self._state['settings'].update(new_settings)
        self.save()

    def list_bookmarks(self) -> List[Bookmark]:
        result: List[Bookmark] = []
        for item in self._state.get('bookmarks', []):
            try:
                result.append(Bookmark(**item))
            except TypeError:
                continue
        return result

    def upsert_bookmark(self, bookmark: Bookmark) -> None:
        bookmarks = self._state.get('bookmarks', [])
        bookmarks = [b for b in bookmarks if b.get('name') != bookmark.name]
        bookmarks.append(asdict(bookmark))
        bookmarks.sort(key=lambda x: x['name'].lower())
        self._state['bookmarks'] = bookmarks
        self.save()

    def delete_bookmark(self, name: str) -> None:
        self._state['bookmarks'] = [b for b in self._state.get('bookmarks', []) if b.get('name') != name]
        self.save()

    def rename_bookmark(self, old_name: str, new_name: str) -> None:
        for item in self._state.get('bookmarks', []):
            if item.get('name') == old_name:
                item['name'] = new_name
        self._state['bookmarks'].sort(key=lambda x: x['name'].lower())
        self.save()

    def add_recent_source(self, entry: Dict[str, str], limit: int = 15) -> None:
        recents = self._state.get('recent_sources', [])
        recents = [r for r in recents if not (r.get('path') == entry.get('path') and r.get('kind') == entry.get('kind'))]
        recents.insert(0, entry)
        self._state['recent_sources'] = recents[:limit]
        self.save()

    def list_recent_sources(self) -> List[Dict[str, str]]:
        return list(self._state.get('recent_sources', []))