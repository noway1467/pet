"""后台生成有界纹理缓存；绝不写入模型原目录，不创建 Qt 控件或 OpenGL 对象。"""
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImageReader, QImageWriter

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pet-textures")
_lock = threading.Lock()
_leases = {}
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 8


class PreparedModel:
    def __init__(self, path, cache_dir=None):
        self.path = str(path)
        self.cache_dir = cache_dir
        if cache_dir is not None:
            with _lock:
                _leases[cache_dir] = _leases.get(cache_dir, 0) + 1

    def close(self):
        with _lock:
            key, self.cache_dir = self.cache_dir, None
            if key is not None:
                count = _leases.get(key, 1) - 1
                if count:
                    _leases[key] = count
                else:
                    _leases.pop(key, None)


def _safe_remove(path, root):
    # 只清除本模块自己生成的缓存目录，拒绝链接和逃逸路径。
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or path.is_junction() or path.resolve().parent != root:
        raise ValueError("不安全的缓存清理路径")
    shutil.rmtree(path)


def prune_cache(root):
    root = Path(root)
    if not root.is_dir():
        return
    entries = []
    for path in root.iterdir():
        if (len(path.name) != 64 or any(c not in '0123456789abcdef' for c in path.name)
                or path.is_symlink() or path.is_junction() or not (path / 'ready.json').is_file()):
            continue
        size = sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        entries.append((path.stat().st_mtime_ns, path, size))
    total = sum(e[2] for e in entries)
    count = len(entries)
    for _, path, size in sorted(entries):
        if count <= MAX_CACHE_ENTRIES and total <= MAX_CACHE_BYTES:
            break
        with _lock:
            if _leases.get(path):
                continue
            _safe_remove(path, root)
        count -= 1
        total -= size


def _references(data, folder):
    """只遍历模型声明的文件字段；动作组名、表情名称和其他配置不重写。"""
    result = []
    def add(container, key, texture=False):
        value = container[key]
        if not isinstance(value, str) or not value:
            return
        path = (folder / value.replace('\\', '/')).resolve()
        if not path.is_file():
            # 交由原运行时处理可选/缺失引用，不把失效的派生模型写入缓存。
            raise FileNotFoundError(str(path))
        result.append((container, key, path, texture))
    if 'FileReferences' in data:
        refs = data['FileReferences']
        for key in ('Moc', 'Physics', 'Pose', 'DisplayInfo', 'UserData'):
            if key in refs:
                add(refs, key)
        textures = refs.get('Textures', [])
        for i in range(len(textures)):
            add(textures, i, True)
        for expression in refs.get('Expressions', []):
            add(expression, 'File')
        for motions in refs.get('Motions', {}).values():
            for motion in motions:
                for key in ('File', 'Sound'):
                    if key in motion:
                        add(motion, key)
    else:
        for key in ('model', 'physics', 'pose'):
            if key in data:
                add(data, key)
        textures = data.get('textures', [])
        for i in range(len(textures)):
            add(textures, i, True)
        for expression in data.get('expressions', []):
            add(expression, 'file')
        for motions in data.get('motions', {}).values():
            for motion in motions:
                for key in ('file', 'sound'):
                    if key in motion:
                        add(motion, key)
    return result


def _stamp(path):
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns)


def prepare_model(model_path, limit, cache_root):
    source = Path(model_path).resolve()
    if limit == 0:
        return PreparedModel(source)
    if limit not in (1024, 2048, 4096):
        raise ValueError("不支持的纹理档位")
    data = json.loads(source.read_text(encoding='utf-8-sig'))
    refs = _references(data, source.parent)
    stamps = [_stamp(source)] + [_stamp(p) for _, _, p, _ in refs]
    oversized = set()
    for _, _, path, texture in refs:
        if texture:
            size = QImageReader(str(path)).size()
            if max(size.width(), size.height()) > limit:
                oversized.add(path)
    if not oversized:
        return PreparedModel(source)
    key = hashlib.sha256(json.dumps([3, limit, stamps]).encode()).hexdigest()
    root = Path(cache_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / key
    settings_name = 'prepared.model3.json' if 'FileReferences' in data else 'prepared.model.json'
    ready = target / 'ready.json'
    if ready.is_file():
        record = json.loads(ready.read_text(encoding='utf-8'))
        if all((target / name).is_file() and (target / name).stat().st_size == size
               for name, size in record['files'].items()):
            os.utime(target, None)
            prepared = PreparedModel(target / settings_name, target)
            prune_cache(root)
            return prepared
    if target.exists():
        with _lock:
            if _leases.get(target):
                raise RuntimeError('正在使用的纹理缓存不完整')
            _safe_remove(target, root)
    stage = Path(tempfile.mkdtemp(prefix='stage-', dir=root))
    try:
        files = []
        mapped = {}
        for container, field, path, texture in refs:
            if path not in mapped:
                suffix = '.png' if path in oversized else path.suffix
                name = '%04d%s' % (len(mapped), suffix)
                dest = stage / name
                if path in oversized:
                    reader = QImageReader(str(path))
                    size = reader.size()
                    reader.setScaledSize(size.scaled(QSize(limit, limit), Qt.KeepAspectRatio))
                    image = reader.read()
                    if image.isNull():
                        raise RuntimeError(reader.errorString())
                    writer = QImageWriter(str(dest), b'png')
                    writer.setCompression(1)
                    if not writer.write(image):
                        raise RuntimeError(writer.errorString())
                    del image, reader, writer
                else:
                    shutil.copyfile(path, dest)
                mapped[path] = name
                files.append(name)
            container[field] = mapped[path]
        # 源文件在准备过程中被用户修改时不发布半新半旧缓存。
        if stamps != [_stamp(source)] + [_stamp(p) for _, _, p, _ in refs]:
            raise RuntimeError('模型在准备过程中发生变化，请重试')
        (stage / settings_name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        files.append(settings_name)
        (stage / 'ready.json').write_text(json.dumps({'files': {
            name: (stage / name).stat().st_size for name in files}}), encoding='utf-8')
        stage.rename(target)
        prepared = PreparedModel(target / settings_name, target)
        prune_cache(root)
        return prepared
    finally:
        if stage.exists():
            _safe_remove(stage, root)


def prepare_async(model_path, limit, cache_root):
    if limit == 0:
        future = Future()
        future.set_result(PreparedModel(model_path))
        return future
    return _executor.submit(prepare_model, model_path, limit, cache_root)


def discard_future(future):
    if future.cancel():
        return
    def discard(done):
        if not done.cancelled() and done.exception() is None:
            done.result().close()
    future.add_done_callback(discard)
