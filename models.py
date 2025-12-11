"""SQLAlchemy models for the lab kit tracking system.

This file complements the existing psycopg2-based repository by defining ORM
classes for future use. Because the schema now includes additional tables
and columns (e.g., SiteContact, address fields, default_expiry_days), run
a fresh init (drop/recreate) in development to align the database with these
models before relying on them.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Float,
    create_engine,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

# Connection for ORM usage (reuses the same credentials already used by psycopg2)
DATABASE_URL = (
    "postgresql+psycopg2://labkit_app:labkitdb123@192.168.99.41/labkit_db"
)

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Site(Base):
    __tablename__ = "site"

    id = Column(Integer, primary_key=True)
    site_code = Column(String, unique=True, nullable=False)
    site_name = Column(String, nullable=False)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    postal_code = Column(String)
    country = Column(String)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    contacts = relationship("SiteContact", back_populates="site", cascade="all, delete-orphan")
    labkits = relationship("Labkit", back_populates="site")


class SiteContact(Base):
    __tablename__ = "site_contact"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    name = Column(String, nullable=False)
    role = Column(String)
    email = Column(String)
    phone = Column(String)
    is_primary = Column(Boolean, default=False, nullable=False)

    site = relationship("Site", back_populates="contacts")


class LabkitType(Base):
    __tablename__ = "labkit_type"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    prefix = Column(String)
    description = Column(String)
    default_expiry_days = Column(Integer)
    standard_weight = Column(Float)
    weight_variance = Column(Float)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    labkits = relationship("Labkit", back_populates="labkit_type")


class Labkit(Base):
    __tablename__ = "labkit"

    id = Column(Integer, primary_key=True)
    kit_barcode = Column(String, unique=True, nullable=False)
    labkit_type_id = Column(Integer, ForeignKey("labkit_type.id"), nullable=False)
    site_id = Column(Integer, ForeignKey("site.id"), nullable=True)  # nullable for depot
    barcode_value = Column(String, unique=True)
    lot_number = Column(String)
    measured_weight = Column(Float)
    expiry_date = Column(Date)
    status = Column(
        String,
        nullable=False,
        default="planned",
        doc="planned|packed|ready_to_ship|shipped|at_site|used|returned|destroyed",
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    labkit_type = relationship("LabkitType", back_populates="labkits")
    site = relationship("Site", back_populates="labkits")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user = Column(String, nullable=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    field_name = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)


def init_orm_schema() -> None:
    """Create tables based on these ORM models."""
    Base.metadata.create_all(bind=engine)
