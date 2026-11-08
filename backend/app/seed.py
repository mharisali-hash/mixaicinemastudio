import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import create_account
from app.config import get_settings
from app.models import User
from app.packages import ensure_defaults as ensure_packages
from app.rights import ensure_baseline as ensure_rights
from app.security import hash_password

log = logging.getLogger(__name__)


def seed(db: Session) -> None:
    from app.photo.looks import ensure_builtins

    ensure_builtins(db)
    ensure_packages(db)
    ensure_rights(db)
    db.commit()
    if db.scalar(select(func.count()).select_from(User)):
        return
    s = get_settings()
    # the first account is the admin; its studio keeps the old "Default" name so upgrades look the same
    user = create_account(db, s.admin_email, s.admin_name, verified=True, admin=True, workspace_name="Default")
    # set directly: the length rule is for people choosing one, and .env may still hold a short default
    user.password_hash = hash_password(s.admin_password)
    db.commit()
    log.info("seeded workspace and admin %s", user.email)
    if s.admin_password == "change-me":
        log.warning("ADMIN_PASSWORD is still the default; change it in .env")
