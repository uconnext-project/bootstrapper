import pathlib
import signal
import time
import os
import shutil
import threading
import fcntl
from colorama import Fore
from functionfs.gadget import Gadget, ConfigFunctionKernel
from enum import Enum

from app.virtual_fat import VirtualFAT
from app.log import Log

read_debug = False

class MassStorageFunction(ConfigFunctionKernel):
    type_name = 'mass_storage'

    def __init__(self, fat_image_path, name=None):
        self._fat_image_path = fat_image_path
        self._lun_dir_list = []
        super().__init__(
            config_dict={'stall': '1'},
            name=name,
        )

    def start(self, path):
        lun_dir = os.path.join(path, 'lun.0')
        if not os.path.exists(lun_dir):
            os.mkdir(lun_dir)
            self._lun_dir_list.append(lun_dir)
        
        with open(os.path.join(lun_dir, 'removable'), 'w') as f:
            f.write('1')

        if os.path.exists(os.path.join(lun_dir, 'nofua')): # disable cache because it interferes with read detection
            with open(os.path.join(lun_dir, 'nofua'), 'w') as f:
                f.write('0')

        with open(os.path.join(lun_dir, 'file'), 'w') as lun_file:
            lun_file.write(self._fat_image_path)
        
        super().start(path)


    def kill(self):
        for lun_path in self._lun_dir_list:
            pass
        super().kill()

class Loader:
    @staticmethod
    def cleanup():
        base_path = '/sys/kernel/config/usb_gadget/'
        if not os.path.exists(base_path):
            return
        
        for g in os.listdir(base_path):
            g_path = os.path.join(base_path, g)
            if os.path.isdir(g_path):
                Log.info(f"Cleaning up gadget: {g}")
                udc_path = os.path.join(g_path, 'UDC')
                if os.path.exists(udc_path):
                    try:
                        with open(udc_path, 'w') as f:
                            f.write('\n')
                    except Exception as e:
                        Log.error(f"Failed to detach UDC: {e}, continuing anyway")
                        Log.warning("if you see this multiple times, you need to reboot your device to clear up the gadget files")
                
                try:
                    os.system(f"find {g_path} -depth -type d -exec rmdir {{}} \\; 2>/dev/null")
                except:
                    pass

    @staticmethod
    def init(original_upd_path: pathlib.Path, payload_lua_path: pathlib.Path, original_manifest: bytes, new_manifest: bytes):
        Loader.cleanup()
        
        vfat = VirtualFAT("mass_storage.img", 128)
        
        Log.info("Creating VirtualFAT image with files...")
        vfat.add_file("README.TXT", b"Hello from uconnext!")
        vfat.add_from_path("swdl.upd", original_upd_path)
        vfat.add_from_path("payload.lua", payload_lua_path)
        
        if not vfat.prepare_manifest_patch(original_upd_path, original_manifest, new_manifest):
            Log.error("Failed to prepare manifest patch! Aborting.")
            return
        
        start_time = time.perf_counter()
        
        class State(Enum):
            WAITING = 1
            INIT_READ = 2
            COPYING = 3
            VERIFYING = 4
            LOADING_PAYLOAD = 5
            PAYLOAD_SUCCESSFUL = 6

        state = State.WAITING
        
        def run_swap():
            nonlocal state
            state = State.VERIFYING
            Log.info("State change: Verifying")
            
            time_swap_start = time.perf_counter_ns()
            
            if vfat.fast_swap_manifest():
                time_swap = time.perf_counter_ns() - time_swap_start
                Log.success(f"Manifest swapped in {time_swap} ns ({time_swap/1000000:.2f} ms)")
            else:
                Log.error("Fast swap failed!")

        swap_timer = threading.Timer(4, run_swap)

        last_lens = []

        if os.path.exists("log.txt"):
            os.remove("log.txt")

        def log(message):
            elapsed = time.perf_counter() - start_time
            if read_debug:
                Log.debug(f"[{elapsed:07.4f}s] {message}")
            with open("log.txt", "a") as f:
                f.write(f"[{elapsed:07.4f}s] {message}\n")
        
        def on_write(path, offset, length):
            nonlocal state
            if state == State.LOADING_PAYLOAD:
                state = State.PAYLOAD_SUCCESSFUL
                Log.success("Payload ran successfully!")
            log(f"WRITE to {path} at {offset}, length {length}")
            
        def on_read(path, offset, length):
            nonlocal state, swap_timer
            last_lens.append(length)
            if state == State.WAITING:
                state = State.INIT_READ
                Log.info("State change: Init read")
            elif state == State.INIT_READ:
                last15 = last_lens[-15:]
                correct = True
                for read in last15:
                    if read not in (8192, 16384):
                        correct = False
                if correct:
                    state = State.COPYING
                    Log.info("State change: Copying")
                    Log.success("Sit back and watch the magic begin!")
            elif state == State.COPYING:
                if swap_timer:
                    swap_timer.cancel()
                swap_timer = threading.Timer(4, run_swap)
                swap_timer.start()
            elif state == State.VERIFYING:
                Log.info("State change: Loading payload")
                state = State.LOADING_PAYLOAD
            log(f"READ from {path} at {offset}, length {length}")

        vfat.set_callbacks(on_read=on_read, on_write=on_write)
        vfat.start_monitoring()

        with Gadget(
                config_list=[
                    {
                        'function_list': [
                            MassStorageFunction(vfat.fuse_image_path),
                        ],
                        'MaxPower': 500,
                        'lang_dict': {
                            0x409: {
                                'configuration': 'uconnext Mass Storage USB Drive',
                            },
                        },
                    }
                ],
                idVendor=0x1d6b,
                idProduct=0x0104,
                lang_dict={
                    0x409: {
                        'product': 'uconnext Mass Storage USB Drive',
                        'manufacturer': 'uconnext Mass Storage Corp.',
                    },
                },
        ):
            try:
                Log.info("Gadget is active. Press Ctrl+C to stop.")
                Log.info("You can connect the device to the car now.")
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                Log.warning("Stopping...")