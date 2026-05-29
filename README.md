# Requirements
- An uConnect 3C radio on SW level 17.11+
- A device capable of running Linux and having a dual-mode USB controller
- Python 3.13+, FUSE 1.x and dependencies listed in the pyproject.toml file.
- An original swdl.upd file compatible with your radio variant.

# How to use
1. Get a legit swdl.upd for your radio model and mount / extract it.
2. Place the installer.iso into the input directory.
3. Put your script into input/payload.lua
4. Run the script
5. Connect your device to the radio
6. Wait for your script to execute
7. !!! WARNING !!! If the radio asks to update after / while execution, DO NOT ACCEPT THE UPDATE!
