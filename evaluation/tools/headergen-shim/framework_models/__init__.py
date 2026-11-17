"""framework_models of HeaderGen 1ea5226, replaced for the evaluation.

The analysis of HeaderGen uses one function of this package, check_alias
(pycg_extended/machinery/typestubs.py:20). The original package also loads
an ML classifier at import (scikit-learn, scipy, numpy, nltk, eight
pickles, an nltk download); this shim does not. ML_MODULES_ALIAS and
check_alias are those of framework_models/__init__.py:118-140 and 183-190
at the pin, over the same pinned aliases/*.json, copied into the image.
"""

import json
from pathlib import Path

ALIASES = Path(__file__).resolve().parent / "aliases"
MODULES = ("keras", "matplotlib", "numpy", "pandas", "tensorflow", "sklearn", "nibabel")
ML_MODULES_ALIAS = {
    module: json.loads((ALIASES / f"{module}.json").read_text(encoding="utf-8"))
    for module in MODULES
}


def check_alias(func_call):
    root_module = func_call.split(".")[0]
    if root_module in ML_MODULES_ALIAS:
        if func_call in ML_MODULES_ALIAS[root_module]:
            func_call = ML_MODULES_ALIAS[root_module][func_call]
    return func_call
