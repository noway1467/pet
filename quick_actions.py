"""按需创建的原生快捷面板：本地收藏筛选 + 显式网页搜索，无扫描、定时器或常驻线程。"""
import os
from pathlib import Path
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QEvent, QSize, QRect, QUrl
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QLabel, QLineEdit, QPushButton, QToolButton, QComboBox,
    QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QStyledItemDelegate,
    QStyle, QFileDialog, QMessageBox, QInputDialog, QMenu,
)

import config

MAX_ITEMS = 80
ENGINES = {
    'bing': ('必应', 'https://www.bing.com/search', 'q'),
    'baidu': ('百度', 'https://www.baidu.com/s', 'wd'),
    'google': ('Google', 'https://www.google.com/search', 'q'),
}


def builtin_entries():
    windows = os.environ.get('SystemRoot', r'C:\Windows')
    return [
        {'id': 'builtin-notepad', 'name': '记事本', 'path': os.path.join(windows, 'System32', 'notepad.exe'),
         'kind': 'app', 'keywords': 'notepad jishiben jsb 文本'},
        {'id': 'builtin-calculator', 'name': '计算器', 'path': os.path.join(windows, 'System32', 'calc.exe'),
         'kind': 'app', 'keywords': 'calculator calc jisuanqi jsq'},
        {'id': 'builtin-explorer', 'name': '文件资源管理器', 'path': os.path.join(windows, 'explorer.exe'),
         'kind': 'app', 'keywords': 'explorer wenjian wj 文件'},
    ]


