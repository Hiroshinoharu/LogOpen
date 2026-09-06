"""Unit tests for incident analysis models and LLM integration boundaries."""

import io
import importlib
import os
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from models.incident_analysis import IncidentAnalysis


def make_incident():
    return {
        "incident_type": "Repeated failed logins",
        "source": "Security",
        "count": 5,
    }


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


class IncidentAnalysisTests(unittest.TestCase):
    def test_incident_analysis_accepts_complete_analysis(self):
        analysis = IncidentAnalysis(
            explanation="Several failed sign-in attempts were detected.",
            likely_causes=["Incorrect credentials", "Brute-force attempt"],
            recommended_actions=["Review the account activity"],
            remediation_notes=["Reset the password if compromise is suspected"],
        )

        self.assertEqual(
            analysis.likely_causes,
            ["Incorrect credentials", "Brute-force attempt"],
        )
        self.assertEqual(
            analysis.recommended_actions,
            ["Review the account activity"],
        )

    def test_incident_analysis_requires_all_analysis_fields(self):
        with self.assertRaises(ValidationError):
            IncidentAnalysis(
                explanation="An explanation",
                likely_causes=["A cause"],
                recommended_actions=["An action"],
            )

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
        )
        mock_client.responses.parse.return_value = Mock(output_parsed=expected)

        with patch.dict(sys.modules, {"openai": mock_openai}), patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-key"}
        ):
            result = llm_analysis.analyse_incident_with_llm(make_incident())

        self.assertEqual(result, expected)
        mock_openai.OpenAI.assert_called_once_with()
        mock_client.responses.parse.assert_called_once()


if __name__ == "__main__":
    unittest.main()
