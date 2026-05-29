import os, sys, tempfile
import pathlib

try:
    from colorama import Fore
except ImportError:
    print("Please install colorama to use this script.")
    sys.exit(1)

try:
    import pycdlib
except ImportError:
    print("Please install pycdlib to use this script.")
    sys.exit(1)

try:
    import functionfs
except ImportError:
    print("Please install functionfs to use this script.")
    sys.exit(1)

try:
     import fuse
except ImportError:
    print("Please install fusepy to use this script.")
    sys.exit(1)

logo = f"""{Fore.CYAN}                                                                                        
     ******                          ======     
    *********                      -=======+    
    ***********                  ---=======*    
    *************              -----=====+**    
    **************            ------====****    
    ****************        --------==******    
    ******************    ----------+*******    
    ******** *********** ---------=@********    
    ********  *********+=--------#@ ********    
    ********    *****+=====----*@   ********    
    ********      **========-=@     ********    
    ********      --=======+**      ********    
    ********    ------====******    ********    
    ********  ---------=+*********  ********    
    ********----------=@%*******************    
    *******=--------=%    ******************    
    *****+==-------#@       ****************    
    ***+====-----+@           **************    
    **======---=@               ************    
    +=======--%@                 ***********    
    *=======*@                     *********    
     *====+@                         ******     
      @@@@                                      
                                                {Fore.RESET}"""

def clear():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')




if __name__ == "__main__":
    try:
        from app.loader_gadget import Loader
        from app.iso_creator import create_iso, get_manifest_bytes_from_iso, patch_file
        from app.log import Log
    except ImportError as e:
        print(f"Failed to load one of the modules. {e}")
        sys.exit(1)
    clear()
    print(logo)
    Log.info("-"*10 + "uconnext bootstraper" + "-"*10)

    Log.info("Checking for required files...")

    iso_path = pathlib.Path(__file__).parent.joinpath('input').joinpath('installer.iso')
    payload_lua_path = pathlib.Path(__file__).parent.joinpath('input').joinpath('payload.lua')
    temp_path = pathlib.Path(tempfile.gettempdir()).joinpath('uconnext-installer')

    payload_path = temp_path.joinpath('payload').joinpath("swdl.upd")

    if not iso_path.exists():
        Log.error("Installer ISO not found. Please place it in the 'input' directory.")
        sys.exit(1)

    if not payload_lua_path.exists():
        Log.error("Payload script not found. Please place it in the 'input' directory.")
        Log.debug("P.S. why are you running this if you don't have anything to execute?")
        sys.exit(1)

    Log.success("Paths:")
    Log.success(f"ISO path: {iso_path}")
    Log.success(f"Temp path: {temp_path}")
    Log.success(f"Payload path: {payload_path}")
    Log.success(f"Payload script path: {payload_lua_path}")

    Log.info("Creating minified ISO...")
    if not temp_path.exists():
        temp_path.mkdir(parents=True)

    payload_path.parent.mkdir(parents=True, exist_ok=True)
    if payload_path.exists():
        payload_path.unlink()

    payload_path.touch()

    create_iso([iso_path], payload_path)

    Log.success("ISO created successfully!")

    Log.info("Preparing payload to inject...")
    original_manifest = get_manifest_bytes_from_iso(iso_path)

    new_manifest = b'''local os = os
os.execute("lua -s /fs/usb0/payload.lua &")
return {}'''


    Log.info("Aligning manifest...")
    if len(new_manifest) < len(original_manifest):
        diff = len(original_manifest) - len(new_manifest)
        bogus_comment = b'-- ' + b'.' * (diff - 3)
        new_manifest += bogus_comment
    elif len(new_manifest) > len(original_manifest):
        Log.error("New manifest too large!")
        sys.exit(1)

    Log.success("Payload prepared!")


    Loader.init(
        original_upd_path=payload_path,
        payload_lua_path=payload_lua_path,
        original_manifest=original_manifest,
        new_manifest=new_manifest
    )
