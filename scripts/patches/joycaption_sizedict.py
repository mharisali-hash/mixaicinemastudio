"""ComfyUI-JoyCaption (a0e9f0a) on transformers 5: image_processor.size is a SizeDict, not a dict, so the node
fell through to (size, size) and PIL failed with "'SizeDict' object cannot be interpreted as an integer".

    python joycaption_sizedict.py /path/to/ComfyUI/custom_nodes/ComfyUI-JoyCaption/JC.py

Idempotent: a file that already has the fix is left alone.
"""
import sys
from pathlib import Path

OLD = "            if isinstance(expected_size, dict):\n"
NEW = ("            if getattr(expected_size, 'height', None) and getattr(expected_size, 'width', None):\n"
       "                self.target_size = (int(expected_size.height), int(expected_size.width))\n"
       "            elif isinstance(expected_size, dict):\n")

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")
if NEW in src:
    print(f"already patched: {path}")
elif OLD in src:
    path.write_text(src.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"patched: {path}")
else:
    sys.exit(f"the size check in {path} has changed; patch not applied")
