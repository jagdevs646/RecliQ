from typing import Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator
from typing_extensions import Annotated


Username = Annotated[str, StringConstraints(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
StrongPassword = Annotated[str, StringConstraints(min_length=12, max_length=256)]


class RegisterRequest(BaseModel):
    username: Username
    email: EmailStr
    password: StrongPassword
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]

    @field_validator("username", mode="before")
    @classmethod
    def username_is_not_an_email(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if "@" in value:
                raise ValueError("Enter only your username, not your full email address (for example, jagdevs646).")
        return value


class LoginRequest(BaseModel):
    identifier: Annotated[str, StringConstraints(min_length=3, max_length=254)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    totp_code: str | None = Field(default=None, min_length=6, max_length=8)


class TokenRequest(BaseModel):
    token: Annotated[str, StringConstraints(min_length=20, max_length=256)]


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(TokenRequest):
    new_password: StrongPassword


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    new_password: StrongPassword


class ChangeEmailRequest(BaseModel):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    new_email: EmailStr


class ProfileUpdateRequest(BaseModel):
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class ThemeUpdateRequest(BaseModel):
    theme: Literal["light", "dark", "system"]
    notifications: dict[str, bool] | None = None


class MfaCodeRequest(BaseModel):
    code: Annotated[str, StringConstraints(min_length=6, max_length=8)]


class MfaDisableRequest(BaseModel):
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    code: Annotated[str, StringConstraints(min_length=6, max_length=8)]


class ImportGuestRequest(BaseModel):
    confirm: bool


class DeleteAccountRequest(BaseModel):
    confirm: Literal["DELETE"]
    current_password: str | None = Field(default=None, max_length=256)
