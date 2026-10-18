# Event sources, detection rules, and monitoring

LogOpen reads local Windows administrative and operational event channels using
`EvtQuery`, `EvtNext`, and XML rendering. A missing/inaccessible channel produces
an explicit coverage warning; it never silently substitutes the Application log.
Analytic/debug trace channels, remote collection, `.evtx` import, ETL traces, and
Linux/text log parsing are not implemented in this version.

## Choosing sources

Open **Scan scope…**, choose a profile, and adjust its channel list as needed.
Profiles are suggestions, not a guarantee that every channel exists or is enabled.

| Profile | Suggested sources |
| --- | --- |
| General troubleshooting | System, Application |
| Security | System, Application, Security, Defender/Operational, PowerShell/Operational |
| Networking | System, WLAN-AutoConfig/Operational, DNS-Client/Operational |
| Performance and updates | System, Application, Diagnostics-Performance/Operational, WindowsUpdateClient/Operational |
| Custom channels | Any selected administrative/operational channels |

**Browse channels…** enumerates channels registered on this PC, showing their
type, logging state, description, and access status. Search does not uncheck
hidden selections. **Check selected access** probes a query and at most one event
per checked channel; it does not save that event. Access is checked under the
current process identity. Security commonly needs additional privileges. LogOpen
does not elevate itself, enable logging, or change audit policy.

A disabled channel may contain older events, but cannot establish complete
coverage for the current period. Such scans remain incomplete. The record cap
counts all events in the requested time window, including events later rejected
by severity/rules. At the cap, coverage is conservatively incomplete even if the
last inspected record happened to be the final available record.

## Detection behavior

Critical, Error, Warning, and Failure Audit events are retained. Built-in rules
also retain selected lower-severity events, such as audit-log clearance, account
lockouts, Defender actions/protection changes, Wi-Fi disconnections, and boot or
shutdown performance reports. Ordinary PowerShell script-block records are not
included automatically merely because the Security profile selected that channel.
Use a specific custom rule if those informational records are needed.

Every rule matches the **provider and event ID**, plus an exact channel or `*`.
An event ID from an unrelated provider cannot trigger that rule. Labels describe
the recorded event: a failed login is not automatically called an intrusion,
and a normal Wi-Fi disconnect is not automatically called a failure.

Windows' original `level` remains in each event. `detection_severity` is a
separate rule assessment used, alongside the native level, for incident scoring.
Custom rule labels/assessments take precedence over built-in rules for the same
event; they cannot lower its original Windows severity. Rule severity contributes
to a score; it is not itself the final Low/Medium/High/Critical incident priority.
Grouping still uses the existing machine, provider/component, and time heuristics;
a timeline or correlation ID alone does not prove causation.

## Custom rules

Use **Scan scope… → Detection rules… → Custom rules**. The editor validates the
whole JSON array before Save. Save the outer scope dialog to persist changes.
Rules are non-secret desktop preferences stored with the scope in QSettings;
they are also recorded in saved reports. CLI scans use `config.py` defaults and
built-in rules unless a caller explicitly supplies `ScanOptions(rules_json=...)`.

```json
[
  {
    "id": "my-app-start-failure",
    "channel": "Application",
    "provider": "MyApplication",
    "event_ids": [1000, 1001],
    "label": "My Application Startup Failure",
    "severity": "Error",
    "enabled": true
  }
]
```

Replace the provider and IDs with verified identifiers from your source events.
`channel` may be `*`; `provider` must be exact. `severity` accepts Information,
Warning, Error, or Critical. `enabled` defaults to true and disables only that
custom rule when false. Up to 100 custom rules are allowed, with 1–100 event IDs
per rule. No commands, Python expressions, or regex execution are supported.
Changing rules invalidates disappearance comparisons against older scopes;
changing only automatic AI does not change the evidence scope.

## Timelines

An incident's Timeline tab shows its events; **View → Timeline of filtered
incidents…** combines currently visible incidents across logs. Timestamps are
ordered by absolute time and displayed in the computer's local timezone. The
evidence pane preserves original timestamps, payload fields, record numbers,
process/thread IDs, correlation IDs where supplied, and matching rule labels.
Gap values refer to the full chronological dataset, not just filtered rows.

## Monitoring and notifications

Monitoring is opt-in and never starts on launch. It snapshots the current scope;
stop it before changing that scope. A scan is followed by a 30–3,600-second wait.
Foreground scans, manual AI, and modal dialogs defer the next poll. Results go to
normal scan history and `reports/incidents.json`; repeated scans therefore consume
disk space. History retention/pruning is not automated.

The first noncancelled scan establishes a silent baseline. Later notifications
require unseen evidence timestamped since monitoring started, an active review
state, and High/Critical incident priority. Ignored/acknowledged incidents are
remembered silently. Up to 100,000 evidence identities are retained per session
to suppress overlapping-window duplicates; older identities may expire. Stopping
and restarting monitoring begins a fresh baseline. Desktop notifications depend
on Windows/Qt tray support and OS notification settings; the in-app monitor status
and saved scan remain available regardless. Notification text contains only counts.

Monitoring always disables automatic AI, regardless of saved AI preferences.
Explicit manual analysis remains available between polls and uses normal AI
settings. Closing or stopping requests cooperative cancellation; a running OS
read/save finishes before its worker is destroyed. Monitoring is not a Windows
service or an instant subscription. Events lost to log rollover, disabled logging,
record caps, or downtime cannot be recovered by polling. Coverage warnings must
be considered alongside any alerts.

## API and rule references

- [Microsoft: querying for events](https://learn.microsoft.com/en-us/windows/win32/wes/querying-for-events)
- [pywin32 Evt API implementation and signatures](https://github.com/mhammond/pywin32/blob/main/win32/src/win32evtlog.i)
- [Microsoft: Defender event IDs](https://learn.microsoft.com/en-us/defender-endpoint/troubleshoot-microsoft-defender-antivirus)
- [Microsoft: failed logon event 4625](https://learn.microsoft.com/en-us/windows/security/threat-protection/auditing/event-4625)
- [Microsoft: audit-log clearance event 1102](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-1102)
- [Microsoft: Windows Update installation failure event 20](https://learn.microsoft.com/en-us/troubleshoot/windows-server/installing-updates-features-roles/troubleshoot-windows-update-error-0x80070002)

The legacy `get_classic_events` helper remains for explicit compatibility use
with System, Application, or Security only. The application scan workflow always
uses the modern reader; legacy-reader tests remain separate from modern API tests.
