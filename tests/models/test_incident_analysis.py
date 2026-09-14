"""Unit tests for incident analysis models and LLM integration boundaries."""

import io
import importlib
import os
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from models.incident_analysis import DiagnosticStep, IncidentAnalysis
from settings.app_settings import AISettings
import config


def make_incident():
    return {
        "incident_type": "Repeated failed logins",
        "source": "Security",
        "count": 5,
    }


def make_analysis_data(**overrides):
    data = {
        "explanation": "Several failed sign-in attempts were detected.",
        "likely_causes": ["Incorrect credentials", "Brute-force attempt"],
        "recommended_actions": ["Review the account activity"],
        "remediation_notes": ["Reset the password if compromise is suspected"],
        "diagnostic_steps": [
            {
                "description": "Inspect the affected service.",
                "command": "Get-Service",
                "shell": "powershell",
                "risk_level": "safe",
            }
        ],
    }
    data.update(overrides)
    return data


def import_llm_analysis(mock_client):
    """Load llm_analysis against a mock OpenAI SDK without network access."""

    class OpenAIError(Exception):
        pass

    class APIConnectionError(OpenAIError):
        pass

    class APITimeoutError(OpenAIError):
        pass

    class APIStatusError(OpenAIError):
        pass

    class AuthenticationError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    mock_client.__enter__ = Mock(return_value=mock_client)
    mock_client.__exit__ = Mock(return_value=False)
    mock_openai = ModuleType("openai")
    mock_openai.OpenAI = Mock(return_value=mock_client)
    mock_openai.OpenAIError = OpenAIError
    mock_openai.APIConnectionError = APIConnectionError
    mock_openai.APITimeoutError = APITimeoutError
    mock_openai.APIStatusError = APIStatusError
    mock_openai.AuthenticationError = AuthenticationError
    mock_openai.RateLimitError = RateLimitError

    with patch.dict(sys.modules, {"openai": mock_openai}):
        sys.modules.pop("llm_analysis", None)
        llm_analysis = importlib.import_module("llm_analysis")

    return llm_analysis, mock_openai


class DiagnosticStepTests(unittest.TestCase):
    def test_accepts_each_risk_level_without_a_command(self):
        for risk_level in ("safe", "caution", "system_change"):
            with self.subTest(risk_level=risk_level):
                step = DiagnosticStep(description="Review the proposed step.", risk_level=risk_level)
                self.assertEqual(step.risk_level, risk_level)
                self.assertIsNone(step.command)

    def test_accepts_an_explicit_null_command(self):
        step = DiagnosticStep(description="Review related events.", command=None, risk_level="safe")
        self.assertIsNone(step.command)
        self.assertIsNone(step.shell)

    def test_accepts_supported_command_shells(self):
        for shell, command in (("powershell", "Get-Service"), ("cmd", "ver"), ("bash", "pwd")):
            with self.subTest(shell=shell):
                step = DiagnosticStep(description="Inspect the environment.", command=command,
                                      shell=shell, risk_level="safe")
                self.assertEqual(step.model_dump()["shell"], shell)

    def test_legacy_command_without_shell_still_loads(self):
        step = DiagnosticStep(description="Inspect the service.", command="Get-Service", risk_level="safe")
        self.assertIsNone(step.shell)

    def test_rejects_unknown_command_shells(self):
        for shell in ("terminal", "unknown", 123):
            with self.subTest(shell=shell), self.assertRaises(ValidationError) as error:
                DiagnosticStep(description="Inspect the service.", command="Get-Service",
                               shell=shell, risk_level="safe")
            self.assertEqual(error.exception.errors()[0]["loc"], ("shell",))

    def test_shell_must_be_null_without_a_command(self):
        for command in (None, "", "  "):
            with self.subTest(command=command), self.assertRaisesRegex(ValidationError, "shell must be null"):
                DiagnosticStep(description="Review related events.", command=command,
                               shell="powershell", risk_level="safe")

    def test_requires_description_and_risk_level(self):
        for field in ("description", "risk_level"):
            data = {"description": "Inspect the affected service.", "risk_level": "safe"}
            del data[field]
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                DiagnosticStep.model_validate(data)
            self.assertEqual(error.exception.errors()[0]["loc"], (field,))
            self.assertEqual(error.exception.errors()[0]["type"], "missing")

    def test_rejects_unknown_risk_levels(self):
        for risk_level in ("unsafe", "Safe", "", None, 1):
            with self.subTest(risk_level=risk_level), self.assertRaises(ValidationError) as error:
                DiagnosticStep(description="Inspect the affected service.", risk_level=risk_level)
            self.assertEqual(error.exception.errors()[0]["loc"], ("risk_level",))

    def test_rejects_a_non_string_command(self):
        with self.assertRaises(ValidationError) as error:
            DiagnosticStep(description="Inspect the affected service.", command=123, risk_level="safe")
        self.assertEqual(error.exception.errors()[0]["loc"], ("command",))


