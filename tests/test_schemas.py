"""
Unit tests for llm/schemas.py — the pydantic structured-output models.

The most important guarantee here is graceful handling of LLM outputs
that are syntactically plausible but semantically invalid (e.g. a date
that doesn't exist on the calendar). Without graceful handling these
surface to the user as a generic "brief technical hiccup" — they should
surface as the appropriate "please share a clearer date" prompt instead.
"""
from __future__ import annotations

from datetime import date

import pytest

from llm.schemas import IdentityExtraction


class TestIdentityExtractionDob:
    def test_valid_iso_date_parses(self):
        m = IdentityExtraction(dob="1990-05-14", user_intent="providing_info")
        assert m.dob == date(1990, 5, 14)
        assert m.dob_ambiguous is False

    def test_valid_leap_year_parses(self):
        # ACC1004 (Rahul Mehta) actually has this DOB — must succeed.
        m = IdentityExtraction(dob="1988-02-29", user_intent="providing_info")
        assert m.dob == date(1988, 2, 29)
        assert m.dob_ambiguous is False

    def test_feb_29_non_leap_year_rerouted_as_ambiguous(self):
        # 1998 is NOT a leap year — Feb 29 doesn't exist. Pydantic's
        # strict date parser would raise ValidationError; our model
        # validator catches that and re-routes to dob_ambiguous=True so
        # the FSM uses its existing "please share a clearer date" path.
        m = IdentityExtraction(dob="1998-02-29", user_intent="providing_info")
        assert m.dob is None
        assert m.dob_ambiguous is True

    @pytest.mark.parametrize("bad_date", [
        "1990-02-30",   # Feb 30 never exists
        "1990-04-31",   # April only has 30 days
        "1990-13-01",   # month 13
        "1990-00-15",   # month 0
        "1990-05-32",   # day 32
        "not-a-date",   # garbage
    ])
    def test_other_invalid_dates_rerouted_as_ambiguous(self, bad_date):
        m = IdentityExtraction(dob=bad_date, user_intent="providing_info")
        assert m.dob is None
        assert m.dob_ambiguous is True

    def test_null_dob_stays_null(self):
        m = IdentityExtraction(dob=None, user_intent="providing_info")
        assert m.dob is None
        assert m.dob_ambiguous is False

    def test_invalid_dob_does_not_clobber_other_fields(self):
        # An invalid DOB must not lose the rest of the extraction.
        m = IdentityExtraction(
            full_name="Nithin Jain",
            dob="1998-02-29",  # invalid
            aadhaar_last4="4321",
            pincode="400001",
            user_intent="providing_info",
        )
        assert m.full_name == "Nithin Jain"
        assert m.dob is None
        assert m.dob_ambiguous is True
        assert m.aadhaar_last4 == "4321"
        assert m.pincode == "400001"

    def test_empty_string_dob_does_not_lose_extraction(self):
        # LLM sometimes returns dob="" when it isn't sure. Previously
        # the model_validator's `and raw_dob` check skipped this case,
        # the empty string fell through to pydantic's date parser,
        # raised ValidationError, and discarded the entire extraction
        # (name / aadhaar / pincode all lost). Now empty/whitespace dob
        # is treated as "no DOB provided" — extraction preserved.
        m = IdentityExtraction(
            full_name="Nithin Jain",
            dob="",
            aadhaar_last4="4321",
            pincode="400001",
            user_intent="providing_info",
        )
        assert m.full_name == "Nithin Jain"
        assert m.dob is None
        assert m.dob_ambiguous is False  # didn't try a date, just empty
        assert m.aadhaar_last4 == "4321"
        assert m.pincode == "400001"

    def test_whitespace_only_dob_treated_as_missing(self):
        m = IdentityExtraction(
            full_name="Nithin Jain",
            dob="   ",
            user_intent="providing_info",
        )
        assert m.dob is None
        assert m.dob_ambiguous is False
        assert m.full_name == "Nithin Jain"


class TestPortableDateFormatting:
    """Catches a non-portable %-d (glibc-only) format regression. Previously
    the DOB confirm-back used strftime('%-d %B %Y'), which crashes on
    macOS BSD strftime and Windows. The fix uses an f-string for the day."""

    def test_extractors_module_has_no_dash_d_format(self):
        # Source-level guard: search the extractors file for %-d / %-m
        # — if any future refactor reintroduces them, this test fails.
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "llm" / "extractors.py"
        content = src.read_text()
        # Strip comments so a comment EXPLAINING the bug doesn't fail the check.
        lines_without_comments = [
            line.split("#", 1)[0] for line in content.splitlines()
        ]
        executable = "\n".join(lines_without_comments)
        assert "%-d" not in executable, "Non-portable %-d found in extractors.py"
        assert "%-m" not in executable, "Non-portable %-m found in extractors.py"

    def test_dob_string_format_works_with_single_digit_day(self):
        # Direct check of the day-formatting expression we use:
        # f"{date.day} {date.strftime('%B %Y')}" — portable everywhere.
        from datetime import date as _date
        d = _date(1990, 5, 1)  # single-digit day
        formatted = f"{d.day} {d.strftime('%B %Y')}"
        assert formatted == "1 May 1990"
        assert "1 " in formatted
