import math
import re
from datetime import datetime, time as dtime
from pydantic import BaseModel, field_validator
from typing import Optional, List, Literal


def normalize_phone(raw: str) -> str:
    """Reformat any phone number input into (xxx)-xxx-xxxx.

    Accepts any input containing 10 digits (extra digits for a leading
    '1' country code are stripped). Raises ValueError if it can't find
    exactly 10 digits to work with, so obviously-wrong input is rejected
    rather than silently mangled.
    """
    digits = re.sub(r"\D", "", raw or "")
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
_DATE_INPUT_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%y", "%m-%d-%y"]
MIN_YEAR, MAX_YEAR = 1900, 2100


def _parse_date(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fmt in _DATE_INPUT_FORMATS:
        try:
            d = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        # "10/30/26" parses as the year 26: treat a typed 2-digit year as 20xx.
        if d.year < 100 and not re.search(r"\d{3,}", raw):
            d = d.replace(year=2000 + d.year)
        if not (MIN_YEAR <= d.year <= MAX_YEAR):
            raise ValueError(f"Date '{raw}' is out of range (years {MIN_YEAR}-{MAX_YEAR}).")
        return d
    raise ValueError(f"Unrecognized date '{raw}' — use mm/dd/yyyy (or yyyy-mm-dd).")


def normalize_date(raw: str) -> str:
    """Reformat any recognized date input into mm/dd/yyyy."""
    return _parse_date(raw).strftime("%m/%d/%Y")


def parse_flexible_date(raw: str) -> datetime:
    """Parses a date string in any of our accepted formats into a datetime,
    for range comparisons (e.g. in the history/activity view)."""
    return _parse_date(raw)


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


def parse_time(raw: str) -> dtime:
    """Parses a time string ('12:23 PM', '14:30', ...) into a time object,
    for time-of-day range filtering in the history view."""
    raw = raw.strip().upper()
    for fmt in _TIME_INPUT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time '{raw}'")


LanguageCode = Literal["en", "es"]

# ---------- Shared field checks ----------

MAX_AMOUNT = 10_000_000  # far above anything real; stops typos like 1e308
MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 300


def _blank_to_zero(v):
    """Treats an empty/blank amount as 0 rather than a validation error, so
    an empty field defaults to 0 the same way whether it's left blank in the
    UI or sent blank directly to the API."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return 0.0
    return v


def _check_amount(v: float, allow_negative: bool = False) -> float:
    """Rejects NaN/Infinity/absurd values (which would break every page that
    shows them) and rounds to cents."""
    if not math.isfinite(v):
        raise ValueError("Amount must be a normal number.")
    if v < 0 and not allow_negative:
        raise ValueError("Amount can't be negative. Use the other column instead.")
    if abs(v) > MAX_AMOUNT:
        raise ValueError(f"Amount is too large (limit {MAX_AMOUNT:,}).")
    return round(v, 2)


def _required_text(v, label: str, max_len: int = MAX_NAME_LENGTH) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{label} can't be empty.")
    if len(v) > max_len:
        raise ValueError(f"{label} is too long (limit {max_len} characters).")
    return v


def _optional_text(v, label: str, max_len: int = MAX_TEXT_LENGTH) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    if len(v) > max_len:
        raise ValueError(f"{label} is too long (limit {max_len} characters).")
    return v or None


# ---------- Entry schemas ----------

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

    @field_validator("item_description")
    @classmethod
    def _clean_description(cls, v: Optional[str]) -> Optional[str]:
        return _optional_text(v, "Item description")

    @field_validator("deposit", "withdrawal", mode="before")
    @classmethod
    def _default_blank_amounts(cls, v):
        return _blank_to_zero(v)

    @field_validator("deposit", "withdrawal")
    @classmethod
    def _valid_amounts(cls, v: float) -> float:
        return _check_amount(v)


class EntryCreate(EntryBase):
    # A new item must have a date, or it would never show up in History.
    date: str

    @field_validator("date")
    @classmethod
    def _require_date(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Date is required.")
        return v


class EntryUpdate(BaseModel):
    date: Optional[str] = None
    item_description: Optional[str] = None
    deposit: Optional[float] = None
    withdrawal: Optional[float] = None

    @field_validator("date")
    @classmethod
    def _format_date(cls, v: Optional[str]) -> str:
        # Only runs when a date was sent; clearing it isn't allowed.
        if not v or not v.strip():
            raise ValueError("Date can't be empty.")
        return normalize_date(v)

    @field_validator("item_description")
    @classmethod
    def _clean_description(cls, v: Optional[str]) -> Optional[str]:
        return _optional_text(v, "Item description")

    @field_validator("deposit", "withdrawal", mode="before")
    @classmethod
    def _default_blank_amounts(cls, v):
        # Here blank (or null) specifically means "clear it to 0", not "leave
        # unchanged" — a genuinely omitted field (not sent at all) is left
        # alone by exclude_unset in the endpoint, independent of this.
        return _blank_to_zero(v)

    @field_validator("deposit", "withdrawal")
    @classmethod
    def _valid_amounts(cls, v: float) -> float:
        return _check_amount(v)


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


class DateFixRequest(BaseModel):
    entry_ids: List[int] = []
    customer_ids: List[int] = []


# ---------- Customer schemas ----------

class CustomerBase(BaseModel):
    name: str
    phone_number: str
    address: Optional[str] = None
    date_created: str
    time_created: Optional[str] = None  # auto-stamped at creation if not given — see main.py
    language: Optional[LanguageCode] = None  # per-customer override; None = use the app's global default
    opening_balance: Optional[float] = 0.0  # balance carried over from the old system

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return _required_text(v, "Name")

    @field_validator("address")
    @classmethod
    def _clean_address(cls, v: Optional[str]) -> Optional[str]:
        return _optional_text(v, "Address")

    @field_validator("opening_balance", mode="before")
    @classmethod
    def _default_blank_opening(cls, v):
        return _blank_to_zero(v)

    @field_validator("opening_balance")
    @classmethod
    def _valid_opening(cls, v: float) -> float:
        return _check_amount(v, allow_negative=True)  # a customer may owe money

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
    """Every field is optional (only what's sent changes), but a field that
    is sent can't be blanked out if the card needs it."""
    name: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    date_created: Optional[str] = None
    time_created: Optional[str] = None
    language: Optional[LanguageCode] = None
    opening_balance: Optional[float] = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: Optional[str]) -> str:
        return _required_text(v, "Name")

    @field_validator("address")
    @classmethod
    def _clean_address(cls, v: Optional[str]) -> Optional[str]:
        return _optional_text(v, "Address")

    @field_validator("opening_balance", mode="before")
    @classmethod
    def _default_blank_opening(cls, v):
        return _blank_to_zero(v)

    @field_validator("opening_balance")
    @classmethod
    def _valid_opening(cls, v: float) -> float:
        return _check_amount(v, allow_negative=True)

    @field_validator("phone_number")
    @classmethod
    def _format_phone(cls, v: Optional[str]) -> str:
        return normalize_phone(v)  # raises on blank/None

    @field_validator("date_created")
    @classmethod
    def _format_date(cls, v: Optional[str]) -> str:
        return normalize_date(v)  # raises on blank/None

    @field_validator("time_created")
    @classmethod
    def _format_time(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return normalize_time(v)


class CustomerOut(BaseModel):
    """What the API sends back. Deliberately has no validators: data already
    in the database is shown as-is, so one odd old value can never stop the
    whole customer list from loading."""
    id: int
    name: str
    phone_number: str
    address: Optional[str] = None
    date_created: str
    time_created: Optional[str] = None
    language: Optional[str] = None
    opening_balance: Optional[float] = 0.0

    class Config:
        from_attributes = True


class CustomerDetailOut(CustomerOut):
    entries: List[EntryOut] = []

    class Config:
        from_attributes = True