def normalize_entries(value):
    """配置只保存有界的已知字段；读取时不查磁盘，失效项也保留给用户修复。"""
    if value is None:
        value = builtin_entries()
    if not isinstance(value, list):
        return []
    result, seen_ids, seen_paths = [], set(), set()
    for item in value[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name, path = item.get('name'), item.get('path')
        if not isinstance(name, str) or not isinstance(path, str) or not name.strip() or not path.strip():
            continue
        path = path.strip()
        key = os.path.normcase(os.path.normpath(path))
        if key in seen_paths or len(path) > 32760 or '\x00' in path:
            continue
        identity = str(item.get('id') or uuid.uuid4().hex)[:80]
        if identity in seen_ids:
            identity = uuid.uuid4().hex
        keywords = item.get('keywords', '')
        result.append({'id': identity, 'name': name.strip()[:80], 'path': path,
                       'kind': 'folder' if item.get('kind') == 'folder' else 'app',
                       'keywords': keywords[:160] if isinstance(keywords, str) else ''})
        seen_ids.add(identity)
        seen_paths.add(key)
    return result


def filter_entries(entries, query):
    words = query.casefold().split()
    return [entry for entry in entries if all(
        word in (entry['name'] + ' ' + entry['keywords'] + ' ' + os.path.basename(entry['path'])).casefold()
        for word in words)]


def search_url(query, engine='bing'):
    query = query.strip()
    if not query:
        raise ValueError('先输入要搜索的内容。')
    if len(query) > 2048:
        raise ValueError('搜索内容过长，请缩短到 2048 字以内。')
    _, base, parameter = ENGINES.get(engine, ENGINES['bing'])
    return base + '?' + urlencode({parameter: query})


def validate_target(path, kind='app'):
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
    # 不解析任意网址/命令，也不访问网络共享，避免名称查询拖慢 UI。
    if path.startswith(('\\\\', '//')) or '\x00' in path:
        raise ValueError('请选择本机程序或文件夹，不支持网络共享路径。')
    if kind == 'folder':
        if not os.path.isdir(path):
            raise ValueError('找不到这个文件夹，请在管理中重新添加。')
    else:
        if Path(path).suffix.casefold() not in ('.exe', '.lnk'):
            raise ValueError('请选择 .exe 程序或 .lnk 快捷方式，不支持命令脚本。')
        if not os.path.isfile(path):
            raise ValueError('找不到这个程序，请在管理中重新添加。')
    return path


def launch_entry(entry):
    path = validate_target(entry['path'], entry['kind'])
    # 交给系统打开确切路径，不拼接 shell 命令；空格、中文和 & 不会变成额外命令。
    if os.name == 'nt':
        os.startfile(os.path.normpath(path), 'open')
        return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


PANEL_STYLE = """
QDialog#QuickPanel { background: transparent; }
QFrame#quickSurface { background: #f7fbfe; border: 1px solid #cbdde9; border-radius: 16px; }
QFrame#quickHeader { background: transparent; border: none; }
QLabel { background: transparent; color: #243b53; border: none; }
QLabel#quickTitle { font-size: 20px; font-weight: 600; }
QLabel#quickSubtitle, QLabel#quickCount { color: #617b90; font-size: 12px; }
QLabel#quickBadge { color: #207caa; background: #e6f3fb; border-radius: 10px; font-size: 22px; }
QLabel#quickFooter { color: #617b90; font-size: 11px; }
QLabel#quickError { color: #a63737; background: #fff0ee; padding: 8px; border-radius: 8px; }
QFrame#quickSearchBox { background: white; border: 1px solid #b6d8eb; border-radius: 11px; }
QLineEdit#quickSearch { background: transparent; border: none; color: #243b53; padding: 10px 6px; font-size: 15px; selection-background-color: #c6e8fb; }
QComboBox { background: #edf6fb; color: #315b75; border: none; border-radius: 6px; padding: 5px 9px; min-height: 24px; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: white; color: #243b53; selection-background-color: #e0f0fc; selection-color: #243b53; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #c9dce8; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QPushButton { background: #e8f3fb; color: #315b75; border: 1px solid transparent; border-radius: 7px; padding: 6px 10px; min-width: 0; font-size: 12px; }
QPushButton:hover { background: #d8edfb; }
QPushButton:checked { background: #207caa; color: white; }
QPushButton:focus, QToolButton:focus { border: 1px solid #207caa; }
QPushButton:disabled { color: #9cadb9; background: #edf2f6; }
QToolButton { background: transparent; color: #617b90; border: 1px solid transparent; border-radius: 6px; font-size: 20px; padding: 2px; }
QToolButton:hover { background: #e2eff8; }
"""


class ResultDelegate(QStyledItemDelegate):
    """绘制列表行而不是每条创建一组子控件，也不提取磁盘上的 exe 图标。"""
    def sizeHint(self, option, index):
        return QSize(200, 66)

    def paint(self, painter, option, index):
        item = index.data(Qt.UserRole)
        if not item:
            return
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(0, 3, -3, -3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#dfeffb' if selected else '#edf5fa' if hovered else '#f7fbfe'))
        painter.drawRoundedRect(rect, 9, 9)
        if selected:
            painter.setBrush(QColor('#207caa'))
            painter.drawRoundedRect(QRect(rect.x(), rect.y() + 13, 3, rect.height() - 26), 1, 1)
        badge = QRect(rect.x() + 12, rect.y() + 12, 36, 36)
        painter.setBrush(QColor('#cfe5f4' if selected else '#e6f0f7'))
        painter.drawRoundedRect(badge, 9, 9)
        font = QFont(option.font)
        font.setPixelSize(17)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#27789f'))
        label = '搜' if item['kind'] == 'search' else '夹' if item['kind'] == 'folder' else item['name'][0].upper()
        painter.drawText(badge, Qt.AlignCenter, label)
        text_left, text_width = rect.x() + 61, rect.width() - 133
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#203f56'))
        painter.drawText(QRect(text_left, rect.y() + 9, text_width, 23), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(item['name'], Qt.ElideRight, text_width))
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor('#617b90'))
        detail = item.get('detail', item.get('path', ''))
        painter.drawText(QRect(text_left, rect.y() + 32, text_width, 18), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(detail, Qt.ElideMiddle, text_width))
        if selected:
            painter.setPen(QColor('#397b9c'))
            painter.drawText(QRect(rect.right() - 62, rect.y(), 52, rect.height()), Qt.AlignCenter,
                             '选择' if self.parent().property('managing') else 'Enter ↵')
        painter.restore()


class Header(QFrame):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)


