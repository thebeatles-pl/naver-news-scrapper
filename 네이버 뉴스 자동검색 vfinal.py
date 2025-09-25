import sys
import json
import traceback
import requests
import os
import html
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextBrowser, QLabel, QMessageBox,
    QTabWidget, QInputDialog, QComboBox, QFileDialog, QSystemTrayIcon,
    QMenu, QStyle, QTabBar, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import QThread, QObject, pyqtSignal, pyqtSlot, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon, QAction

# --- 설정 파일 경로 ---
CONFIG_FILE = "news_scraper_config.json"

# --- API 키 입력 다이얼로그 ---
class ApiKeyDialog(QDialog):
    """사용자로부터 Naver API 키를 입력받기 위한 별도의 대화창 클래스입니다."""
    def __init__(self, parent=None, current_id="", current_secret=""):
        super().__init__(parent)
        self.setWindowTitle("네이버 API 키 설정")
        self.setModal(True)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        info_label = QLabel(
            "네이버 뉴스 API 사용을 위해 Client ID와 Client Secret을 입력해주세요.\n"
            "<a href='https://developers.naver.com/apps'>네이버 개발자 센터에서 발급받을 수 있습니다.</a>"
        )
        info_label.setOpenExternalLinks(True)
        layout.addWidget(info_label)

        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel("Client ID:"))
        self.id_input = QLineEdit(current_id)
        self.id_input.setPlaceholderText("Client ID를 여기에 붙여넣으세요")
        id_layout.addWidget(self.id_input)
        layout.addLayout(id_layout)

        secret_layout = QHBoxLayout()
        secret_layout.addWidget(QLabel("Client Secret:"))
        self.secret_input = QLineEdit(current_secret)
        self.secret_input.setPlaceholderText("Client Secret을 여기에 붙여넣으세요")
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        secret_layout.addWidget(self.secret_input)
        layout.addLayout(secret_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_keys(self):
        return self.id_input.text().strip(), self.secret_input.text().strip()

# --- 백그라운드 API 요청을 위한 Worker 클래스 ---
class Worker(QObject):
    """UI 멈춤 현상 없이 네트워크 요청을 처리하기 위한 스레드 작업자 클래스입니다."""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, keyword, exclude_keywords, client_id, client_secret):
        super().__init__()
        self.keyword = keyword
        self.exclude_keywords = exclude_keywords
        self.session = requests.Session()
        self.session.headers.update({
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        })

    @pyqtSlot()
    def run(self):
        try:
            news_items = self.fetch_naver_news(self.keyword)
            self.finished.emit(news_items)
        except Exception as e:
            detailed_error = traceback.format_exc()
            error_message = f"오류가 발생했습니다: {e}\n\n--- 상세 정보 ---\n{detailed_error}"
            self.error.emit(error_message)

    def fetch_naver_news(self, keyword):
        api_url = "https://openapi.naver.com/v1/search/news.json"
        params = {"query": keyword, "display": 100, "sort": "date"}
        response = self.session.get(api_url, params=params)

        if response.status_code != 200:
            raise Exception(f"API 호출 실패: {response.status_code} - {response.text}")

        response_json = response.json()
        processed_news = []
        for item in response_json.get("items", []):
            title = html.unescape(item.get('title', '')).replace('<b>', '').replace('</b>', '')
            description = html.unescape(item.get('description', '')).replace('<b>', '').replace('</b>', '')
            if self.exclude_keywords and any(ex in title or ex in description for ex in self.exclude_keywords):
                continue
            processed_news.append({
                'title': title,
                'link': item.get('originallink', item.get('link', '')),
                'description': description,
                'pubDate': item.get('pubDate', '')
            })
        return processed_news

