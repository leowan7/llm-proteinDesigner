"""Pipeline registry mapping tool names to their ToolPipeline implementations.

PIPELINE_MAP is the single lookup used by the worker to get the correct
config generator, result parser, and timeout policy for each design tool.
"""

from pipelines.base import ToolPipeline
from pipelines.bindcraft import BindCraftPipeline
from pipelines.boltzgen import BoltzGenPipeline
from pipelines.pxdesign import PXDesignPipeline
from pipelines.rfantibody import RFantibodyPipeline
from pipelines.rfdiffusion import RFdiffusionPipeline

# Map tool name strings (matching JobSpec.tool literals) to pipeline instances.
PIPELINE_MAP: dict[str, ToolPipeline] = {
    "rfdiffusion": RFdiffusionPipeline(),
    "rfantibody": RFantibodyPipeline(),
    "bindcraft": BindCraftPipeline(),
    "boltzgen": BoltzGenPipeline(),
    "pxdesign": PXDesignPipeline(),
}

__all__ = [
    "PIPELINE_MAP",
    "ToolPipeline",
    "RFdiffusionPipeline",
    "BindCraftPipeline",
    "RFantibodyPipeline",
    "BoltzGenPipeline",
    "PXDesignPipeline",
]
