import tempfile
from pathlib import Path

from app.config import get_settings


def job_workdir(kind: str, job_id: str) -> Path:
    """A fresh scratch folder for one run of a job.

    Keyed by the job id alone, a job that a second worker picked up (after a missed heartbeat) shared the folder
    with the first run, and whichever started later wiped the other's files mid-way. Each run gets its own now.
    """
    base = get_settings().data_dir / "tmp" / kind
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{job_id}-", dir=base)).resolve()
