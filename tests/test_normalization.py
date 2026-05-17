import pytest
from core.normalization import normalize_name, normalize_account_id, normalize_pincode, normalize_card_number


class TestNormalizeName:
    def test_basic(self):
        assert normalize_name("Nithin Jain") == "Nithin Jain"

    def test_extra_whitespace(self):
        assert normalize_name("Nithin  Jain") == "Nithin Jain"

    def test_trailing_whitespace(self):
        assert normalize_name("  Nithin Jain  ") == "Nithin Jain"

    def test_unicode_nfc(self):
        # NFC decomposed → composed
        import unicodedata
        decomposed = unicodedata.normalize("NFD", "Nithin Jain")
        assert normalize_name(decomposed) == "Nithin Jain"

    def test_preserves_case(self):
        # Should NOT lowercase
        assert normalize_name("NITHIN JAIN") == "NITHIN JAIN"


class TestNormalizeAccountId:
    def test_uppercase(self):
        assert normalize_account_id("acc1001") == "ACC1001"

    def test_spaces(self):
        assert normalize_account_id("ACC 1001") == "ACC1001"

    def test_hyphens(self):
        assert normalize_account_id("ACC-1001") == "ACC1001"

    def test_invalid_prefix(self):
        assert normalize_account_id("XYZ1001") is None

    def test_valid(self):
        assert normalize_account_id("ACC1001") == "ACC1001"


class TestNormalizePincode:
    def test_spaced(self):
        assert normalize_pincode("4 0 0 0 0 1") == "400001"

    def test_no_spaces(self):
        assert normalize_pincode("400001") == "400001"


class TestNormalizeCardNumber:
    def test_spaces(self):
        assert normalize_card_number("4532 0151 1283 0366") == "4532015112830366"

    def test_no_spaces(self):
        assert normalize_card_number("4532015112830366") == "4532015112830366"
