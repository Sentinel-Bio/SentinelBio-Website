"""Registry of available bioinformatics tools.

Each tool has:
  - id: short string used in tool_runs.tool
  - label: human-friendly name
  - description: shown in the picker
  - inputs: shape of expected inputs (informational, frontend reads this)
  - params: parameter definitions for the picker UI
  - run: the async function to execute
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable
from dataclasses import dataclass, field

@dataclass
class ParamDef:
    name: str
    type: str   # 'int' | 'float' | 'string' | 'enum' | 'bool' | 'text_long'
                # | 'project_file' | 'project_file_fasta' | 'project_files_multi'
    label: str
    default: Any = None
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None  # for enum
    help: str = ""
    # For project_files_multi / project_file param types:
    kind: str | None = None       # comma-separated mime_hints (e.g. 'pdb,cif')
    category: str | None = None   # category filter


@dataclass
class ToolDef:
    id: str
    label: str
    description: str
    input_kind: str  # 'fastq_upload' | 'fasta_upload' | 'aligned_fasta' | 'multi_fasta' | 'none'
    inputs: dict[str, Any] = field(default_factory=dict)  # extra info, deprecated mostly
    params: list[ParamDef] = field(default_factory=list)
    run: Callable[..., Awaitable[dict[str, Any]]] = None  # type: ignore


_REGISTRY: dict[str, ToolDef] = {}


def register(tool: ToolDef) -> None:
    _REGISTRY[tool.id] = tool


def get_tool(tool_id: str) -> ToolDef | None:
    return _REGISTRY.get(tool_id)


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "label": t.label,
            "description": t.description,
            "input_kind": t.input_kind,
            "inputs": t.inputs,
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "label": p.label,
                    "default": p.default,
                    "min": p.min,
                    "max": p.max,
                    "options": p.options,
                    "help": p.help,
                    "kind": p.kind,
                    "category": p.category,
                }
                for p in t.params
            ],
        }
        for t in _REGISTRY.values()
    ]


# Auto-register tools.
from app.tools import fastqc as _fastqc  # noqa: F401
from app.tools import cutadapt as _cutadapt  # noqa: F401
from app.tools import mafft as _mafft  # noqa: F401
from app.tools import iqtree as _iqtree  # noqa: F401
from app.tools import popstats as _popstats  # noqa: F401
from app.tools import region_extract as _region_extract  # noqa: F401
from app.tools import blast as _blast  # noqa: F401
from app.tools import ncbi_fetch as _ncbi_fetch  # noqa: F401
from app.tools import alphafold_fetch as _alphafold_fetch  # noqa: F401
from app.tools import annotate_gene as _annotate_gene  # noqa: F401
from app.tools import colabfold_run as _colabfold_run  # noqa: F401
from app.tools import hyphy_selection as _hyphy_selection  # noqa: F401
from app.tools import mhcxgraph as _mhcxgraph  # noqa: F401
