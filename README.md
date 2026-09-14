# LogOpen

LogOpen analyzes recent Windows Event Log warnings and errors, groups related
events into separate incidents, assigns deterministic classifications and scores,
and produces terminal and JSON reports.

## Requirements and usage

LogOpen runs on Windows. It uses `pywin32` for Windows Event Log access and secure
credential storage, `PyQt6` for the desktop UI and preferences, and `pydantic` for
structured analysis. Install `openai` to use optional AI analysis.

```powershell
python -m pip install pywin32 PyQt6 pydantic openai
python -m ui.main_window
# Or run a scan from the terminal:
python main.py
```

The application reads up to 500 events from each configured log, keeps warning
and error events from the previous 24 hours, prints incident reports, and writes
the latest results to `reports/incidents.json`.

Run the test suite with:

```powershell
python -m pip install pytest
python -m pytest -q
```

`python -m unittest` also runs the existing unittest cases. The full pytest suite
includes settings and AI integration coverage. Tests mock API calls, isolate
preferences in temporary files, and never use real credentials or network access.
`test_real_llm.py` is an explicit manual script; importing it does not call OpenAI.

## AI Settings

Open **Settings** in the desktop header. Enable or disable AI analysis, enter your
own password-masked OpenAI API key, and select **Save Key**. The input is cleared
after saving; stored keys are represented only by **API key configured**. Use
**Remove API Key** to delete the saved credential. Key changes take effect
immediately; **Save Settings** persists the enabled state, model, and analysis
limit. Close discards unsaved preferences. Settings are unavailable during a scan.

Preferences apply to the next scan, including terminal scans. Saved preferences
override `ENABLE_LLM_ANALYSIS`, `DEFAULT_ANALYSIS_MODEL`, and
`MAX_LLM_ANALYSES_PER_RUN` in `config.py`; those values supply initial defaults.
The current model default is `gpt-5.6-luna`. Enter any compatible model identifier;
there is no bundled model catalog. The scan limit is bounded to **1–20**. Invalid
stored models fall back to the default, malformed limits recover to a bounded
default, and unrecognized enabled values or unreadable settings disable AI.

Non-secret preferences use QSettings with organization/application **LogOpen**
(on Windows, `HKEY_CURRENT_USER\Software\LogOpen\LogOpen`). The key is stored
separately as a generic **LogOpen/OpenAI** credential in Windows Credential
Manager, using the existing pywin32 dependency. It persists for the current
Windows user on this machine. No new dependency or plaintext secret fallback is
added. LogOpen never writes a key to configuration, logs, incident dictionaries,
or exported reports. Unexpected AI exceptions are replaced with fixed messages.

Credential precedence is:

1. The securely stored user key.
2. `OPENAI_API_KEY` in the process environment, for development/testing.
3. No credentials: skip AI and continue deterministic analysis.

If secure storage is unavailable, saving/removing keys reports a safe error.
The environment fallback still works; without it, AI is skipped. Removing a saved
key does not clear the environment variable. Disable AI to skip all incident AI
requests, regardless of available credentials. An invalid stored key is reported
as an authentication failure; LogOpen does not silently switch to another key.
The resolved key is passed directly to `OpenAI(api_key=...)` inside the service
layer. Requests use `https://api.openai.com/v1`; `OPENAI_BASE_URL` is intentionally
not used for user credentials.

