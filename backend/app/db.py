from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        # a SQLite connection is just an open file, so the pool can match the API's 40 request threads; at the
        # default 5 + 10, a Library page of video thumbs left nothing for anyone else (QueuePool timeouts)
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30},
                            pool_size=20, max_overflow=30)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            # WAL lets the API read while the worker writes progress updates
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # the same 30 s as `timeout` above (this pragma overrides it): at 5 s, two ad campaigns plus three
            # video upscales writing progress at once failed with "database is locked" (22 Oct)
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def release(db) -> None:
    # FastAPI closes a yield dependency only after the whole response has been sent, so a file streaming
    # to a slow client would pin a pooled connection for minutes; a few of those starve every other
    # request (QueuePool timeout, 500 after 30 s). File routes call this once they've done their checks.
    db.close()
