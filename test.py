"""Minimal scratch script for inspecting the Windows event log handle API."""

import win32evtlog   

event = win32evtlog.OpenEventLog("", "System")
print(dir(event))
