import pytest
from core.normalization import normalize_name, normalize_account_id, normalize_pincode, normalize_card_number


class TestNormalizeName:
    def test_basic(self):
        assert normalize_name("Nithin Jain") == "nithin jain"

    def test_extra_whitespace(self):
        assert normalize_name("Nithin  Jain") == "nithin jain"

    def test_trailing_whitespace(self):
        assert normalize_name("  Nithin Jain  ") == "nithin jain"

    def test_unicode_nfc(self):
        # NFC decomposed → composed (and casefold)
        import unicodedata
        decomposed = unicodedata.normalize("NFD", "Nithin Jain")
        assert normalize_name(decomposed) == "nithin jain"

    def test_case_insensitive(self):
        # User-typed "rahul mehta" must match account "Rahul Mehta"
        assert normalize_name("RAHUL MEHTA") == normalize_name("Rahul Mehta")
        assert normalize_name("rahul mehta") == normalize_name("Rahul Mehta")


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
