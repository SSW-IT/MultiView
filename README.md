# MultiView (Advanced Webtoon Viewer & Downloader)
[🇺🇸 Read in English](README(EN).md)

<img width="276" height="298" alt="화면 캡처 2026-03-23 195346" src="https://github.com/user-attachments/assets/343b3791-d39e-4fd7-b182-0a43235d6a67" />
<img width="1274" height="951" alt="화면 캡처 2026-03-23 195314" src="https://github.com/user-attachments/assets/a931a350-812a-4202-8344-08f99ae83db6" />

**로컬 ZIP 파일 감상부터 웹툰 정주행 자동 다운로드까지 지원하는 고성능 멀티 이미지 뷰어입니다.**

## 💡 핵심 기능 요약
- Non-blocking 백그라운드 다운로드:** 정주행 다운로드 중에도 메인 뷰어에서 다른 작품 열람 및 조작 가능.
- 가상화 스크롤 렌더링:** 4,000장 이상의 거대 ZIP 파일도 메모리 누수 없이 실시간 로딩 및 해제 (Virtual Viewport 적용).
- Anti-Bot 회피 스크래핑:** Playwright 기반 브라우저 핑거프린트 위장 및 Lazy Loading 완벽 파훼.
- 스마트 회차 탐색:** 1개의 거대 ZIP 파일 내부에 병합된 회차라도 숫자로 검색하여 즉시 스크롤 워프(Jump).

## 주요 기능
- 로컬 폴더 / ZIP 파일 열기
- 웹툰 주소 열기
- 정주행 다운로드
- 정주행 다운로드 관리자 UI
  - 큐 목록
  - 남은 작업 수
  - 완료 목록
  - 실패 로그
  - 프로그레스 바
  - 비모달 진행창
- 백그라운드 다운로드 중 다른 웹툰 계속 보기
- 이전 화 / 다음 화 이동
- 회차 검색 및 이동
- 확대 / 축소 / 맞춤 전환
- 슬라이드쇼
- 책갈피 저장 / 복원
- F1 도움말

## 📌 프로젝트 소개

### 어떤 프로그램인가요?
**MultiView**는 파편화된 웹툰 감상 환경을 하나로 통합한 데스크톱 애플리케이션입니다. 로컬에 저장된 이미지/ZIP/폴더를 끊김 없는(Gapless) 세로 스크롤로 감상할 수 있으며, 원격 웹툰 URL을 입력하여 실시간으로 보거나 백그라운드에서 전체 회차를 자동 다운로드할 수 있습니다.

### 왜 만들었나요?
기존 뷰어 프로그램들은 단순히 로컬 파일을 여는 데 그치거나, 대용량 ZIP 파일을 열 때 메모리 폭주로 프로그램이 멈추는(Freezing) 문제가 잦았습니다. 또한, 웹툰 사이트의 Anti-Bot 솔루션과 지연 로딩(Lazy Loading)으로 인해 이미지 다운로드 시 누락이 발생하는 문제를 근본적으로 해결하고자 개발했습니다.

### 핵심 문제를 어떻게 해결했나요?
1. **대용량 파일 메모리 문제:** `QThread` 기반의 비동기 로딩을 구현하고, 현재 화면에 보이는 가시 영역(및 프리로드 영역)의 이미지만 `QImage`로 메모리에 올린 뒤, 멀어진 이미지는 즉각 해제하도록 가상화 렌더링을 설계했습니다.
2. **웹툰 다운로드 차단 및 누락 문제:** 단순 HTML 파싱(`requests`) 대신 `Playwright`를 도입하여 실제 브라우저 환경을 모방(Fingerprint Spoofing)했습니다. 자바스크립트로 직접 스크롤(`window.scrollBy`)을 발생시켜 지연 로딩을 100% 트리거한 뒤 이미지를 추출합니다.
3. **UI 멈춤 현상(Blocking):** 뷰어 로딩(Loader)과 정주행 엔진(SeriesDownloader)을 물리적인 스레드로 완전히 격리하여, 수백 화를 다운로드하는 동안에도 메인 UI가 전혀 버벅거리지 않도록 설계했습니다.

## 프로젝트 구조

- `main.py` : 프로그램 시작점
- `viewer.py` : 메인 UI, 단축키, 뷰어 동작
- `loader.py` : 로컬 폴더 / ZIP 이미지 소스 로딩
- `downloader.py` : 현재 화 다운로드, 정주행 다운로드
- `scraper_bridge.py` : 웹툰 페이지 해석 및 회차 추적
- `bookmark.py` : 책갈피 저장 / 불러오기

## 실행 환경

