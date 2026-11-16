import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sketch_cache', ROOT/'tools/ai-support/sketch_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def object_file():
    ident = b'\x7fELF\x01\x01\x01'+bytes(9)
    return struct.pack('<16sHHIIIIIHHHHHH', ident,1,40,1,0,0,52,0,52,0,0,40,1,0)+bytes(40)


class SketchCacheTest(unittest.TestCase):
    def test_interrupted_objects_are_rebuilt_without_touching_app_or_valid_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build=root/'.cache/sketch/sketch/src/tof';build.mkdir(parents=True)
            protected = {'sketch/sketch.ino':b'source', 'data/voice_config.json':b'private settings',
                         '.cache/.venv/marker':b'environment', '.cache/sketch/sketch.ino.elf-zsk.bin':b'firmware',
                         '.cache/sketch/sketch/good.cpp.o':object_file()}
            for name, body in protected.items():
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body)
            before={n:((root/n).read_bytes(),(root/n).stat().st_mtime_ns) for n in protected}
            for name, body in [('api.cpp',b''),('platform.cpp',b'\x7fELF'),('other.cpp',b'corrupt')]:
                (build/(name+'.o')).write_bytes(body);(build/(name+'.d')).write_bytes(b'')
            removed=cache.repair(root)
            self.assertEqual(len(removed),3)
            self.assertFalse(list(build.glob('*.o')))
            self.assertFalse(list(build.glob('*.d')))
            for name, state in before.items():
                self.assertEqual(((root/name).read_bytes(),(root/name).stat().st_mtime_ns),state)
            self.assertEqual(cache.repair(root),[])  # Healthy next boot does no work.

    def test_empty_dependency_or_truncated_section_table_also_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build=root/'.cache/sketch';build.mkdir(parents=True)
            (build/'a.cpp.o').write_bytes(object_file());(build/'a.cpp.d').write_bytes(b'')
            (build/'b.cpp.o').write_bytes(object_file()[:-1])
            self.assertEqual(cache.repair(root),['a.cpp.o','b.cpp.o'])

    def test_symlinks_do_not_extend_deletion_outside_the_sketch_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);outside=root/'outside';outside.mkdir()
            file=outside/'victim.o';file.write_bytes(b'')
            build=root/'app/.cache/sketch';build.mkdir(parents=True)
            (build/'linked.o').symlink_to(file)
            (build/'linked-dir').symlink_to(outside,target_is_directory=True)
            self.assertEqual(cache.repair(root/'app'),[])
            self.assertTrue(file.exists())
            (build/'linked.o').unlink();(build/'linked-dir').unlink();build.rmdir()
            build.symlink_to(outside,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'symlinked'):
                cache.repair(root/'app')
            self.assertTrue(file.exists())


if __name__=='__main__':
    unittest.main()
