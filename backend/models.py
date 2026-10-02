from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    phone_number = Column(String, nullable=False, index=True)
    address = Column(String, nullable=True)
    date_created = Column(String, nullable=False)  # stored as text (e.g. "09/16/2026") to keep manual entry simple
    time_created = Column(String, nullable=True)   # e.g. "12:23 PM" — auto-stamped at creation if not given
    language = Column(String, nullable=True)        # "en" / "es" override for this customer's card; None = use app default

    entries = relationship(
        "Entry", back_populates="customer", cascade="all, delete-orphan",
        order_by="Entry.sort_order"
    )


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)

    date = Column(String, nullable=True)
    time = Column(String, nullable=True)  # e.g. "12:23 PM" — auto-stamped when the item is added, not user-editable
    item_description = Column(String, nullable=True)
    deposit = Column(Float, nullable=True, default=0.0)
    withdrawal = Column(Float, nullable=True, default=0.0)
    total_balance = Column(Float, nullable=True, default=0.0)  # server-computed running balance — not client-settable

    # Preserves the order entries were added in (used for pagination into "pages" of 30-40)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer", back_populates="entries")