**Test Connection** saves preferences and checks the saved/environment credential
with [`models.retrieve`](https://developers.openai.com/api/reference/python/resources/models/methods/retrieve).
This reads metadata for the selected model without sending incident data or
generating an analysis. A successful test confirms authentication and model
metadata access; it does not prove inference quota or structured-output support.
Restricted keys need model-read permission for this check.

The test uses a QObject worker on a QThread, a 10-second request timeout, and no
automatic retries. Results are delivered by Qt signals to UI-thread slots. The
dialog prevents overlapping tests and waits for the worker to finish when closed,
including when its parent window closes. Error messages never include raw SDK
exceptions. Explicit testing works even when incident AI analysis is disabled.

Implementation files: `settings/app_settings.py` owns ordinary preferences,
`settings/credential_store.py` owns OS credentials and precedence,
`settings/ai_service.py` creates clients and checks connectivity, and
`ui/settings_dialog.py` owns the settings UI and connection worker. The existing
LLM eligibility, score ordering, and incident processing flow are preserved.

## Diagnostic command shells

Configure the shells used for new AI diagnostic recommendations in `config.py`:

```python
ALLOWED_DIAGNOSTIC_SHELLS = ("powershell", "cmd")
PREFERRED_DIAGNOSTIC_SHELL = "powershell"
```

Supported identifiers are `powershell`, `cmd`, and `bash`. The preferred shell
must be in the allowed list. Enable `bash` only when you have a suitable Bash
environment on Windows, such as WSL or Git Bash. Restart LogOpen after editing
the settings; existing analyses keep their original shell metadata.

Each diagnostic command is labeled PowerShell, Command Prompt, or Bash in the
AI analysis tab. Older commands without metadata show “Shell not specified”.
Steps without commands use `shell: null`. Commands can be selected and copied;
LogOpen does not execute them or install shells.

## Processing flow

```text
Windows Event Logs
    -> collect and normalize events
    -> filter recent warnings and errors
    -> bundle related events into separate incidents
    -> build and classify incident summaries
    -> annotate recurrence metadata
    -> refresh score, priority, reasons, and summary text
    -> terminal report and JSON export
```

Incident bundling is based on provider, event ID, extracted components, and the
configured one-minute time window. Recurrence analysis does not merge incidents;
it enriches each completed summary by comparing its classification and start time
with the other summaries in the current analysis set.

## Scoring

Scores are rule-based and capped at `100`. Every contribution is recorded in the
`incident_score_breakdown` object, and the final score is calculated as:

```text
min(sum(incident_score_breakdown.values()), 100)
```

| Dimension | Rule | Points |
| --- | --- | --- |
| Severity | Error / Warning | 40 / 20 |
| Event count | 3-4 / 5-9 / 10 or more events | 5 / 15 / 30 |
| Duration | Under 1 minute / 1-5 minutes / over 5-15 minutes / over 15 minutes | 0 / 5 / 10 / 15 |
| Recurrence | At least 3 matching classifications in 24 hours | 10 |
| Recurrence | At least 5 matching classifications in 7 days | 20 |
| Classification | Known classification impact | 0-20 |

Priority is derived from the capped score: `Critical` at 80 or above, `High` at
60-79, `Medium` at 40-59, and `Low` below 40.

## Incident report fields

Each exported incident retains source and summary data, including:

- Classification, providers, event IDs, severity, timestamps, event count, and duration.
- `incident_score`, `incident_priority`, and human-readable `incident_score_reasons`.
- `incident_score_breakdown` with `severity`, `event_count`,
  `classification_impact`, `recurrence`, and `duration` values.
- `recurrence_count_24h`, `recurrence_count_7d`, and `is_recurring`.
- `summary_text`, a concise natural-language explanation of the incident and its score.

The current application input window is 24 hours. The recurrence module supports
seven-day comparisons when given seven days of summaries, but a production
seven-day recurrence signal requires collecting or retaining summaries beyond the
current 24-hour input window.

## Repository guide

| File or directory | Purpose |
| --- | --- |
| `main.py` | Application entry point and workflow orchestration. |
| `config.py` | Configures input logs, bundling thresholds, and provider labels. |
| `event_collection.py` | Reads and normalizes raw Windows Event Log records. |
| `event_filtering.py` | Filters events by severity level and time window. |
| `incident_detection.py` | Bundles events, builds summaries, and produces natural-language summary text. |
| `incident_classification.py` | Maps providers and known event patterns to incident classifications. |
| `incident_recurrence.py` | Adds classification-based recurrence metadata to completed summaries. |
| `incident_scoring.py` | Calculates score breakdowns, capped scores, priorities, and score reasons. |
| `terminal_reporting.py` | Renders incident summaries and source messages in the terminal. |
| `json_reporting.py` | Converts report values to JSON-safe data and exports report files. |
| `reports/incidents.json` | Generated output from the latest application run. |
| `tests/helpers.py` | Shared factory for representative event dictionaries. |
| `tests/test_event_collection.py` | Tests Windows Event Log parsing and collection. |
| `tests/test_event_filtering.py` | Tests severity and time-window filtering. |
| `tests/test_incident_classification.py` | Tests provider, event, and incident classification. |
| `tests/test_incident_detection.py` | Tests similarity, bundling, summaries, and summary text. |
| `tests/test_incident_recurrence.py` | Tests recurrence counts, windows, and recurrence scoring. |
| `tests/test_incident_scoring.py` | Tests score rules, breakdowns, duration bands, capping, and priorities. |
| `tests/test_terminal_reporting.py` | Tests terminal output formatting. |
| `tests/test_main.py` | Tests the application workflow orchestration. |
