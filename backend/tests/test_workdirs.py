from app.workdirs import job_workdir


def test_two_runs_of_one_job_never_share_a_folder():
    # the second worker used to wipe the first one's song.wav (poetry timing, 4 and 7 Nov)
    a, b = job_workdir("poetry", "job-1"), job_workdir("poetry", "job-1")
    (a / "song.wav").write_bytes(b"x")
    assert a != b and a.parent == b.parent and (a / "song.wav").is_file()
    assert a.name.startswith("job-1-")
