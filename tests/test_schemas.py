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
