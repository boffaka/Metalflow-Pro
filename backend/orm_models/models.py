from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Boolean, JSON, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import config as _cfg
    _DEFAULT_GOLD_PRICE = _cfg.DEFAULT_GOLD_PRICE_USD_OZ
except Exception:
    _DEFAULT_GOLD_PRICE = 2340

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String)
    token_version = Column(Integer, nullable=False, default=0)
    role = Column(String, default="Read-only")
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    projects = relationship("Project", back_populates="owner")

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_name = Column(String, nullable=False)
    project_code = Column(String, unique=True, nullable=False)
    target_tph = Column(Numeric)
    gold_grade_g_t = Column(Numeric)
    status = Column(String, default="SCOPING")
    capex_musd = Column(Numeric)
    project_owner = Column(String)
    commodity = Column(String, default="Au")
    location = Column(String)
    capacity_mtpa = Column(Numeric)
    process_options = Column(String)

    # Economic parameters
    gold_price_usd_oz = Column(Numeric, default=_DEFAULT_GOLD_PRICE)
    discount_rate_pct = Column(Numeric, default=5)
    mine_life_years = Column(Integer, default=10)
    operating_hours_day = Column(Numeric, default=24)
    availability_pct = Column(Numeric, default=92)
    electricity_rate = Column(Numeric, default=0.075)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"), onupdate=datetime.now)

    owner = relationship("User", back_populates="projects")
    equipment = relationship("Equipment", back_populates="project")

class Equipment(Base):
    __tablename__ = "equipment_v2"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    circuit_section = Column(String, nullable=False)
    tag_id = Column(String)
    category = Column(String)
    short_desc = Column(String)
    specs = Column(JSON)
    motor_kw = Column(Numeric)
    price_cad = Column(Numeric)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"), onupdate=datetime.now)

    project = relationship("Project", back_populates="equipment")

class LimsSample(Base):
    __tablename__ = "lims_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sample_id_display = Column(String, nullable=False)
    phase = Column(String)
    sample_type = Column(String)
    lithology = Column(String)
    provenance = Column(String)
    mass_kg = Column(Numeric)
    representativity = Column(String)
    waste_rock_dilution_pct = Column(Numeric)

    # Extra columns that might exist in schema but are optional/added via migrations
    source_horizon = Column(String)
    depth_interval = Column(String)
    total_mass_kg = Column(Numeric)
    sent_mass_kg = Column(Numeric)
    collection_date = Column(DateTime)
    reception_date = Column(DateTime)
    collection_method = Column(String)
    qaqc_protocol = Column(String)
    crm_standard = Column(String)
    duplicate_freq = Column(String)
    blank_freq = Column(String)
    packaging = Column(String)
    oxidation_state = Column(String)
    domain = Column(String)
    status = Column(String)
    observations = Column(String)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))

    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    project = relationship("Project")
    a1_tests = relationship("LimsA1", back_populates="sample", cascade="all, delete-orphan")

class LimsA1(Base):
    __tablename__ = "lims_a1"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sample_id = Column(UUID(as_uuid=True), ForeignKey("lims_samples.id", ondelete="CASCADE"), nullable=False)
    au_g_t = Column(Numeric)
    ag_g_t = Column(Numeric)
    cu_pct = Column(Numeric)
    fe_pct = Column(Numeric)
    s_total_pct = Column(Numeric)
    s_sulfide_pct = Column(Numeric)
    as_ppm = Column(Numeric)
    c_organic_pct = Column(Numeric)
    sb_ppm = Column(Numeric)

    # Extra fields
    hg_ppm = Column(Numeric)
    sio2_pct = Column(Numeric)
    al2o3_pct = Column(Numeric)
    cao_pct = Column(Numeric)
    mgo_pct = Column(Numeric)
    na2o_pct = Column(Numeric)
    k2o_pct = Column(Numeric)
    tio2_pct = Column(Numeric)
    mno_pct = Column(Numeric)
    loi_pct = Column(Numeric)

    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    project = relationship("Project")
    sample = relationship("LimsSample", back_populates="a1_tests")

class Flowsheet(Base):
    __tablename__ = "flowsheets"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    blocks = Column(JSON, default=list)
    connections = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    project = relationship("Project")

class LimsB1(Base):
    __tablename__ = "lims_b1"
    id = Column(UUID(as_uuid=True), primary_key=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))

class LimsC2(Base):
    __tablename__ = "lims_c2"
    id = Column(UUID(as_uuid=True), primary_key=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))

class LimsD1(Base):
    __tablename__ = "lims_d1"
    id = Column(UUID(as_uuid=True), primary_key=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
