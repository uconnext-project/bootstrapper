import fcntl
import os
import struct
import time
import subprocess
import threading
import errno
import pathlib
from colorama import Fore
from fuse import FUSE, Operations

from app.log import Log

class FATImageFUSE(Operations):
    def __init__(self, source_path, on_read=None, on_write=None):
        self.source_path = source_path
        self.on_read = on_read
        self.on_write = on_write
        self._fd = None

    def _get_fd(self):
        if self._fd is None:
            self._fd = os.open(self.source_path, os.O_RDWR)
        return self._fd

    def open(self, path, flags):
        if path != '/image.bin':
            raise OSError(errno.ENOENT)
        return 0

    def getattr(self, path, fh=None):
        if path != '/image.bin':
            raise OSError(errno.ENOENT)
        st = os.lstat(self.source_path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
                    'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    def read(self, path, length, offset, fh):
        if self.on_read:
            self.on_read("HOST_DISK", offset, length)
        os.lseek(self._get_fd(), offset, os.SEEK_SET)
        return os.read(self._get_fd(), length)

    def write(self, path, data, offset, fh):
        length = len(data)
        if self.on_write:
            self.on_write("HOST_DISK", offset, length)
        os.lseek(self._get_fd(), offset, os.SEEK_SET)
        res = os.write(self._get_fd(), data)
        return res

    def flush(self, path, fh):
        if self._fd is not None:
            os.fsync(self._fd)

    def release(self, path, fh):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

class VirtualFAT:
    def __init__(self, image_path, size_mb=64):
        self.image_path = os.path.abspath(image_path)
        self.size_mb = size_mb
        self._on_read = None
        self._on_write = None
        self._fuse_thread = None
        self.fuse_mount_point = os.path.join(os.path.dirname(self.image_path), "vfat_mount")
        self.fuse_image_path = os.path.join(self.fuse_mount_point, "image.bin")
        
        # Pre-kalkulowane offsety dla swapa
        self.manifest_offset = None
        self.manifest_size = None
        self.new_manifest_content = None

        if os.path.exists(self.image_path):
            os.remove(self.image_path)
        
        if not os.path.exists(self.image_path):
            self._create_empty_image()
            self._format_fat32()

        if not os.path.exists(self.fuse_mount_point):
            os.makedirs(self.fuse_mount_point, exist_ok=True)

    def prepare_manifest_patch(self, original_upd_path: pathlib.Path, original_manifest: bytes, new_manifest: bytes) -> bool:
        Log.info("Pre-calculating offsets for manifest swap...")
        
        original_manifest_bytes = original_manifest
        new_manifest_bytes = new_manifest
        
        original_upd = original_upd_path.read_bytes()
        
        manifest_offset_in_upd = original_upd.find(original_manifest_bytes)
        
        if manifest_offset_in_upd == -1:
            Log.error("Cannot find original manifest in original UPD file?!")
            Log.error(f"Looking for: {original_manifest_bytes[:50]}...")
            Log.error(f"First 200 bytes of UPD: {original_upd[:200]}")
            print()
            Log.warning("^^^ this error should't ever happen")
            return False
        
        found_fragment = original_upd[manifest_offset_in_upd:manifest_offset_in_upd + len(original_manifest_bytes)]
        if found_fragment != original_manifest_bytes:
            Log.error("Found fragment doesn't match original manifest!")
            return False
        
        self.manifest_size = len(original_manifest_bytes)

        if len(new_manifest_bytes) < self.manifest_size:
            Log.error("Manifest too small")
            return False
        elif len(new_manifest_bytes) > self.manifest_size:
            Log.error("Manifest too large")
            return False
        else:
            self.new_manifest_content = new_manifest_bytes
        
        file_signature = original_upd[0x8000:0x9000]
        self.file_offset_in_fat = self._find_bytes_in_image(file_signature)

        if self.file_offset_in_fat != -1:
            self.file_offset_in_fat -= 0x8000
        
        self.manifest_offset = self.file_offset_in_fat + manifest_offset_in_upd
        
        Log.success(f"Manifest patch prepared successfully:")
        Log.success(f"  - Manifest length: {len(new_manifest_bytes)} bytes")
        Log.success(f"  - Manifest offset in original UPD: {manifest_offset_in_upd} (0x{manifest_offset_in_upd:x})")
        Log.success(f"  - UPD file offset in FAT image: {self.file_offset_in_fat} (0x{self.file_offset_in_fat:x})")
        Log.success(f"  - Final manifest offset in FAT: {self.manifest_offset} (0x{self.manifest_offset:x})")
        
        with open(self.image_path, 'rb') as f:
            f.seek(self.manifest_offset)
            found_manifest = f.read(self.manifest_size)
            if found_manifest == original_manifest_bytes:
                Log.success("  - Verification passed - manifest found correctly in FAT image!")
            else:
                Log.error("  ! Verification failed - manifest mismatch in FAT image!")
                Log.error(f"    Expected: {original_manifest_bytes[:50]}...")
                Log.error(f"    Found: {found_manifest[:50]}...")
                return False
        
        return True
    
    def _find_bytes_in_image(self, pattern: bytes, search_start: int = 0, search_end: int = None) -> int:
        with open(self.image_path, 'rb') as f:
            if search_end:
                f.seek(search_start)
                data = f.read(search_end - search_start)
                offset = search_start + data.find(pattern)
            else:
                if search_start > 0:
                    f.seek(search_start)
                    data = f.read()
                    offset = search_start + data.find(pattern)
                else:
                    data = f.read()
                    offset = data.find(pattern)
            
            return offset if offset != -1 else -1
    
    def fast_swap_manifest(self) -> bool:
        if self.manifest_offset is None or self.new_manifest_content is None:
            Log.error("Fast swap not prepared! Call prepare_manifest_patch() first")
            print()
            Log.warning("^^^ this error should't ever happen")
            return False

        Log.warning("!!!!! SWAPPING MANIFEST !!!!!")
        
        fd = os.open(self.image_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.lseek(fd, self.manifest_offset, os.SEEK_SET)
            os.write(fd, self.new_manifest_content)
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        
        if self._on_write:
            self._on_write("HOST_DISK", self.manifest_offset, len(self.new_manifest_content))
        
        return True

    def _create_empty_image(self):
        with open(self.image_path, 'wb') as f:
            f.truncate(self.size_mb * 1024 * 1024)

    def _format_fat32(self):
        subprocess.run(["mkfs.vfat", "-F", "32", "-n", "UCONNEXT", self.image_path], check=True)

    def _run_mtools(self, command, **kwargs):
        env = os.environ.copy()
        env["MTOOLS_LOWER_CASE"] = "1"
        env["MTOOLS_SKIP_CHECK"] = "1"
        return subprocess.run(command, env=env, **kwargs)

    def start_monitoring(self):
        if self._fuse_thread:
            return

        try:
            subprocess.run(["fusermount", "-u", self.fuse_mount_point], stderr=subprocess.DEVNULL)
        except:
            pass

        def run_fuse():
            FUSE(
                FATImageFUSE(self.image_path, self._on_read, self._on_write), 
                self.fuse_mount_point, 
                foreground=True, 
                nothreads=True,
                attr_timeout=0,
                entry_timeout=0,
                direct_io=True # disable caching
            )

        self._fuse_thread = threading.Thread(target=run_fuse, daemon=True)
        self._fuse_thread.start()
        
        for _ in range(10):
            if os.path.exists(self.fuse_image_path):
                break
            time.sleep(0.5)

    def stop_monitoring(self):
        if self._fuse_thread:
            subprocess.run(["fusermount", "-u", self.fuse_mount_point])
            self._fuse_thread.join(timeout=2)
            self._fuse_thread = None

    def add_file(self, path, content):
        path = str(path).replace("\\", "/").lower()
        try:
            self._run_mtools(
                ["mcopy", "-o", "-i", self.image_path, "-", f"::{path}"],
                input=content,
                check=True
            )
            if self._on_write:
                self._on_write(path, 0, len(content))
        except subprocess.CalledProcessError as e:
            Log.warning(f"Error adding file {path}: {e}")

    def add_from_path(self, path, input_path):
        try:
            input_path = pathlib.Path(input_path)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file does not exist: {input_path}")
            fat_path = str(path).replace("\\", "/").lower()
            self._run_mtools(
                ["mcopy", "-o", "-i", self.image_path, str(input_path), f"::{fat_path}"],
                check=True
            )
            if self._on_write:
                self._on_write(fat_path, 0, input_path.stat().st_size)
        except subprocess.CalledProcessError as e:
            Log.warning(f"Error adding file {path}: {e}")

    def edit_file(self, path, offset, content):
        path = str(path).replace("\\", "/").lower()
        tmp_file = f"/tmp/fat_edit_{os.getpid()}"
        try:
            self._run_mtools(["mcopy", "-i", self.image_path, f"::{path}", tmp_file], check=True)
            with open(tmp_file, "r+b") as f:
                f.seek(offset)
                f.write(content)
            self._run_mtools(["mcopy", "-o", "-i", self.image_path, tmp_file, f"::{path}"], check=True)
            if self._on_write:
                self._on_write(path, offset, len(content))
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    def set_callbacks(self, on_read=None, on_write=None):
        self._on_read = on_read
        self._on_write = on_write

    def log_operation(self, is_write, path, offset, length):
        if is_write and self._on_write:
            self._on_write(path, offset, length)
        elif not is_write and self._on_read:
            self._on_read(path, offset, length)