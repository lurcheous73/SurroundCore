import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from app import db, sources


class SourceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'test.sqlite3')
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_source_persists(self):
        item = sources.validate_source({
            'id': 'nas', 'name': 'NAS Music', 'kind': 'nfs',
            'cache_policy': 'read-through', 'config': {}, 'enabled': True,
        })
        db.save_source(item)
        stored = db.get_source('nas')
        self.assertEqual(stored['path'], '/sources/nas')
        self.assertEqual(stored['cache_policy'], 'read-through')
        self.assertTrue(stored['enabled'])


    def test_source_id_traversal_rejected(self):
        with self.assertRaises(ValueError):
            sources.validate_source({'id': '../bad', 'name': 'bad', 'kind': 'nfs'})

    def test_invalid_source_kind_rejected(self):
        with self.assertRaises(ValueError):
            sources.validate_source({'id': 'x', 'name': 'x', 'kind': 'magic'})

    def test_plex_source_is_reserved_not_claimed_available(self):
        item = sources.validate_source({'id': 'plex', 'name': 'Plex', 'kind': 'plex'})
        status = sources.source_status(item)
        self.assertFalse(status['available'])
        self.assertFalse(status['implemented'])


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = sources.CACHE_ROOT
        self.old_db = db.DB_PATH
        self.old_max = sources.CACHE_MAX_GB
        self.old_free = sources.CACHE_MIN_FREE_GB
        sources.CACHE_ROOT = Path(self.tmp.name) / 'cache'
        db.DB_PATH = os.path.join(self.tmp.name, 'cache-test.sqlite3')
        db.init_db()
        sources.CACHE_MAX_GB = 0
        sources.CACHE_MIN_FREE_GB = 0

    def tearDown(self):
        sources.CACHE_ROOT = self.old_root
        sources.CACHE_MAX_GB = self.old_max
        sources.CACHE_MIN_FREE_GB = self.old_free
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_read_through_cache_is_reused(self):
        src = Path(self.tmp.name) / 'track.flac'
        src.write_bytes(b'abc123' * 100)
        first = Path(sources.cache_file(str(src), 'slow-disk'))
        self.assertTrue(first.is_file())
        cached_inode = first.stat().st_ino
        second = Path(sources.cache_file(str(src), 'slow-disk'))
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_ino, cached_inode)
        self.assertEqual(second.read_bytes(), src.read_bytes())
        expected=second.read_bytes(); src.unlink()
        offline=Path(sources.cache_file(str(src), 'slow-disk'))
        self.assertEqual(offline, second)
        self.assertEqual(offline.read_bytes(), expected)


    def test_global_limit_evicts_oldest_read_through_file(self):
        sources.CACHE_MAX_GB = 700 / (1024 ** 3)
        a = Path(self.tmp.name) / 'a.flac'; a.write_bytes(b'a' * 600)
        b = Path(self.tmp.name) / 'b.flac'; b.write_bytes(b'b' * 600)
        first = Path(sources.cache_file(str(a), 'slow'))
        self.assertTrue(first.exists())
        second = Path(sources.cache_file(str(b), 'slow'))
        self.assertTrue(second.exists())
        self.assertFalse(first.exists())
        self.assertFalse(sources._meta_for(first).exists())

    def test_purge_removes_cached_media(self):
        src = Path(self.tmp.name) / 'track.flac'
        src.write_bytes(b'x' * 512)
        cached = Path(sources.cache_file(str(src), 'usb'))
        self.assertTrue(cached.exists())
        self.assertEqual(sources.purge_source_cache('usb'), 1)
        self.assertFalse(cached.exists())


if __name__ == '__main__':
    unittest.main()
