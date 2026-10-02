"""纹理缓存完整性与隔离测试：不依赖真实用户模型。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage, QColor
import live2d_assets as assets


class TextureCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pet-texture-test-')
        self.root = Path(self.temp.name)
        self.source = self.root / '用户模型'
        self.source.mkdir()
        self.cache = self.root / 'cache'
        image = QImage(2304, 1152, QImage.Format_RGBA8888)
        image.fill(QColor(30, 90, 180, 120))
        self.assertTrue(image.save(str(self.source / 'texture.png')))
        for name in ('avatar.moc3', 'idle.motion3.json', 'happy.exp3.json', 'physics.json', 'voice.wav'):
            (self.source / name).write_bytes(b'fixture-content')
        self.data = {'Version': 3, 'FileReferences': {
            'Moc': 'avatar.moc3', 'Textures': ['texture.png'], 'Physics': 'physics.json',
            'Expressions': [{'Name': 'happy', 'File': 'happy.exp3.json'}],
            'Motions': {'Idle': [{'File': 'idle.motion3.json', 'Sound': 'voice.wav', 'FadeInTime': .5}]}}}
        self.model = self.source / 'avatar.model3.json'
        self.model.write_text(json.dumps(self.data), encoding='utf-8')
        self.prepared = []

    def tearDown(self):
        for prepared in self.prepared:
            prepared.close()
        self.temp.cleanup()

    def prepare(self, limit=2048):
        prepared = assets.prepare_model(self.model, limit, self.cache)
        self.prepared.append(prepared)
        return prepared

    def test_references_actions_alpha_and_source_unchanged(self):
        before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in self.source.iterdir()}
        prepared = self.prepare()
        target = Path(prepared.path)
        self.assertTrue(target.is_relative_to(self.cache))
        data = json.loads(target.read_text(encoding='utf-8'))
        refs = data['FileReferences']
        self.assertEqual(refs['Expressions'][0]['Name'], 'happy')
        self.assertEqual(refs['Motions']['Idle'][0]['FadeInTime'], .5)
        for _, _, path, _ in assets._references(data, target.parent):
            self.assertTrue(path.is_file())
        image = QImage(str(target.parent / refs['Textures'][0]))
        self.assertEqual((image.width(), image.height()), (2048, 1024))
        self.assertAlmostEqual(image.pixelColor(20, 20).alpha(), 120, delta=1)
        after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in self.source.iterdir()}
        self.assertEqual(before, after)

    def test_original_and_small_texture_bypass(self):
        self.assertEqual(self.prepare(0).path, str(self.model))
        self.assertEqual(self.prepare(4096).path, str(self.model))
        self.assertFalse(self.cache.exists())

    def test_reuse_and_invalidation(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first.path, second.path)
        (self.source / 'idle.motion3.json').write_bytes(b'changed-motion')
        third = self.prepare()
        self.assertNotEqual(first.path, third.path)

    def test_v2_keeps_motion_group_and_expression(self):
        data = {'model': 'avatar.moc3', 'textures': ['texture.png'],
                'motions': {'idle': [{'file': 'idle.motion3.json', 'sound': 'voice.wav'}]},
                'expressions': [{'name': 'happy', 'file': 'happy.exp3.json'}]}
        self.model.write_text(json.dumps(data), encoding='utf-8')
        target = Path(self.prepare().path)
        data = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(set(data['motions']), {'idle'})
        self.assertEqual(data['expressions'][0]['name'], 'happy')
        self.assertTrue((target.parent / data['motions']['idle'][0]['sound']).is_file())

    def test_missing_reference_does_not_publish_incomplete_model(self):
        self.data['FileReferences']['Pose'] = 'missing.pose3.json'
        self.model.write_text(json.dumps(self.data), encoding='utf-8')
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertFalse(self.cache.exists())

    def test_eviction_respects_leases_and_unrelated_files(self):
        first = self.prepare()
        marker = self.cache / 'my-important-file.txt'
        marker.write_text('keep', encoding='utf-8')
        (self.source / 'idle.motion3.json').write_bytes(b'another-motion')
        second = self.prepare()
        first.close()
        with patch.object(assets, 'MAX_CACHE_ENTRIES', 1):
            assets.prune_cache(self.cache)
        self.assertFalse(Path(first.path).exists())
        self.assertTrue(Path(second.path).exists())
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')
        with self.assertRaises(ValueError):
            assets._safe_remove(self.source, self.cache)

    def test_async_discard_releases_lease(self):
        future = assets.prepare_async(str(self.model), 2048, self.cache)
        prepared = future.result(timeout=20)
        directory = prepared.cache_dir
        self.assertIn(directory, assets._leases)
        assets.discard_future(future)
        self.assertNotIn(directory, assets._leases)


if __name__ == '__main__':
    unittest.main()
