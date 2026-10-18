"""Maps Windows event providers and patterns to readable incident labels."""

from config import PROVIDER_CLASSIFICATIONS


def classify_provider(provider):
    """Return a configured display label, or clean a raw provider name.

    The fallback removes a known Microsoft/Windows prefix and replaces hyphens
    with spaces. This friendly label is not a registered Windows identifier.
    """

    if provider in PROVIDER_CLASSIFICATIONS:
        return PROVIDER_CLASSIFICATIONS[provider]

    cleaned_provider = provider
    for prefix in ("Microsoft-Windows-", "Microsoft-", "Windows-"):
        if cleaned_provider.startswith(prefix):
            cleaned_provider = cleaned_provider[len(prefix):]
            break

    return cleaned_provider.replace("-", " ")


# cspell:ignore dcom
def classify_dcom_event(event_id, message):
    """Choose a DCOM label from event_id and an already lowercased message.

    Handles service-start failures, registration timeouts, and permission
    warnings, with message-based labels for recognized applications. Unmatched
    cases return DCOM Issue; this helper performs no OS or network lookup.
    """

    if event_id == 10005 and "gamingservices" in message:
        return "Gaming Services DCOM Startup Failure"
    if event_id == 10010:
        return "DCOM Server Start Timeout"
    if event_id != 10016:
        return "DCOM Issue"

    permission_labels = {
        "windows.securitycenter": "Security Center DCOM Permission Warning",
        "openai.chatgpt-desktop": "ChatGPT Desktop DCOM Permission Warning",
        "openai.codex": "Codex Permission Warning",
    }
    return next(
        (label for token, label in permission_labels.items() if token in message),
        "DCOM Permission Warning",
    )


def classify_service_event(message):
    """Label a service termination using an already lowercased message.

    Recognized Defender names produce Defender Service Crash; otherwise the
    label is Service Terminated Unexpectedly. The caller checks the event ID.
    """

    if "microsoft defender antivirus service" in message:
        return "Defender Service Crash"
    return "Service Terminated Unexpectedly"


def classify_event(event):
    """Classify an event using provider, event_id, and message evidence.

    Expects those three keys and string provider/message values. Known rules
    cover DNS, WLAN, TCP/IP, DCOM, service termination, and delayed shutdown;
    unmatched events use classify_provider. Returns a label without mutation.
    """

    if event.get("detection_classification"):
        return event["detection_classification"]
    provider = event["provider"]
    event_id = event["event_id"]
    message = event["message"].lower()

    provider_classifications = {
        ("Microsoft-Windows-DNS-Client", 1014): "DNS Resolution Timeout",
        ("Microsoft-Windows-WLAN-AutoConfig", 10002): "WLAN Module Stopped",
        ("Tcpip", 4266): "UDP Ephemeral Port Exhaustion",
    }
    classification = provider_classifications.get((provider, event_id))
    if classification:
        return classification
    if provider == "DCOM":
        return classify_dcom_event(event_id, message)
    if provider == "Service Control Manager" and event_id == 7031:
        return classify_service_event(message)
    if provider == "winsrvext" and "delaying system shutdown" in message:
        return "Application Delayed System Shutdown"
    return classify_provider(provider)


def classify_incident(incident):
    """Return a raw-provider-to-display-label dictionary for incident['providers'].

    This labels the contributing providers. build_incident chooses the overall
    incident classification separately from per-event classification counts.
    """

    return {
        provider: classify_provider(provider)
        for provider in incident["providers"]
    }
