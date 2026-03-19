"""Tool-specific wizard parameter definitions with Ranomics-curated defaults.

Each tool has 3-5 essential parameters. Advanced settings are deferred to v2.
These definitions drive the wizard UI and validate user-supplied parameters
against allowed ranges before JobSpec creation.
"""

from typing import Literal

from pydantic import BaseModel


class WizardParam(BaseModel):
    """A single wizard parameter definition."""

    name: str                            # Machine name, e.g. "num_designs"
    label: str                           # Human label, e.g. "Number of designs"
    param_type: Literal["int", "float", "str", "bool"]
    default: int | float | str | bool
    description: str                     # One sentence explaining why this default
    min_value: float | None = None       # For numeric types
    max_value: float | None = None       # For numeric types


WIZARD_PARAMS: dict[str, list[WizardParam]] = {
    "rfdiffusion": [
        WizardParam(
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=10,
            description="10 designs balances diversity with GPU cost (~15 min total).",
            min_value=1,
            max_value=100,
        ),
        WizardParam(
            name="binder_length",
            label="Binder length (residues)",
            param_type="int",
            default=80,
            description="80 residues is a standard starting length for de novo binders.",
            min_value=30,
            max_value=200,
        ),
        WizardParam(
            name="noise_scale",
            label="Noise scale",
            param_type="float",
            default=1.0,
            description="1.0 is the default diffusion noise; lower values produce more conservative designs.",
            min_value=0.1,
            max_value=2.0,
        ),
    ],
    "bindcraft": [
        WizardParam(
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=10,
            description="10 designs provides a reasonable screening pool.",
            min_value=1,
            max_value=50,
        ),
        WizardParam(
            name="design_cycles",
            label="Design cycles per candidate",
            param_type="int",
            default=4,
            description="4 cycles balances optimization depth with runtime (~20 min/design).",
            min_value=1,
            max_value=10,
        ),
        WizardParam(
            name="mpnn_sampling_temp",
            label="MPNN sampling temperature",
            param_type="float",
            default=0.1,
            description="0.1 produces high-confidence sequences; increase for more diversity.",
            min_value=0.01,
            max_value=1.0,
        ),
        WizardParam(
            name="filter_score_threshold",
            label="AlphaFold confidence threshold (pLDDT)",
            param_type="float",
            default=80.0,
            description="Designs below 80 pLDDT are unlikely to fold correctly; lower to retain more candidates.",
            min_value=50.0,
            max_value=95.0,
        ),
    ],
    "rfantibody": [
        WizardParam(
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=10,
            description="10 designs balances diversity with GPU cost for CDR loop generation.",
            min_value=1,
            max_value=100,
        ),
        WizardParam(
            name="antibody_type",
            label="Antibody type",
            param_type="str",
            default="vhh",
            description="vhh for single-domain nanobodies; vh_vl for conventional antibody variable domains.",
        ),
        WizardParam(
            name="cdr_loops",
            label="CDR loops to design",
            param_type="str",
            default="H1,H2,H3",
            description="Comma-separated CDR loops to redesign. H3 is the primary binding determinant.",
        ),
    ],
    "boltzgen": [
        WizardParam(
            name="num_samples",
            label="Number of conformational samples",
            param_type="int",
            default=100,
            description="100 samples captures the dominant conformational states.",
            min_value=10,
            max_value=1000,
        ),
        WizardParam(
            name="temperature",
            label="Sampling temperature",
            param_type="float",
            default=1.0,
            description="1.0 samples the Boltzmann distribution; higher explores rare states.",
            min_value=0.1,
            max_value=5.0,
        ),
        WizardParam(
            name="num_steps",
            label="Integration steps",
            param_type="int",
            default=100,
            description="100 steps provides good accuracy for the ODE solver.",
            min_value=10,
            max_value=500,
        ),
    ],
}
