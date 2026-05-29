local os = os

-- source dbus env and show popup
os.execute("(eval 'cat /tmp/envars.sh' && dbus-send --type=method_call --dest='com.harman.service.HMIGateWay' /com/harman/service/HMIGateWay com.harman.ServiceIpc.Invoke string:'displayGlobalPopup' string:'{\"timeout\":\"15000\",\"showonDisplayoff\":\"true\",\"showRunningCategory\":\"true\",\"msg\":\"Thanks, Harman.\\nuid=0(root) gid=0(root)\",\"title\":\"Software Update\"}') > /dev/null 2>&1 &")

os.execute("sleep 1")

os.execute("/bin/df -h > /fs/usb0/diskfree.txt")
os.execute("cp -f /dev/fram/productid /fs/usb0/productid.bin")
os.execute("ls -laR /fs/mmc0 > /fs/usb0/fs-mmc0.txt &")

os.execute()