class IncidentAnalysisTests(unittest.TestCase):
    def setUp(self):
        # unittest runs must also avoid the real user's settings and key store.
        for mocked in (
            patch("settings.app_settings.AppSettings.load", return_value=AISettings(
                True, config.DEFAULT_ANALYSIS_MODEL, 3,
            )),
            patch("settings.credential_store.get_openai_api_key", return_value=None),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_incident_analysis_accepts_complete_analysis(self):
        data = make_analysis_data()
        analysis = IncidentAnalysis(**data)
        self.assertEqual(
            analysis.likely_causes,
            ["Incorrect credentials", "Brute-force attempt"],
        )
        self.assertEqual(
            analysis.recommended_actions,
            ["Review the account activity"],
        )
        self.assertEqual(len(analysis.diagnostic_steps), 1)
        step = analysis.diagnostic_steps[0]
        self.assertIsInstance(step, DiagnosticStep)
        self.assertEqual(step.description, "Inspect the affected service.")
        self.assertEqual(step.command, "Get-Service")
        self.assertEqual(step.shell, "powershell")
        self.assertEqual(step.risk_level, "safe")
        self.assertEqual(analysis.model_dump(mode="json"), data)

    def test_incident_analysis_requires_all_analysis_fields(self):
        for field in make_analysis_data():
            data = make_analysis_data()
            del data[field]
            with self.subTest(field=field), self.assertRaises(ValidationError) as error:
                IncidentAnalysis.model_validate(data)
            self.assertEqual(error.exception.errors()[0]["loc"], (field,))
            self.assertEqual(error.exception.errors()[0]["type"], "missing")

    def test_incident_analysis_accepts_an_empty_diagnostic_steps_list(self):
        analysis = IncidentAnalysis(**make_analysis_data(diagnostic_steps=[]))
        self.assertEqual(analysis.diagnostic_steps, [])

    def test_incident_analysis_validates_nested_diagnostic_steps(self):
        data = make_analysis_data()
        data["diagnostic_steps"][0]["risk_level"] = "unknown"
        with self.assertRaises(ValidationError) as error:
            IncidentAnalysis.model_validate(data)
        self.assertEqual(error.exception.errors()[0]["loc"], ("diagnostic_steps", 0, "risk_level"))

    def test_incident_analysis_rejects_invalid_diagnostic_step_containers(self):
        for steps in (None, "Inspect the service", {"description": "Inspect the service"}, ["Get-Service"]):
            with self.subTest(steps=steps), self.assertRaises(ValidationError) as error:
                IncidentAnalysis(**make_analysis_data(diagnostic_steps=steps))
            self.assertEqual(error.exception.errors()[0]["loc"][0], "diagnostic_steps")

    def test_llm_analysis_returns_none_when_credentials_are_missing(self):
        mock_client = Mock()
        llm_analysis, mock_openai = import_llm_analysis(mock_client)

        with patch.dict(os.environ, {}, clear=True), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertIsNone(result)
        mock_openai.OpenAI.assert_not_called()
        self.assertIn("credentials are not configured", stderr.getvalue())

    def test_llm_analysis_returns_none_when_openai_request_fails(self):
        mock_client = Mock()
        llm_analysis, mock_openai = import_llm_analysis(mock_client)
        mock_client.responses.parse.side_effect = mock_openai.APIConnectionError()

        with patch.dict(sys.modules, {"openai": mock_openai}), patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertIsNone(result)
        mock_client.responses.parse.assert_called_once()
        self.assertIn("request could not be completed", stderr.getvalue())
        self.assertNotIn("test-key", stderr.getvalue())

    def test_llm_analysis_returns_none_when_structured_output_is_missing(self):
        mock_client = Mock()
        llm_analysis, mock_openai = import_llm_analysis(mock_client)
        mock_client.responses.parse.return_value = Mock(output_parsed=None)

        with patch.dict(sys.modules, {"openai": mock_openai}), patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertIsNone(result)
        self.assertIn("valid structured output", stderr.getvalue())

    def test_llm_analysis_returns_none_when_structured_output_is_invalid(self):
        mock_client = Mock()
        llm_analysis, mock_openai = import_llm_analysis(mock_client)
        mock_client.responses.parse.return_value = Mock(
            output_parsed={"explanation": "Incomplete analysis"}
        )

        with patch.dict(sys.modules, {"openai": mock_openai}), patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertIsNone(result)
        self.assertIn("did not match the analysis schema", stderr.getvalue())

    def test_llm_analysis_returns_valid_structured_output(self):
        mock_client = Mock()
        llm_analysis, mock_openai = import_llm_analysis(mock_client)
        expected = IncidentAnalysis(
            explanation="A DCOM permission warning was detected.",
            likely_causes=["Missing DCOM permission"],
            recommended_actions=["Review component permissions"],
            remediation_notes=["Confirm the warning does not recur"],
            diagnostic_steps=[DiagnosticStep(
                description="Review the related DCOM events.",
                risk_level="safe",
            )],
        )
        mock_client.responses.parse.return_value = Mock(output_parsed=expected)

        with patch.dict(sys.modules, {"openai": mock_openai}), patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ):
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertEqual(result, expected)
        self.assertIsInstance(result.diagnostic_steps[0], DiagnosticStep)
        self.assertIsNone(result.diagnostic_steps[0].command)
        mock_openai.OpenAI.assert_called_once_with(
            api_key="test-key", base_url="https://api.openai.com/v1", timeout=60.0, max_retries=2,
        )
        mock_client.responses.parse.assert_called_once()


if __name__ == "__main__":
    unittest.main()
