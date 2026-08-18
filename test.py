import win32evtlog   

event = win32evtlog.OpenEventLog("", "System")
print(dir(event))