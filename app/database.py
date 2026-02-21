import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from urllib.parse import quote_plus

# Get DATABASE_URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

# Fallback for local development
if not DATABASE_URL:
    password = quote_plus("Huda@333")
    DATABASE_URL = f"mysql+pymysql://root:{password}@localhost/school_attendance"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()