class QuickPanel(QDialog):
    def __init__(self, cfg, parent=None, *, save=None, launch=None, open_url=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName('QuickPanel')
        self.setWindowTitle('快捷启动与搜索')
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.cfg, self._save = cfg, save or config.save
        self._launch, self._open_url = launch or launch_entry, open_url or QDesktopServices.openUrl
        self.entries = normalize_entries(cfg.get('quick_launch_items'))
        self._launching = False
        self.setStyleSheet(PANEL_STYLE)
        self.resize(620, 530)
        self.setMinimumSize(420, 340)
        font = QFont('Microsoft YaHei UI', 10)
        self.setFont(font)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        surface = QFrame(self)
        surface.setObjectName('quickSurface')
        outer.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(12)
        header = Header(surface)
        header.setObjectName('quickHeader')
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        badge = QLabel('↗', header)
        badge.setObjectName('quickBadge')
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(badge)
        text = QVBoxLayout()
        title = QLabel('快捷启动与搜索', header)
        title.setObjectName('quickTitle')
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        subtitle = QLabel('程序、文件夹与网页搜索', header)
        subtitle.setObjectName('quickSubtitle')
        subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        text.addWidget(title)
        text.addWidget(subtitle)
        row.addLayout(text, 1)
        close = QToolButton(header)
        close.setText('×')
        close.setFixedSize(30, 30)
        close.setToolTip('关闭 · Esc')
        close.setAccessibleName('关闭快捷面板')
        close.clicked.connect(self.reject)
        row.addWidget(close, 0, Qt.AlignTop)
        layout.addWidget(header)
        box = QFrame(surface)
        box.setObjectName('quickSearchBox')
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 3, 8, 3)
        self.search = QLineEdit(box)
        self.search.setObjectName('quickSearch')
        self.search.setAccessibleName('筛选程序或输入网页搜索内容')
        self.search.setPlaceholderText('找程序，或输入要搜索的内容…')
        self.search.setMaxLength(2048)
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        row.addWidget(self.search, 1)
        self.engine = QComboBox(box)
        self.engine.setAccessibleName('网页搜索引擎')
        self.engine.setToolTip('选择网页搜索引擎；只有确认搜索时才打开浏览器')
        for key, (name, _, _) in ENGINES.items():
            self.engine.addItem(name + ' ▾', key)
        selected_engine = cfg.get('quick_search_engine', 'bing')
        self.engine.setCurrentIndex(max(0, self.engine.findData(selected_engine)))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        row.addWidget(self.engine)
        layout.addWidget(box)
        row = QHBoxLayout()
        self.count = QLabel(surface)
        self.count.setObjectName('quickCount')
        row.addWidget(self.count, 1)
        add_app = QPushButton('+ 程序', surface)
        add_app.clicked.connect(self._choose_apps)
        row.addWidget(add_app)
        add_folder = QPushButton('+ 文件夹', surface)
        add_folder.clicked.connect(self._choose_folder)
        row.addWidget(add_folder)
        self.manage = QPushButton('管理', surface)
        self.manage.setCheckable(True)
        self.manage.toggled.connect(self._manage_changed)
        row.addWidget(self.manage)
        layout.addLayout(row)
        self.results = QListWidget(surface)
        self.results.setAccessibleName('快捷操作结果')
        self.results.setItemDelegate(ResultDelegate(self.results))
        self.results.setUniformItemSizes(True)
        self.results.setMouseTracking(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(self._activate_item)
        self.results.itemActivated.connect(self._activate_item)
        self.results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._context_menu)
        self.results.currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.results, 1)
        self.empty = QLabel('还没有收藏。点「+ 程序」添加，或输入内容搜索网页。', surface)
        self.empty.setWordWrap(True)
        self.empty.setObjectName('quickSubtitle')
        layout.addWidget(self.empty)
        self.tools = QFrame(surface)
        toolbar = QHBoxLayout(self.tools)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.edit_button = QPushButton('名称 / 关键词', self.tools)
        self.edit_button.clicked.connect(self._edit_current)
        toolbar.addWidget(self.edit_button)
        self.up_button = QPushButton('上移', self.tools)
        self.up_button.clicked.connect(lambda: self._move_current(-1))
        toolbar.addWidget(self.up_button)
        self.down_button = QPushButton('下移', self.tools)
        self.down_button.clicked.connect(lambda: self._move_current(1))
        toolbar.addWidget(self.down_button)
        toolbar.addStretch()
        self.remove_button = QPushButton('移除收藏', self.tools)
        self.remove_button.clicked.connect(self._remove_current)
        toolbar.addWidget(self.remove_button)
        self.tools.hide()
        layout.addWidget(self.tools)
        self.error = QLabel(surface)
        self.error.setObjectName('quickError')
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        footer = QLabel('↑↓ 选择   Enter 打开   Ctrl+Enter 网页搜索   Esc 关闭', surface)
        footer.setObjectName('quickFooter')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=self._search_web)
        QShortcut(QKeySequence('Ctrl+Enter'), self, activated=self._search_web)
        QShortcut(QKeySequence('Ctrl+L'), self, activated=self._focus_search)
        self._refresh()

    def _focus_search(self):
        self.search.setFocus(Qt.ShortcutFocusReason)
        self.search.selectAll()

    def showEvent(self, event):
        super().showEvent(event)
        self._focus_search()

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if event.key() == Qt.Key_Down else -1
            row = max(0, min(self.results.count() - 1, self.results.currentRow() + step))
            self.results.setCurrentRow(row)
            return True
        return super().eventFilter(obj, event)

    def _refresh(self, *_):
        selected = self._current_entry() if hasattr(self, 'results') else None
        self.results.clear()
        query = self.search.text().strip()
        entries = filter_entries(self.entries, query)
        for entry in entries:
            self._append(entry)
        if query and not self.manage.isChecked():
            engine_name = ENGINES[self.engine.currentData()][0]
            self._append({'kind': 'search', 'name': f'用{engine_name}搜索「{query}」',
                          'detail': '打开默认浏览器 · 输入时不会联网', 'query': query})
        self.count.setText(f'快捷收藏  {len(entries)} / {len(self.entries)}' if query else f'快捷收藏  {len(self.entries)} 项')
        self.empty.setVisible(self.results.count() == 0)
        self.empty.setText('没有匹配的收藏。修改关键词，或关闭管理模式搜索网页。' if query
                           else '还没有收藏。点「+ 程序」添加，或输入内容搜索网页。')
        index = 0
        if selected:
            for i in range(self.results.count()):
                if self.results.item(i).data(Qt.UserRole).get('id') == selected.get('id'):
                    index = i
                    break
        self.results.setCurrentRow(index if self.results.count() else -1)
        self._selection_changed()

    def _append(self, entry):
        item = QListWidgetItem(entry['name'])
        item.setData(Qt.UserRole, entry)
        item.setToolTip(entry.get('path', entry.get('detail', '')))
        self.results.addItem(item)

    def _current_entry(self):
        item = self.results.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _engine_changed(self):
        self.cfg['quick_search_engine'] = self.engine.currentData()
        self._save(self.cfg)
        self._refresh()

    def _manage_changed(self, managing):
        self.manage.setText('完成' if managing else '管理')
        self.results.setProperty('managing', managing)
        self.tools.setVisible(managing)
        self._refresh()

    def _selection_changed(self, *_):
        if not hasattr(self, 'tools'):
            return
        entry = self._current_entry()
        editable = bool(entry and entry['kind'] != 'search')
        self.edit_button.setEnabled(editable)
        self.remove_button.setEnabled(editable)
        index = next((i for i, e in enumerate(self.entries) if editable and e['id'] == entry['id']), -1)
        self.up_button.setEnabled(index > 0)
        self.down_button.setEnabled(0 <= index < len(self.entries) - 1)

    def _show_error(self, message):
        self.error.setText(str(message))
        self.error.show()

    def _activate_current(self):
        self._activate_item(self.results.currentItem())

    def _activate_item(self, item):
        if item is None or self._launching or self.manage.isChecked():
            return
        entry = item.data(Qt.UserRole)
        if entry['kind'] == 'search':
            self._search_web()
            return
        self._launching = True
        try:
            if not self._launch(entry):
                raise OSError('系统未能打开这个项目，请检查文件关联或程序路径。')
        except (OSError, ValueError) as error:
            self._launching = False
            self._show_error(error)
            return
        self.accept()

    def _search_web(self):
        if self._launching:
            return
        try:
            url = search_url(self.search.text(), self.engine.currentData())
            if not self._open_url(QUrl(url)):
                raise OSError('无法打开默认浏览器，请检查 Windows 默认浏览器设置。')
        except (OSError, ValueError) as error:
            self._show_error(error)
            return
        self._launching = True
        self.accept()

    def _persist(self, select_id=None):
        self.cfg['quick_launch_items'] = [dict(e) for e in self.entries]
        self._save(self.cfg)
        self.error.hide()
        self._refresh()
        if select_id:
            for i in range(self.results.count()):
                if self.results.item(i).data(Qt.UserRole).get('id') == select_id:
                    self.results.setCurrentRow(i)
                    break

    def add_target(self, path, kind='app'):
        path = validate_target(path, kind)
        for entry in self.entries:
            if os.path.normcase(entry['path']) == os.path.normcase(path):
                self._show_error('这个项目已经在收藏中。')
                return False
        if len(self.entries) >= MAX_ITEMS:
            self._show_error(f'最多保留 {MAX_ITEMS} 个快捷项目，请先移除不常用的收藏。')
            return False
        entry = {'id': uuid.uuid4().hex, 'name': Path(path).stem if kind == 'app' else Path(path).name or path,
                 'path': path, 'kind': kind, 'keywords': ''}
        self.entries.append(entry)
        self.search.clear()
        self._persist(entry['id'])
        return True

    def _choose_apps(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加程序或快捷方式', '', '程序与快捷方式 (*.exe *.lnk)')
        for path in paths:
            try:
                self.add_target(path)
            except (ValueError, OSError) as error:
                self._show_error(error)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '添加常用文件夹')
        if path:
            try:
                self.add_target(path, 'folder')
            except (ValueError, OSError) as error:
                self._show_error(error)

    def _edit_current(self):
        entry = self._current_entry()
        if not entry or entry['kind'] == 'search':
            return
        name, ok = QInputDialog.getText(self, '名称与关键词', '显示名称（最多 80 字）：', text=entry['name'])
        if not ok or not name.strip():
            return
        keywords, ok = QInputDialog.getText(self, '搜索关键词', '可填英文、拼音或简称，用空格分隔：', text=entry['keywords'])
        if not ok:
            return
        for saved in self.entries:
            if saved['id'] == entry['id']:
                saved.update(name=name.strip()[:80], keywords=keywords.strip()[:160])
        self._persist(entry['id'])

    def _move_current(self, delta):
        entry = self._current_entry()
        if not entry or entry['kind'] == 'search':
            return
        index = next(i for i, e in enumerate(self.entries) if e['id'] == entry['id'])
        target = index + delta
        if 0 <= target < len(self.entries):
            self.entries[index], self.entries[target] = self.entries[target], self.entries[index]
            self._persist(entry['id'])

    def _remove_current(self):
        entry = self._current_entry()
        if not entry or entry['kind'] == 'search':
            return
        answer = QMessageBox.question(self, '移除收藏', f'从快捷面板移除「{entry["name"]}」？\n不会删除程序或文件。',
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.entries = [e for e in self.entries if e['id'] != entry['id']]
            self._persist()

    def _context_menu(self, point):
        item = self.results.itemAt(point)
        if not item or item.data(Qt.UserRole)['kind'] == 'search':
            return
        self.results.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('名称 / 关键词').triggered.connect(self._edit_current)
        menu.addAction('上移').triggered.connect(lambda: self._move_current(-1))
        menu.addAction('下移').triggered.connect(lambda: self._move_current(1))
        menu.addSeparator()
        menu.addAction('移除收藏（不删除文件）').triggered.connect(self._remove_current)
        menu.exec(self.results.mapToGlobal(point))
        menu.deleteLater()
