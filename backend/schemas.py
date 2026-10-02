import re
from datetime import datetime
from pydantic import BaseModel, field_validator
from typing import Optional, List, Literal


def normalize_phone(raw: str) -> str:
    """Reformat any phone number input into (xxx)-xxx-xxxx.

    Accepts any input containing 10 digits (extra digits for a leading
    '1' country code are stripped). Raises ValueError if it can't find
    exactly 10 digits to work with, so obviously-wrong input is rejected
    rather than silently mangled.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"Phone number must contain 10 digits, got {len(digits)} from '{raw}'"
        )
    return f"({digits[0:3]})-{digits[3:6]}-{digits[6:10]}"


# Accepted input formats, tried in order. "%Y-%m-%d" covers the value sent
# by an HTML <input type="date">; the rest cover manual/typed entry and
# re-saving an already-normalized value.
_DATE_INPUT_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"]


def normalize_date(raw: str) -> str:
    """Reformat any recognized date input into mm/dd/yyyy."""
    raw = raw.strip()
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognized date '{raw}' — use mm/dd/yyyy (or yyyy-mm-dd)."
    )


_TIME_INPUT_FORMATS = ["%I:%M %p", "%H:%M", "%I:%M%p"]


def normalize_time(raw: str) -> str:
    """Reformat any recognized time input into h:mm AM/PM (e.g. '12:23 PM')."""
    raw = raw.strip().upper()
    for fmt in _TIME_INPUT_FORMATS:
        try:
            formatted = datetime.strptime(raw, fmt).strftime("%I:%M %p")
            return formatted.lstrip("0") if formatted[0] == "0" else formatted
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time '{raw}' — use h:mm AM/PM (or 24-hour HH:MM).")


def parse_flexible_date(raw: str) -> datetime:
    """Parses a date string in any of our accepted formats into a datetime,
    for range comparisons (e.g. in the history/activity view)."""
    raw = raw.strip()
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date '{raw}'")


LanguageCode = Literal["en", "es"]


# ---------- Entry schemas ----------

def _blank_to_zero(v):
    """Treats an empty/blank deposit or withdrawal as 0 rather than a
    validation error, so an empty field defaults to 0 the same way whether
    it's left blank in the UI or sent blank directly to the API."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return 0.0
    return v


class EntryBase(BaseModel):
    date: Optional[str] = None
    item_description: Optional[str] = None
    deposit: Optional[float] = 0.0
    withdrawal: Optional[float] = 0.0
    # total_balance is intentionally NOT accepted here — it's a computed,
    # server-maintained running balance. New entries get theirs calculated
    # automatically (previous balance + deposit - withdrawal); editing or
    # deleting an entry does NOT recalculate any balance. See
    # main.py:recalculate_balances() and add_entry().

    @field_validator("date")
    @classmethod
    def _format_date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_date(v)

    @field_validator("deposit", "withdrawal", mode="before")
    @classmethod
    def _default_blank_amounts(cls, v):
        return _blank_to_zero(v)


class EntryCreate(EntryBase):
    pass


class EntryUpdate(BaseModel):
    date: Optional[str] = None
    item_description: Optional[str] = None
    deposit: Optional[float] = None
    withdrawal: Optional[float] = None

    @field_validator("date")
    @classmethod
    def _format_date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_date(v)

    @field_validator("deposit", "withdrawal", mode="before")
    @classmethod
    def _default_blank_amounts(cls, v):
        # Here blank specifically means "clear it to 0", not "leave
        # unchanged" — a genuinely omitted field (not sent at all) is left
        # alone by exclude_unset in the endpoint, independent of this.
        if isinstance(v, str) and v.strip() == "":
            return 0.0
        return v


class EntryOut(BaseModel):
    id: int
    customer_id: int
    sort_order: int
    date: Optional[str] = None
    time: Optional[str] = None
    item_description: Optional[str] = None
    deposit: Optional[float] = 0.0
    withdrawal: Optional[float] = 0.0
    total_balance: Optional[float] = 0.0

    class Config:
        from_attributes = True


# ---------- Customer schemas ----------

class CustomerBase(BaseModel):
    name: str
    phone_number: str
    address: Optional[str] = None
    date_created: str
    time_created: Optional[str] = None  # auto-stamped at creation if not given — see main.py
    language: Optional[LanguageCode] = None  # per-customer override; None = use the app's global default

    @field_validator("phone_number")
    @classmethod
    def _format_phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("date_created")
    @classmethod
    def _format_date(cls, v: str) -> str:
        return normalize_date(v)

    @field_validator("time_created")
    @classmethod
    def _format_time(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_time(v)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    date_created: Optional[str] = None
    time_created: Optional[str] = None
    language: Optional[LanguageCode] = None

    @field_validator("phone_number")
    @classmethod
    def _format_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        return normalize_phone(v)

    @field_validator("date_created")
    @classmethod
    def _format_date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_date(v)

    @field_validator("time_created")
    @classmethod
    def _format_time(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_time(v)


class CustomerOut(CustomerBase):
    id: int

    class Config:
        from_attributes = True


class CustomerDetailOut(CustomerOut):
    entries: List[EntryOut] = []

    class Config:
        from_attributes = True