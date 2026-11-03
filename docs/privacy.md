# Privacy and local data

LogOpen reads Windows event logs without modifying them. Scans, event messages,
AI results, notes, review decisions and troubleshooting checks are stored locally
in history.sqlite3 under the app data directory. Saved API keys are kept in
Windows Credential Manager; preferences use current-user settings. Exports are
stored where you choose, and the latest scan report is written to reports/incidents.json.

Manual AI requests show the exact incident input, instructions and response
schema before sending. Common usernames, emails, computer names and paths are
masked by default. Additional exact values can be masked in the preview. Masking
is best effort: review the entire preview. It does not change original evidence.

Automatic AI sharing is off until explicitly enabled under **Privacy and local
data**. Enabling it allows selected incidents and coverage to be sent without
individual previews, subject to AI settings and the per-scan limit. Live monitoring
does not run automatic AI. Local notes are excluded from AI input. The CLI uses
the same masking and automatic-sharing preferences.

Requests go to OpenAI with your key and can incur API charges. LogOpen sets
`store=False` for response storage; this does not promise zero retention.
OpenAI's abuse-monitoring, caching and account-specific policies still apply:
https://developers.openai.com/api/docs/guides/your-data

Opening Settings with a configured key queries OpenAI model metadata without
sending event details. Update checks and downloads contact GitHub. LogOpen does
not automatically upload crash reports, event logs or support reports. The
problem-report dialog prepares a local report that you can edit, copy or save.
Opening the GitHub issue form does not attach its content.

**Help > Privacy and local data** (also accessible from Settings > App) lets you
clear the history database or remove the saved API key. Deletion requires explicit
confirmation and waits until monitoring/scans/requests are stopped. Removing the
saved key also disables AI and automatic sharing. Environment keys are not removed.

Uninstall preserves history, preferences and credentials. Clearing local history
does not delete Windows event logs, manually exported/imported reports, the latest
JSON report, cloud backups, filesystem backups or data already sent to a provider.
Remove exported files separately if needed. Database deletion is not a guarantee
of forensic erasure from storage or backups.