# --- 메인 애플리케이션 윈도우 클래스 ---
class NewsScraperApp(QMainWindow):
    """
    실시간 네이버 뉴스 검색 애플리케이션 v8.0 (Refactored)

    아키텍처 원칙: 단방향 데이터 흐름 (Unidirectional Data Flow)
    1.  **중앙 데이터 모델 (Single Source of Truth):**
        -   애플리케이션의 모든 상태(뉴스, 북마크, 읽음 여부 등)는 메인 클래스의 속성
            (self.tab_data, self.bookmarked_news, self.read_links)에서 중앙 관리됩니다.
    2.  **UI는 데이터의 반영:**
        -   UI(화면)는 오직 중앙 데이터 모델의 상태를 기반으로 그려집니다.
    3.  **상태 변경과 UI 업데이트:**
        -   사용자 행동(예: 북마크 클릭)은 중앙 데이터 모델을 변경하는 함수를 호출합니다.
        -   데이터 모델이 변경되면, 해당 데이터에 의존하는 모든 UI 컴포넌트를
            처음부터 다시 그리는 'redraw' 함수를 호출하여 화면을 갱신합니다.
        -   이를 통해 데이터와 화면의 불일치 문제를 원천적으로 방지하고 예측 가능한 앱 상태를 유지합니다.
    """
    def __init__(self):
        super().__init__()
        # --- 1. 중앙 데이터 모델 (Single Source of Truth) ---
        self.client_id = ""
        self.client_secret = ""
        self.read_links = set()
        self.bookmarked_news = []
        self.tab_data = {} # 각 탭의 뉴스 데이터: {'탭 이름': [news_items]}

        # --- 백그라운드 작업을 위한 멤버 변수 ---
        self.thread = None
        self.worker = None

        self.setWindowTitle("실시간 뉴스 검색 (네이버 API) v8.0")
        self.setGeometry(100, 100, 900, 750)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))

        self.init_ui()
        self.init_tray_icon()
        QTimer.singleShot(100, self.post_init_setup) # UI가 그려진 후 설정 로드 및 초기화 실행

    def post_init_setup(self):
        """UI가 완전히 초기화된 후 실행되어야 하는 작업들을 처리합니다."""
        self.load_config()
        if not self.client_id or not self.client_secret:
            self.prompt_for_api_keys()
        self.setup_auto_refresh()
        if self.tab_widget.count() > 1: # 기본 탭 외에 다른 탭이 있다면
            self.tab_widget.setCurrentIndex(1)
            self.start_fetching()
        self.redraw_bookmark_tab()

    def init_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.tray_icon.setToolTip("실시간 뉴스 검색")
        tray_menu = QMenu()
        show_action, quit_action = QAction("열기", self), QAction("종료", self)
        show_action.triggered.connect(self.showNormal)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addActions([show_action, quit_action])
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def show_notification(self, keyword, count):
        self.tray_icon.showMessage('새 뉴스 알림', f"'{keyword}'에 {count}개의 새로운 뉴스가 도착했습니다.", QSystemTrayIcon.MessageIcon.Information, 3000)

    def init_ui(self):
        # 스타일시트 설정 (가독성 및 미관 개선)
        self.setStyleSheet("""
            QMainWindow { background-color: #F0F2F5; }
            QLabel { font-family: '맑은 고딕'; font-size: 10pt; }
            QPushButton { font-family: '맑은 고딕'; font-size: 10pt; background-color: #FFFFFF; color: #333; padding: 8px 12px; border-radius: 6px; border: 1px solid #DCDCDC; }
            QPushButton:hover { background-color: #E8E8E8; }
            QPushButton#AddTab { font-weight: bold; background-color: #007AFF; color: white; border: none; }
            QPushButton#AddTab:hover { background-color: #0056b3; }
            QComboBox { font-family: '맑은 고딕'; font-size: 10pt; padding: 5px; border-radius: 6px; border: 1px solid #ccc; }
            QTextBrowser { font-family: '맑은 고딕'; background-color: #FFFFFF; border: 1px solid #DCDCDC; border-radius: 8px; }
            QTabWidget::pane { border-top: 1px solid #DCDCDC; }
            QTabBar::tab { font-family: '맑은 고딕'; font-size: 10pt; color: #333; padding: 10px 15px; border: 1px solid transparent; border-bottom: none; background-color: transparent; }
            QTabBar::tab:selected { background-color: #FFFFFF; border-color: #DCDCDC; border-top-left-radius: 6px; border-top-right-radius: 6px; color: #000; font-weight: bold; }
            QTabBar::tab:!selected { color: #777; }
            QTabBar::tab:!selected:hover { color: #333; }
            QTabBar::close-button { padding: 2px; }
            QLineEdit { font-family: '맑은 고딕'; font-size: 10pt; padding: 5px 8px; border-radius: 6px; border: 1px solid #ccc; }
        """)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # 상단 제어판 레이아웃
        control_layout = QHBoxLayout()
        self.refresh_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload), " 새로고침")
        self.export_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), " 결과 저장")
        self.api_settings_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton), " API 설정")
        self.open_config_folder_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon), " 설정 폴더")
        self.add_tab_button = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon), "+ 새 탭 추가")
        self.add_tab_button.setObjectName("AddTab")
        self.refresh_interval_combo = QComboBox()
        self.refresh_interval_combo.addItems(["10분", "30분", "1시간", "3시간", "6시간", "자동 새로고침 안함"])

        control_layout.addWidget(self.refresh_button)
        control_layout.addWidget(self.export_button)
        control_layout.addWidget(self.api_settings_button)
        control_layout.addWidget(self.open_config_folder_button)
        control_layout.addStretch(1)
        control_layout.addWidget(QLabel("자동 새로고침:"))
        control_layout.addWidget(self.refresh_interval_combo)
        control_layout.addWidget(self.add_tab_button)

        # 탭 위젯 설정
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabBar().setMovable(True)
        self.create_bookmark_tab() # 다른 탭보다 먼저 북마크 탭 생성

        # 메인 레이아웃에 위젯 추가
        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.tab_widget)
        self.statusBar().showMessage("준비 완료.")

        # 시그널과 슬롯 연결
        self.refresh_button.clicked.connect(self.start_fetching)
        self.export_button.clicked.connect(self.export_results)
        self.api_settings_button.clicked.connect(self.prompt_for_api_keys)
        self.open_config_folder_button.clicked.connect(self.open_config_folder)
        self.add_tab_button.clicked.connect(self.add_new_tab)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.refresh_interval_combo.currentIndexChanged.connect(self.update_refresh_interval)
        self.tab_widget.tabBar().tabBarDoubleClicked.connect(self.rename_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

    def open_config_folder(self):
        config_path = os.path.abspath(CONFIG_FILE)
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(config_dir))

    def prompt_for_api_keys(self):
        dialog = ApiKeyDialog(self, self.client_id, self.client_secret)
        if dialog.exec():
            new_id, new_secret = dialog.get_keys()
            if new_id and new_secret:
                self.client_id, self.client_secret = new_id, new_secret
                self.save_config()
                QMessageBox.information(self, "성공", "API 키가 성공적으로 저장되었습니다.")
                self.start_fetching()
            else:
                QMessageBox.warning(self, "경고", "Client ID와 Secret 모두 입력해야 합니다.")

    def on_tab_changed(self, index):
        self.refresh_button.setDisabled(index == 0) # 북마크 탭에서는 새로고침 비활성화
        tab_content = self.tab_widget.widget(index)
        if tab_content and hasattr(tab_content, 'new_links') and tab_content.new_links:
            tab_content.new_links.clear()
            # 탭에 (N) 표시가 있으면 원래 제목으로 되돌림
            if self.tab_widget.tabText(index) != tab_content.original_title:
                self.tab_widget.setTabText(index, tab_content.original_title)
            # 데이터를 다시 렌더링하여 'New' 배지 등을 업데이트
            self.render_tab_content(tab_content)

    def setup_auto_refresh(self):
        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self.refresh_all_tabs_auto)
        self.update_refresh_interval()

    def refresh_all_tabs_auto(self):
        """백그라운드에서 모든 뉴스 탭을 자동으로 새로고침합니다."""
        if self.tab_widget.count() <= 1: return
        
        self.statusBar().showMessage("모든 탭 자동 새로고침 중...")
        for i in range(1, self.tab_widget.count()):
            self.start_fetching(is_auto=True, target_index=i)

    def update_refresh_interval(self):
        if not hasattr(self, 'auto_refresh_timer'): return
        self.auto_refresh_timer.stop()
        current_text = self.refresh_interval_combo.currentText()
        if "안함" in current_text:
            self.statusBar().showMessage("자동 새로고침이 비활성화되었습니다.")
            return
        
        interval_map = {"분": 60 * 1000, "시간": 60 * 60 * 1000}
        try:
            value, unit = int(current_text[:-1]), current_text[-1]
            milliseconds = value * interval_map[unit]
            self.auto_refresh_timer.start(milliseconds)
            self.statusBar().showMessage(f"모든 탭 자동 새로고침 간격이 {current_text}으로 설정되었습니다.")
        except (ValueError, KeyError):
            self.statusBar().showMessage("자동 새로고침 간격 설정 오류.")


    def _parse_keywords(self, text):
        """ '키워드 -제외어1 -제외어2' 형식의 문자열을 파싱합니다. """
        parts = [p.strip() for p in text.split('-') if p.strip()]
        return parts[0], parts[1:] if len(parts) > 1 else []

    def load_config(self):
        if not os.path.exists(CONFIG_FILE): return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            app_settings = config.get("app_settings", {})
            self.client_id = app_settings.get("client_id", "")
            self.client_secret = app_settings.get("client_secret", "")
            refresh_index = app_settings.get("refresh_interval_index", 2)
            if 0 <= refresh_index < self.refresh_interval_combo.count():
                self.refresh_interval_combo.setCurrentIndex(refresh_index)
            self.read_links = set(config.get("read_links", []))
            self.bookmarked_news = config.get("bookmarks", [])
            for keyword in config.get("tabs", []):
                self.create_tab(keyword)
        except (json.JSONDecodeError, KeyError) as e:
            QMessageBox.critical(self, "설정 파일 오류", f"설정 파일을 불러오는 중 오류가 발생했습니다: {e}\n기본 설정으로 시작합니다.")

    def save_config(self):
        try:
            tabs_to_save = [
                widget.original_title
                for i in range(1, self.tab_widget.count())
                if (widget := self.tab_widget.widget(i)) and hasattr(widget, 'original_title')
            ]

            config = {
                "app_settings": {
                    "refresh_interval_index": self.refresh_interval_combo.currentIndex(),
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                "tabs": tabs_to_save,
                "read_links": list(self.read_links),
                "bookmarks": self.bookmarked_news
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.statusBar().showMessage(f"설정 파일 저장 오류: {e}")

    def create_bookmark_tab(self):
        tab_content = self.create_tab_content_widget()
        tab_content.original_title = "북마크"
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon)
        index = self.tab_widget.insertTab(0, tab_content, icon, "북마크")
        self.tab_widget.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, None)

    def create_tab_content_widget(self):
        tab_content_widget = QWidget()
        layout = QVBoxLayout(tab_content_widget)
        layout.setContentsMargins(0, 10, 0, 0)
        
        # 탭 내부 상단 UI (필터, 정렬, 업데이트 시간 등)
        top_bar_layout = QHBoxLayout()
        filter_input = QLineEdit(placeholderText="결과 내에서 필터링...")
        sort_combo = QComboBox()
        sort_combo.addItems(["최신순", "오래된순"])
        mark_all_read_button = QPushButton("모두 읽음으로")
        last_updated_label = QLabel("업데이트 대기 중")

        top_bar_layout.addWidget(filter_input)
        top_bar_layout.addWidget(sort_combo)
        top_bar_layout.addWidget(mark_all_read_button)
        top_bar_layout.addStretch()
        top_bar_layout.addWidget(last_updated_label)
        
        # 뉴스 내용이 표시될 브라우저
        browser = QTextBrowser(openExternalLinks=False)
        browser.anchorClicked.connect(self.handle_link_click)
        
        layout.addLayout(top_bar_layout)
        layout.addWidget(browser)
        
        # 각 위젯에 쉽게 접근할 수 있도록 QWidget의 속성으로 저장
        tab_content_widget.browser = browser
        tab_content_widget.filter_input = filter_input
        tab_content_widget.last_updated_label = last_updated_label
        tab_content_widget.sort_combo = sort_combo
        tab_content_widget.new_links = set()
        tab_content_widget.original_title = ""

        # 필터링, 정렬, 모두 읽음 버튼의 동작 연결
        filter_input.textChanged.connect(lambda: self.render_tab_content(tab_content_widget))
        sort_combo.currentIndexChanged.connect(lambda: self.render_tab_content(tab_content_widget))
        mark_all_read_button.clicked.connect(lambda: self.mark_all_as_read(tab_content_widget))
        return tab_content_widget
    
    def mark_all_as_read(self, tab_content):
        keyword = tab_content.original_title
        source_data = self.tab_data.get(keyword, []) if keyword != "북마크" else self.bookmarked_news
        
        count = 0
        for item in source_data:
            if item['link'] not in self.read_links:
                self.read_links.add(item['link'])
                count += 1
        
        if count > 0:
            self.render_tab_content(tab_content) # UI 갱신
            self.statusBar().showMessage(f"{count}개 기사를 읽음으로 처리했습니다.")

    def create_tab(self, keyword):
        self.tab_data[keyword] = [] # 데이터 모델에 새 키워드 공간 생성
        tab_content = self.create_tab_content_widget()
        tab_content.original_title = keyword
        index = self.tab_widget.addTab(tab_content, keyword)
        self.tab_widget.setCurrentIndex(index)
        return tab_content

    def handle_link_click(self, url):
        """ QTextBrowser 내에서 클릭된 링크를 처리하는 중앙 핸들러 """
        scheme = url.scheme()
        
        # 앱 내부 동작을 위한 커스텀 스킴 'app://' 처리
        if scheme == 'app':
            action = url.host()
            # URL Path의 첫 '/'를 제거한 후 data로 사용
            data = url.path().lstrip('/') 

            if action == 'unread':
                if data in self.read_links: self.read_links.remove(data)
                self.redraw_current_tab()
            elif action == 'toggle_bookmark':
                try:
                    # 1. URL로 전달된 인코딩된 문자열을 원래대로 디코딩
                    decoded_news_str = urllib.parse.unquote(data)
                    # 2. 디코딩된 JSON 문자열을 파이썬 딕셔너리 객체로 변환
                    news_item = json.loads(decoded_news_str)
                    # 3. 데이터 모델을 변경하는 함수 호출
                    self.toggle_bookmark(news_item)
                except (json.JSONDecodeError, TypeError) as e:
                    # [개선] 오류 발생 시 사용자에게 명확한 피드백 제공
                    error_details = f"북마크 데이터를 처리하는 중 오류가 발생했습니다.\n\n오류: {e}\n\n데이터: {data[:100]}..."
                    QMessageBox.critical(self, "북마크 오류", error_details)
        
        # 일반 웹 링크 처리
        else:
            url_string = url.toString()
            self.read_links.add(url_string) # 읽음 목록에 추가
            QDesktopServices.openUrl(url)
            self.redraw_current_tab() # '읽음' 상태를 반영하기 위해 현재 탭 다시 그리기

    def toggle_bookmark(self, news_item_to_toggle):
        """
        [상태 변경] 북마크 데이터 모델을 직접 수정하고 UI 갱신을 트리거합니다.
        """
        link_to_toggle = news_item_to_toggle.get('link')
        if not link_to_toggle: return

        is_bookmarked = any(item['link'] == link_to_toggle for item in self.bookmarked_news)
        
        # 1. 중앙 데이터 모델 (self.bookmarked_news) 변경
        if is_bookmarked:
            self.bookmarked_news = [item for item in self.bookmarked_news if item['link'] != link_to_toggle]
        else:
            self.bookmarked_news.insert(0, news_item_to_toggle)
        
        # 2. 데이터 변경 후, 이 데이터에 의존하는 모든 UI를 다시 그리도록 요청
        self.redraw_all_tabs()

    def redraw_all_tabs(self):
        """북마크 상태 변경 후 모든 탭을 다시 그려 데이터-UI 일관성을 유지합니다."""
        for i in range(self.tab_widget.count()):
            if tab_content := self.tab_widget.widget(i):
                self.render_tab_content(tab_content)

    def redraw_current_tab(self):
        if current_tab := self.tab_widget.currentWidget():
            self.render_tab_content(current_tab)

    def redraw_bookmark_tab(self):
        if bookmark_tab := self.tab_widget.widget(0):
            self.render_tab_content(bookmark_tab)

    def render_tab_content(self, tab_content):
        """
        [UI 렌더링] 주어진 탭 위젯의 화면을 중앙 데이터 모델로부터 다시 그립니다.
        이 함수가 UI 렌더링의 핵심입니다.
        """
        if not tab_content: return

        keyword = tab_content.original_title
        is_bookmark_tab = (keyword == "북마크")

        # 1. 데이터 소스 결정 (북마크 탭인가, 일반 검색 탭인가?)
        source_data = self.bookmarked_news if is_bookmark_tab else self.tab_data.get(keyword, [])

        # 2. 필터링 및 정렬
        filter_text = tab_content.filter_input.text().lower()
        sort_order = tab_content.sort_combo.currentText()
        
        filtered_items = [
            item for item in source_data
            if not filter_text or filter_text in item['title'].lower() or filter_text in item['description'].lower()
        ]
        
        # 날짜 문자열을 datetime 객체로 변환하여 정렬 (안정성 강화)
        def get_date(item):
            try:
                return parsedate_to_datetime(item['pubDate'])
            except:
                return datetime.min

        display_items = sorted(filtered_items, key=get_date, reverse=(sort_order == '최신순'))

        # 3. HTML 렌더링
        self.render_html(tab_content, display_items)

    def rename_tab(self, index):
        if index == 0: return # 북마크 탭은 이름 변경 불가
        
        tab_content = self.tab_widget.widget(index)
        old_name = tab_content.original_title

        text, ok = QInputDialog.getText(self, '탭 이름 변경', '새 키워드를 입력하세요:', text=old_name)
        if ok and text and text != old_name:
            # 데이터 모델의 키를 변경
            if old_name in self.tab_data:
                self.tab_data[text] = self.tab_data.pop(old_name)
            
            # UI 위젯 정보 업데이트
            tab_content.original_title = text
            self.tab_widget.setTabText(index, text)
            self.start_fetching(target_index=index)

    def add_new_tab(self):
        text, ok = QInputDialog.getText(self, '새 탭 추가', '검색 키워드를 입력하세요 (예: 네이버)')
        if ok and text:
            # 이미 있는 탭인지 확인
            for i in range(1, self.tab_widget.count()):
                if self.tab_widget.widget(i).original_title == text:
                    self.tab_widget.setCurrentIndex(i)
                    return
            self.create_tab(text)
            self.start_fetching()

    def close_tab(self, index):
        if index == 0: return # 북마크 탭은 닫기 불가
        if widget := self.tab_widget.widget(index):
            keyword_to_delete = widget.original_title
            if keyword_to_delete in self.tab_data:
                del self.tab_data[keyword_to_delete]
            widget.deleteLater()
        self.tab_widget.removeTab(index)

    def start_fetching(self, is_auto=False, target_index=None):
        if not self.client_id or not self.client_secret:
            if not is_auto:
                QMessageBox.warning(self, "API 키 필요", "뉴스 검색을 위해 'API 설정' 메뉴에서 키를 먼저 설정해주세요.")
            self.statusBar().showMessage("API 키가 설정되지 않았습니다.")
            return

        current_idx = self.tab_widget.currentIndex() if target_index is None else target_index
        if current_idx < 1: return

        tab_content = self.tab_widget.widget(current_idx)
        if not tab_content: return

        keyword_text = tab_content.original_title
        keyword, exclude_keywords = self._parse_keywords(keyword_text)
        
        # 현재 활성화된 탭을 새로고침하는 경우에만 UI 변경
        if not is_auto:
            self.refresh_button.setEnabled(False)
            status_message = f"'{keyword}' 뉴스를 검색 중입니다..."
            self.statusBar().showMessage(status_message)
            tab_content.browser.setHtml(f"<div style='padding: 20px; text-align: center; color: #888;'>{status_message}</div>")
        
        self.thread = QThread()
        self.worker = Worker(keyword, exclude_keywords, self.client_id, self.client_secret)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(lambda items, tc=tab_content, ia=is_auto: self.update_results(items, tc, ia))
        self.worker.error.connect(self.handle_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def update_results(self, news_items, target_tab_content, is_auto):
        tab_key = target_tab_content.original_title
        target_index = self.tab_widget.indexOf(target_tab_content)

        if target_index < 0: # 탭이 그 사이에 닫혔을 경우
            if not is_auto: self.refresh_button.setEnabled(True)
            return

        if is_auto:
            main_keyword, _ = self._parse_keywords(tab_key)
            previous_links = {item['link'] for item in self.tab_data.get(tab_key, [])}
            new_links_set = {item['link'] for item in news_items}
            truly_new_links = new_links_set - previous_links
            
            if truly_new_links:
                self.show_notification(main_keyword, len(truly_new_links))
                target_tab_content.new_links.update(truly_new_links)
                if self.tab_widget.currentIndex() != target_index:
                    new_count = len(target_tab_content.new_links)
                    self.tab_widget.setTabText(target_index, f"{tab_key} ({new_count})")
        
        self.tab_data[tab_key] = news_items
        
        # 현재 보이는 탭인 경우에만 즉시 렌더링
        if self.tab_widget.currentIndex() == target_index:
            self.render_tab_content(target_tab_content)
            target_tab_content.last_updated_label.setText(f"업데이트: {datetime.now().strftime('%H:%M:%S')}")
        
        if not is_auto or self.tab_widget.currentIndex() == target_index:
            self.refresh_button.setEnabled(True)
    
    def _create_news_item_html(self, news, keyword, is_bookmark_tab, new_links, bookmarked_links):
        is_read = news['link'] in self.read_links
        is_bookmarked = news['link'] in bookmarked_links
        is_new = news['link'] in new_links

        background_color = "#F8F9FA" if is_read else "#FFFFFF"
        opacity = "0.7" if is_read else "1.0"
        
        title_prefix = "⭐ " if is_bookmarked else ""
        title_html = html.escape(news['title'])
        desc_html = html.escape(news['description'])
        if keyword:
            highlight = f"<span style='background-color: #FFF3CD;'>{keyword}</span>"
            title_html = title_html.replace(keyword, highlight)
            desc_html = desc_html.replace(keyword, highlight)

        new_badge = "<span style='font-size: 8pt; color: white; background-color: #DC3545; padding: 2px 5px; border-radius: 4px; margin-left: 8px;'>New</span>" if is_new else ""
        
        try:
            formatted_date = parsedate_to_datetime(news['pubDate']).strftime('%Y-%m-%d %H:%M')
        except: formatted_date = "날짜 정보 없음"

        # [핵심] 북마크 링크 생성:
        # 1. 뉴스 아이템(딕셔너리)을 JSON 문자열로 변환합니다.
        news_json = json.dumps(news, ensure_ascii=False)
        # 2. URL에 포함될 수 있도록 특수문자를 인코딩합니다. (e.g., " " -> %20)
        encoded_news = urllib.parse.quote(news_json)
        # 3. 'app://' 스킴을 사용하여 앱 내부 동작임을 명시하는 링크를 만듭니다.
        bookmark_url = f"app://toggle_bookmark/{encoded_news}"

        actions = ""
        if is_read:
            actions += f"<a href='app://unread/{news['link']}' style='font-size: 9pt; color: #6C757D; text-decoration: none; margin-left: 10px;'>[안 읽음으로]</a>"
        
        bookmark_text = "[북마크 삭제]" if is_bookmark_tab or is_bookmarked else "[북마크]"
        bookmark_color = "#DC3545" if is_bookmark_tab or is_bookmarked else "#007BFF"
        actions += f"<a href='{bookmark_url}' style='font-size: 9pt; color: {bookmark_color}; text-decoration: none; margin-left: 10px;'>{bookmark_text}</a>"

        return f"""
        <div style="opacity: {opacity}; border: 1px solid #E9ECEF; border-radius: 8px; padding: 15px; margin-bottom: 10px; background-color: {background_color};">
            <div style="margin-bottom: 5px;">
                <span style="font-size: 12pt; font-weight: bold; color: #212529;">{title_prefix}{title_html}</span>
            </div>
            <div style="font-size: 9pt; color: #6C757D; margin-bottom: 8px;">{formatted_date}{new_badge}</div>
            <a href="{news['link']}" style="font-size: 9pt; color: #007BFF; text-decoration: none; word-break: break-all;">{news['link']}</a>
            <span style="float: right;">{actions}</span>
            <p style="font-size: 10pt; color: #495057; margin-top: 10px; line-height: 1.6;">{desc_html}</p>
        </div>"""

    def render_html(self, tab_content, news_items):
        browser = tab_content.browser
        keyword = tab_content.original_title
        is_bookmark_tab = (keyword == "북마크")
        
        search_keyword = "" if is_bookmark_tab else self._parse_keywords(keyword)[0]
        new_links = getattr(tab_content, 'new_links', set())
        bookmarked_links = {item['link'] for item in self.bookmarked_news}

        if not news_items:
            msg = "북마크된 기사가 없습니다." if is_bookmark_tab else "표시할 뉴스 기사가 없습니다."
            browser.setHtml(f"<div style='padding: 20px; text-align: center; color: #888;'>{msg}</div>")
            return
            
        html_content = "<body style='margin: 5px;'>" + "".join(
            [self._create_news_item_html(news, search_keyword, is_bookmark_tab, new_links, bookmarked_links) for news in news_items]
        ) + "</body>"
        browser.setHtml(html_content)

    def export_results(self):
        current_index = self.tab_widget.currentIndex()
        if current_index < 0: return
        
        tab_content = self.tab_widget.widget(current_index)
        keyword = tab_content.original_title
        source_data = self.bookmarked_news if keyword == "북마크" else self.tab_data.get(keyword, [])
        
        if not source_data:
            QMessageBox.information(self, "알림", "저장할 뉴스 데이터가 없습니다.")
            return

        default_filename = f"{keyword.replace(' ', '_').replace('-', '_')}_뉴스_{datetime.now().strftime('%Y%m%d')}.txt"
        
        filepath, _ = QFileDialog.getSaveFileName(self, "결과 저장", default_filename, "Text Files (*.txt)")
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    for i, news in enumerate(source_data, 1):
                        f.write(f"[{i}] {news['title']}\n")
                        f.write(f"  - 링크: {news['link']}\n")
                        f.write(f"  - 요약: {news['description']}\n\n")
                self.statusBar().showMessage(f"'{os.path.basename(filepath)}' 파일로 저장 완료")
            except Exception as e:
                QMessageBox.critical(self, "저장 오류", f"파일 저장 중 오류 발생: {e}")

    def handle_error(self, error_message):
        QMessageBox.critical(self, "오류 발생", f"뉴스 검색 중 오류가 발생했습니다.\n\nAPI 키가 정확한지, 하루 사용량을 초과하지 않았는지 확인해주세요.\n\n{error_message}")
        self.statusBar().showMessage("오류 발생. 대기 중")
        self.refresh_button.setEnabled(True)

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    main_window = NewsScraperApp()
    main_window.show()
    sys.exit(app.exec())
