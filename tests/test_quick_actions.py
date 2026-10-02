"""快捷面板的 Qt 交互、路径安全、持久化与无联网输入回归测试。"""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
import config
from quick_actions import QuickPanel, normalize_entries, filter_entries, search_url, launch_entry, MAX_ITEMS


class QuickActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pet-quick-test-')
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.saved, self.launched, self.urls = [], [], []
        self.panel = QuickPanel(self.cfg,
            save=lambda cfg: self.saved.append(copy.deepcopy(cfg)),
            launch=lambda entry: self.launched.append(entry) or True,
            open_url=lambda url: self.urls.append(url.toString()) or True)
        self.panel.setAttribute(Qt.WA_DeleteOnClose, False)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_defaults_and_empty_are_distinct(self):
        self.assertEqual(len(normalize_entries(None)), 3)
        self.assertEqual(normalize_entries([]), [])
        self.assertEqual(normalize_entries('broken config'), [])
        self.assertEqual(normalize_entries([{}, {'name': 9, 'path': 'x'}]), [])
        self.assertEqual(len(self.panel.entries), 3)

    def test_input_filters_locally_and_never_launches(self):
        self.panel.search.setText('jsb')
        self.assertEqual(self.panel.results.count(), 2)
        self.assertEqual(self.panel.results.item(0).data(Qt.UserRole)['name'], '记事本')
        self.assertEqual(self.launched, [])
        self.assertEqual(self.urls, [])
        self.assertEqual(self.saved, [])
        QTest.keyClick(self.panel.search, Qt.Key_Down)
        self.assertEqual(self.panel.results.currentRow(), 1)
        QTest.keyClick(self.panel.search, Qt.Key_Up)
        self.assertEqual(self.panel.results.currentRow(), 0)
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.urls, [])

    def test_search_encoding_and_engine_persistence(self):
        query = '鲸鱼娘 & a=b #测试 /?'
        for key, parameter in [('bing', 'q'), ('baidu', 'wd'), ('google', 'q')]:
            url = search_url(query, key)
            self.assertEqual(parse_qs(urlsplit(url).query)[parameter], [query])
            self.assertEqual(urlsplit(url).scheme, 'https')
        self.panel.engine.setCurrentIndex(self.panel.engine.findData('baidu'))
        self.assertEqual(self.saved[-1]['quick_search_engine'], 'baidu')
        self.panel.search.setText(query)
        self.panel._search_web()
        self.assertEqual(parse_qs(urlsplit(self.urls[0]).query)['wd'], [query])

    def test_add_edit_order_remove_does_not_touch_files(self):
        folder = Path(self.temp.name) / '项目 文档 & 资料'
        folder.mkdir()
        self.assertTrue(self.panel.add_target(str(folder), 'folder'))
        self.assertFalse(self.panel.add_target(str(folder), 'folder'))
        self.assertTrue(self.panel.error.text())
        self.panel.manage.setChecked(True)
        self.panel._activate_current()
        self.assertEqual(self.launched, [])
        with patch('quick_actions.QInputDialog.getText', side_effect=[('我的项目', True), ('work xm', True)]):
            self.panel._edit_current()
        self.assertEqual(self.panel._current_entry()['name'], '我的项目')
        identity = self.panel._current_entry()['id']
        self.panel._move_current(-1)
        self.assertEqual(self.panel.entries[-2]['id'], identity)
        with patch('quick_actions.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel._remove_current()
        self.assertTrue(folder.is_dir())
        self.assertFalse(any(e['id'] == identity for e in self.saved[-1]['quick_launch_items']))

    def test_launch_passes_exact_path_not_shell(self):
        target = Path(self.temp.name) / '测试 & name.exe'
        target.write_bytes(b'not an executable; launch mocked')
        entry = {'path': str(target), 'kind': 'app'}
        with patch('quick_actions.os.startfile', create=True) as start:
            self.assertTrue(launch_entry(entry))
            start.assert_called_once_with(str(target), 'open')
        with self.assertRaises(ValueError):
            launch_entry({'path': str(target) + ' & calc.exe', 'kind': 'app'})
        with self.assertRaises(ValueError):
            launch_entry({'path': r'\\server\share\tool.exe', 'kind': 'app'})
        with self.assertRaises(ValueError):
            launch_entry({'path': 'cmd /c echo test', 'kind': 'app'})

    def test_failure_stays_open_and_empty_search_does_not_open(self):
        self.panel._launch = lambda entry: (_ for _ in ()).throw(OSError('测试：路径已失效'))
        self.panel._activate_current()
        self.assertIn('路径已失效', self.panel.error.text())
        self.assertFalse(self.panel._launching)
        self.panel.search.clear()
        self.panel._search_web()
        self.assertEqual(self.urls, [])
        self.assertIn('输入', self.panel.error.text())

    def test_filter_speed_no_timers_and_render(self):
        entries = [{'id': str(i), 'name': '编辑器 ' + str(i), 'path': rf'C:\tools\tool{i}.exe',
                    'kind': 'app', 'keywords': 'editor'} for i in range(MAX_ITEMS)]
        start = time.perf_counter()
        for _ in range(100):
            self.assertEqual(len(filter_entries(entries, 'editor')), MAX_ITEMS)
        elapsed = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed, 1000)
        self.assertEqual(self.panel.findChildren(QTimer), [])
        self.panel.show()
        self.app.processEvents()
        image = self.panel.grab()
        self.assertFalse(image.isNull())
        output = os.environ.get('QUICK_PANEL_QA')
        if output:
            image.save(output)
        print('QUICK_FILTER_80x100_MS=%.2f' % elapsed)


if __name__ == '__main__':
    unittest.main()
