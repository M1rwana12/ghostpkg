"""ghostpkg -- catch package names that do not exist before you install them."""

__version__ = "0.17.0"

from .assess import Finding, Verdict, assess
from .registries import PackageFacts, RegistryError, fetch

__all__ = [
    "Finding",
    "PackageFacts",
    "RegistryError",
    "Verdict",
    "assess",
    "fetch",
    "__version__",
]
