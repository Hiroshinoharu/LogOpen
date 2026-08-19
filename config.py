"""Project configuration values."""

from datetime import timedelta

LOG_TYPE = "System"
INCIDENT_BUNDLE_TIMEDELTA = timedelta(minutes=1)
EVENT_SIMILARITY_THRESHOLD = 3
PROVIDER_CLASSIFICATIONS = {
    "DCOM": "Distributed COM",
    "Microsoft-Windows-DNS-Client": "DNS Client",
    "Microsoft-Windows-DNS-Server": "DNS Server",
    "Microsoft-Windows-GroupPolicy": "Group Policy",
    "Microsoft-Windows-GroupPolicy-Client": "Group Policy Client",
    "Microsoft-Windows-GroupPolicy-User": "Group Policy User",
    "Microsoft-Windows-GroupPolicy-Machine": "Group Policy Machine",
    "Microsoft-Windows-WLAN-AutoConfig": "WLAN AutoConfig",
    "Service Control Manager": "Service Control Manager",
    "Tcpip": "TCP/IP",
    "winsrvext": "Windows Server Extension",
}
