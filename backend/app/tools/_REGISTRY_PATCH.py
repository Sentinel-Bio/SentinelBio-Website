# PATCH for backend/app/tools/registry.py
#
# Add these four lines at the end of the file, alongside the existing
# `from app.tools import ... # noqa: F401` block.
#
# Order matches the file naming above; pick wherever you prefer in the list.

from app.tools import mutation_map as _mutation_map  # noqa: F401
from app.tools import struct_align as _struct_align  # noqa: F401
from app.tools import domain_diversity as _domain_diversity  # noqa: F401
from app.tools import cancer_hotspots as _cancer_hotspots  # noqa: F401