- Python 3.10 이상 권장
- Windows 기준 개발 및 테스트
- Miniconda 또는 venv 사용 권장

## 설치 방법

```bash
pip install -r requirements.txt
playwright install
python main.py
```

## 핵심 단축키

- `Ctrl+L` : 로컬 열기
- `Ctrl+R` : Remote 열기
- `Ctrl+Shift+R` : 웹툰 주소 열기
- `Ctrl+Alt+D` 또는 구현 버전에 따른 현재 화 다운로드 단축키 : 현재 화 다운로드
- `Ctrl+Shift+D` : 정주행 다운로드
- `Ctrl+Shift+Q` : 정주행 관리자 열기
- `Ctrl+S` : 책갈피 저장
- `Ctrl+B` : 책갈피 열기
- `Ctrl+Shift+B` : 책갈피 관리
- `Ctrl+G` : 비율 이동
- `Ctrl+J` : 회차 검색 및 이동
- `Ctrl+F` : 맞춤 전환
- `Ctrl+.` : 로딩 취소
- `[` / `]` : 이전 화 / 다음 화
- `PageDown` / `Enter` / `PageUp` : 다음 / 이전 페이지
- `Space` : 슬라이드쇼 On/Off
- `Up` / `Down` : 스크롤 또는 슬라이드쇼 속도 변경
- `Esc` : 슬라이드쇼 중지 + 최소화
- `F1` : 도움말
- `F11` : 전체화면

## 구현 포인트

- QThread 기반 비동기 로딩
- 현재 화면 근처 이미지 우선 로드
- 멀어진 이미지는 해제하여 메모리 절약
- 썸네일 우선, 가시 영역은 전체 품질 요청
- 로딩 취소 지원
- Remote 입력 지원
  - 단일 이미지 URL
  - URL 목록
  - HTTP directory index
  - UNC 폴더
- 웹툰 URL에서 회차 추적 및 정주행 다운로드 지원
- 다운로드 중에도 다른 작품을 계속 열람 가능

## EXE 빌드 예시

```bash
pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name "MultiView" ^
  --icon=app.ico ^
  --collect-all PySide6 ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --hidden-import PySide6.QtNetwork ^
  --hidden-import requests ^
  main.py
```

## 배포 절차

1. 가상환경에서 프로그램이 정상 실행되는지 확인
2. `requirements.txt` 최신화
3. `playwright install` 실행
4. PyInstaller로 `dist/MultiView` 생성
5. `dist/MultiView/MultiView.exe` 실행 테스트
6. 로컬 ZIP / 폴더 / 웹툰 URL / 정주행 다운로드 기능 점검
7. 이상 없으면 `dist/MultiView` 폴더 전체 압축
8. 배포본과 소스코드를 분리 관리

## 테스트 체크리스트

- 로컬 폴더 열기
- ZIP 파일 열기
- ZIP 내부 회차 이동
- 웹툰 주소 열기
- 현재 화 다운로드
- 정주행 다운로드
- 다운로드 중 다른 작품 열기
- 책갈피 저장 / 복원
- F1 도움말 표시
- 종료 후 재실행 안정성

## GitHub 업로드 권장 구성

```text
MultiView/
├─ main.py
├─ viewer.py
├─ loader.py
├─ downloader.py
├─ scraper_bridge.py
├─ bookmark.py
├─ requirements.txt
├─ README.md
├─ README.txt
├─ .gitignore
└─ docs/
   └─ screenshots/
```

## GitHub 업로드 순서

```bash
git init
git add .
git commit -m "Initial commit: MultiView webtoon viewer"
git branch -M main
git remote add origin https://github.com/<your-id>/MultiView.git
git push -u origin main
```

## 주의사항

- 웹툰 사이트 구조가 바뀌면 일부 URL 파싱 로직은 수정이 필요할 수 있습니다.
- Playwright 기반 기능은 브라우저 리소스 설치 상태에 영향을 받습니다.
- GitHub에는 `dist/`, `build/`, 가상환경 폴더를 올리지 않는 것을 권장합니다.

⚠️ Disclaimer (면책 조항)
본 프로그램(MultiView)은 기술 학습 및 프로그래밍 연구 목적으로 개발된 오픈소스 도구입니다.
저작권이 있는 콘텐츠를 원작자나 권리자의 허락 없이 무단으로 다운로드, 배포, 상업적 이용을 하는 것은 저작권법에 의해 엄격히 금지되어 있습니다.
본 도구의 사용으로 인해 발생하는 모든 법적 책임은 전적으로 사용자 본인에게 있으며, 개발자는 어떠한 책임도 지지 않습니다. 반드시 합법적인 용도와 개인적인 소장 목적으로만 사용해 주시기 바랍니다.
