import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).with_name("totp_code_generator_wxpython.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

class TOTPGeneratorSourceTests(unittest.TestCase):
    def test_uses_pyotp_totp_with_selected_digit_count(self):
        self.assertIn("pyotp.TOTP(secret, digits=digits).now()", SOURCE)

    def test_runs_in_backend_directory_and_project_venv(self):
        self.assertIn('BACKEND_DIR / ".venv" / "Scripts" / "python.exe"', SOURCE)
        self.assertIn('cwd=str(BACKEND_DIR)', SOURCE)

    def test_code_display_is_read_only(self):
        self.assertIn("wx.TE_READONLY | wx.TE_CENTRE | wx.BORDER_SIMPLE", SOURCE)

    def test_default_is_six_digits(self):
        self.assertIn('self.digit_choice.SetSelection(0)', SOURCE)
        self.assertIn('choices=["6 digits", "7 digits", "8 digits"]', SOURCE)

    def test_auto_copy_after_generation(self):
        self.assertIn("if self.copy_code(code):", SOURCE)
        self.assertIn("self.generated_code = code", SOURCE)

if __name__ == "__main__":
    unittest.main()
