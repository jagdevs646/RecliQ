"""
TOTP Code Generator (wxPython)
--------------------------------
A small Windows desktop utility for the project's bootstrap admin TOTP code.

Important:
- Standard TOTP uses 6 digits by default and normally rotates every 30 seconds.
- Change the digit count only if the server/verifier is configured for the same count.
- This app reads BOOTSTRAP_ADMIN_TOTP_SECRET from the project's backend/.env.
"""

import subprocess
import sys
from pathlib import Path

import wx


PROJECT_DIR = Path(
    r"D:\Jagdevs INDEX\C\Cources\Udemy Courses\Python\Projects"
    r"\Reconciliation Project\SaaS - version"
)
BACKEND_DIR = PROJECT_DIR / "backend"
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"

TOTP_SCRIPT = (
    "from dotenv import dotenv_values; "
    "import pyotp, sys; "
    "secret = dotenv_values('.env').get('BOOTSTRAP_ADMIN_TOTP_SECRET'); "
    "digits = int(sys.argv[1]); "
    "print(pyotp.TOTP(secret, digits=digits).now() "
    "if secret else 'BOOTSTRAP_ADMIN_TOTP_SECRET is missing from .env')"
)


def generate_totp_code(digits: int = 6) -> str:
    """Generate a TOTP code by invoking the project's virtual environment."""
    if digits not in (6, 7, 8):
        raise ValueError("Choose 6, 7, or 8 digits.")

    if not BACKEND_DIR.is_dir():
        raise FileNotFoundError(f"Backend folder was not found:\n{BACKEND_DIR}")
    if not VENV_PYTHON.is_file():
        raise FileNotFoundError(
            f"Project virtual-environment Python was not found:\n{VENV_PYTHON}"
        )

    result = subprocess.run(
        [str(VENV_PYTHON), "-c", TOTP_SCRIPT, str(digits)],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "Unknown error").strip()
        raise RuntimeError(details)

    output = result.stdout.strip()
    if output == "BOOTSTRAP_ADMIN_TOTP_SECRET is missing from .env":
        raise RuntimeError(output)
    if not output.isdigit() or len(output) != digits:
        raise RuntimeError(
            "The command did not return a valid TOTP code. "
            f"Received: {output or '(empty output)'}"
        )
    return output


class TOTPGeneratorFrame(wx.Frame):
    def __init__(self):
        super().__init__(
            parent=None,
            title="RecliQ Admin Desktop Sign-in",
            size=(440, 290),
            style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX),
        )
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        heading = wx.StaticText(panel, label="Admin desktop sign-in")
        heading.SetFont(
            wx.Font(13, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        )
        outer.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        subtitle = wx.StaticText(
            panel, label="Generate a one-time code for your RecliQ admin account."
        )
        subtitle.SetForegroundColour(wx.Colour(100, 112, 118))
        outer.Add(subtitle, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        controls = wx.BoxSizer(wx.HORIZONTAL)
        controls.Add(
            wx.StaticText(panel, label="Code length"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.digit_choice = wx.Choice(panel, choices=["6 digits", "7 digits", "8 digits"])
        self.digit_choice.SetSelection(0)
        controls.Add(self.digit_choice, 0, wx.RIGHT, 10)

        self.generate_button = wx.Button(panel, label="Generate")
        self.generate_button.SetMinSize((104, 32))
        controls.Add(self.generate_button, 0)
        outer.Add(controls, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        code_row = wx.BoxSizer(wx.HORIZONTAL)
        self.code_display = wx.TextCtrl(
            panel,
            value="------",
            style=wx.TE_READONLY | wx.TE_CENTRE | wx.BORDER_SIMPLE,
        )
        self.code_display.SetFont(
            wx.Font(23, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        )
        self.code_display.SetMinSize((-1, 48))
        code_row.Add(self.code_display, 1, wx.EXPAND | wx.RIGHT, 8)

        self.copy_button = wx.Button(panel, label="Copy", size=(72, 48))
        self.copy_button.Disable()
        code_row.Add(self.copy_button, 0, wx.EXPAND)
        outer.Add(code_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        self.status = wx.StaticText(panel, label="Ready to generate a sign-in code.")
        self.status.SetForegroundColour(wx.Colour(70, 83, 89))
        outer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        warning = wx.StaticText(
            panel,
            label="6 digits is standard. Other lengths work only if configured for admin sign-in.",
        )
        warning.Wrap(400)
        warning.SetForegroundColour(wx.Colour(100, 112, 118))
        outer.Add(warning, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 10)

        panel.SetSizer(outer)
        self.generated_code = ""
        self.generate_button.Bind(wx.EVT_BUTTON, self.on_generate)
        self.copy_button.Bind(wx.EVT_BUTTON, self.on_copy)
        self.Centre()

    def copy_code(self, code: str) -> bool:
        if not wx.TheClipboard.Open():
            return False
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(code))
            wx.TheClipboard.Flush()
            return True
        finally:
            wx.TheClipboard.Close()

    def on_generate(self, _event):
        digits = self.digit_choice.GetSelection() + 6
        self.generate_button.Disable()
        self.status.SetLabel("Generating…")
        wx.BeginBusyCursor()
        try:
            code = generate_totp_code(digits)
            self.generated_code = code
            self.code_display.SetValue(code)
            self.copy_button.Enable()
            if self.copy_code(code):
                self.status.SetLabel(f"{digits}-digit code copied. It changes every 30 seconds.")
            else:
                self.status.SetLabel("Code generated, but clipboard is busy. Click Copy.")
        except Exception as exc:
            self.generated_code = ""
            self.code_display.SetValue("Generation failed")
            self.copy_button.Disable()
            wx.MessageBox(str(exc), "Could not generate code",
                          wx.OK | wx.ICON_ERROR, self)
            self.status.SetLabel("Check the project path, virtual environment, and .env file.")
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            self.generate_button.Enable()

    def on_copy(self, _event):
        if not self.generated_code:
            return
        if self.copy_code(self.generated_code):
            self.status.SetLabel("Code copied to clipboard. It changes every 30 seconds.")
        else:
            self.status.SetLabel("Clipboard is busy. Try again.")


def main():
    app = wx.App(False)
    frame = TOTPGeneratorFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
