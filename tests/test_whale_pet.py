r"""运行：.venv\Scripts\python.exe -m unittest discover -s tests -v。"""
import hashlib
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtWidgets import QApplication

from whale_pet import WhalePet, ACTION_LABELS


class WhalePetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = WhalePet(auto_actions=False)

    def tearDown(self):
        self.pet.shutdown()
        self.pet.close()
        self.pet.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_complete_asset_set_and_hashes(self):
        self.assertEqual(set(self.pet.actions), set(ACTION_LABELS))
        self.assertEqual(len(self.pet.assets), 28)
        self.assertEqual(len(self.pet.manifest['files']), 67)
        for asset in self.pet.assets.values():
            self.assertEqual(len(asset['frames']), asset['frame_count'])
            for relative in asset['frames']:
                path = self.pet._path_for(relative)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 self.pet.manifest['files'][relative]['sha256'])
                self.assertFalse(QImage(str(path)).isNull())

    def test_every_frame_renders_and_cache_is_bounded(self):
        seen = set()
        for name, asset in self.pet.assets.items():
            self.pet.play_asset(name)
            duration = self.pet._duration
            for i, relative in enumerate(asset['frames']):
                self.pet._elapsed = duration * (i + 0.2) / len(asset['frames'])
                self.pet._refresh_frame()
                self.assertEqual(self.pet._frame_key, relative)
                image = self.pet.grab().toImage()
                self.assertFalse(image.isNull())
                self.assertTrue(image.hasAlphaChannel())
                self.assertLessEqual(self.pet.cache_bytes, self.pet.CACHE_LIMIT)
                seen.add(relative)
        self.assertEqual(len(seen), 67)

    def test_all_states_and_transition_completion(self):
        for state, spec in self.pet.actions.items():
            self.pet._start_state(state, True)
            if self.pet._phase == 'enter':
                self.pet.advance(self.pet._duration + 0.01)
            self.assertEqual(self.pet.state, state)
            self.assertEqual(self.pet._asset, self.pet._directional(spec['asset']))
            if spec['duration_ms']:
                self.pet.advance(spec['duration_ms'] / 1000 + 0.01)
                self.assertEqual(self.pet.state, 'IDLE')

    def test_walk_directions_and_exit(self):
        distances = []
        self.pet.walk_step.connect(distances.append)
        for facing, asset in [(-1, 'walk_side'), (1, 'walk_side_right')]:
            self.pet.set_facing(facing)
            self.pet._start_state('WALKING', True)
            self.assertIn('walk_start', self.pet._asset)
            self.pet.advance(0.27)
            self.assertEqual(self.pet._asset, asset)
            self.pet.advance(0.1)
            self.assertGreater(distances[-1] * facing, 0)
            self.pet.play_state('IDLE')
            self.assertIn('walk_stop', self.pet._asset)
            self.pet.advance(0.31)
            self.assertEqual(self.pet.state, 'IDLE')

    def test_sleep_wake_and_grab_interrupt(self):
        self.pet.play_state('SLEEPING')
        self.assertEqual(self.pet._asset, 'sleep_enter')
        self.pet.advance(1.06)
        self.assertEqual(self.pet._asset, 'sleep')
        self.pet.play_state('HAPPY')
        self.assertEqual(self.pet._asset, 'sleep_wake')
        self.pet.react('grab')
        self.assertEqual(self.pet.state, 'DRAGGING')
        self.pet.react('fall')
        self.assertEqual(self.pet._asset, 'released_airborne')
        self.pet.advance(0.15)
        self.assertEqual(self.pet._asset, 'falling')
        self.pet.react('land')
        self.assertEqual(self.pet.state, 'LANDING')

    def test_size_ground_anchor_and_path_boundary(self):
        for height in (120, 240, 360, 600):
            self.pet.set_image_size(height)
            bottoms = []
            for asset in self.pet.assets:
                self.pet.play_asset(asset)
                left, top, right, bottom = self.pet.content_inset()
                self.assertGreaterEqual(min(left, top, right, bottom), 0)
                bottoms.append(bottom)
            self.assertLessEqual(max(bottoms) - min(bottoms), 1)
        with self.assertRaises(ValueError):
            self.pet._path_for('../../config.py')

    def test_hidden_and_shutdown_timers(self):
        self.pet.show()
        self.app.processEvents()
        self.assertTrue(self.pet.timer.isActive())
        self.pet.hide()
        self.assertFalse(self.pet.timer.isActive())
        self.pet.show()
        self.assertTrue(self.pet.timer.isActive())
        self.pet.shutdown()
        self.assertFalse(self.pet.timer.isActive())
        self.assertEqual(self.pet.cache_bytes, 0)
        self.pet.hide()
        self.pet.show()
        self.assertFalse(self.pet.timer.isActive())

    def test_render_contact_sheet(self):
        output = os.environ.get('WHALE_PREVIEW_OUT')
        if not output:
            self.skipTest('未指定预览输出路径')
        self.pet.set_image_size(200)
        sheet = QImage(1000, 5 * 270, QImage.Format_ARGB32)
        sheet.fill(QColor('#191d2b'))
        painter = QPainter(sheet)
        for i, (state, label) in enumerate(ACTION_LABELS.items()):
            self.pet._start_state(state, True)
            if self.pet._phase == 'enter':
                self.pet.advance(self.pet._duration + 0.01)
            self.pet.advance(0.2)
            x, y = i % 4 * 250, i // 4 * 270
            painter.drawPixmap(x + 10, y + 25, self.pet.grab())
            painter.setPen(QColor('#eeeeff'))
            painter.drawText(x + 20, y + 20, label)
        painter.end()
        self.assertTrue(sheet.save(output))


if __name__ == '__main__':
    unittest.main()
