"""FaceFusion's own face finder, in the facefusion venv: the faces on a few pictures, left to right.

Job: {repo, images: [paths]}. Done: {"faces": {path: [{box, score, gender, age}, ...]}} with each list in the
order headless-run's --reference-face-position counts them (--face-selector-order left-right), so a face's
index here is the position the swap asks for.
"""
import os
import sys

import runner_io as rio


def run(job: dict) -> dict:
    repo = job["repo"]
    if not os.path.isfile(os.path.join(repo, "facefusion.py")):
        raise RuntimeError(f"FaceFusion isn't checked out at {repo} (scripts/install-facefusion.sh)")
    # it finds its processors and models relative to the checkout
    os.chdir(repo)
    sys.path.insert(0, repo)
    import onnxruntime

    if hasattr(onnxruntime, "preload_dlls"):
        # CUDA / cuDNN from the pip wheels, when nobody set LD_LIBRARY_PATH for us
        onnxruntime.preload_dlls()
    from facefusion import face_classifier, face_detector, face_landmarker, face_recognizer, logger, state_manager
    from facefusion.args import apply_args
    from facefusion.execution import get_available_execution_providers
    from facefusion.face_creator import get_many_faces
    from facefusion.face_selector import sort_faces_by_order
    from facefusion.program import create_program
    from facefusion.vision import read_static_image

    # the same defaults a headless-run gets (detector, scores, angles), so the faces and their order match
    argv = ["headless-run", "--face-selector-order", "left-right"]
    if "cuda" in get_available_execution_providers():
        argv += ["--execution-providers", "cuda"]
    apply_args(vars(create_program().parse_args(argv)), state_manager.init_item)
    logger.init("error")
    rio.progress(0.1, "Loading the face models")
    for module in (face_detector, face_landmarker, face_recognizer, face_classifier):
        if not module.pre_check():
            raise RuntimeError(f"couldn't download {module.__name__}'s model")

    found = {}
    images = job.get("images") or []
    for i, path in enumerate(images):
        rio.progress(0.2 + 0.8 * i / max(1, len(images)), f"Looking at picture {i + 1}/{len(images)}")
        frame = read_static_image(path)
        if frame is None:
            raise RuntimeError(f"can't read {path}")
        faces = sort_faces_by_order(get_many_faces([frame]), "left-right")
        found[path] = [{"box": [round(float(v), 1) for v in f.bounding_box],
                        "score": round(float(f.score_set.get("detector") or 0), 3),
                        "gender": str(f.gender), "age": [f.age.start, f.age.stop] if isinstance(f.age, range) else None}
                       for f in faces]
    return {"faces": found}


if __name__ == "__main__":
    rio.main(run)
