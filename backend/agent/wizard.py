"""Tool-specific wizard parameter definitions with Bindwave-curated defaults.

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
            # 0.0 is RFdiffusion's own binder-design recipe, and it must
            # match build_hydra_args' default. This wizard emits every
            # param.default into job_spec["parameters"], so a default of
            # 1.0 here would EXPLICITLY re-request the full inference
            # noise the pipeline default exists to avoid -- leaving every
            # agent-driven run broken while direct API callers, which send
            # no noise_scale at all, got the fix.
            default=0.0,
            description=(
                "0 is RFdiffusion's recommended setting for binder design. "
                "Raise it toward 1.0 for more topological diversity at the "
                "cost of designability."
            ),
            # 0.0 must be REACHABLE: the old floor of 0.1 could not express
            # the recommended value even if a user asked for it.
            min_value=0.0,
            max_value=2.0,
        ),
    ],
    "bindcraft": [
        WizardParam(
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=10,
            description="10 designs provides a reasonable screening pool. BindCraft's ~46% hit rate means even small runs yield candidates.",
            min_value=1,
            max_value=500,
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
            default=100,
            description="100 designs for a pilot run. Production campaigns typically use 5,000-20,000.",
            min_value=1,
            max_value=20000,
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
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=100,
            description="100 designs for a pilot run. Production campaigns typically use 10,000-60,000.",
            min_value=10,
            max_value=60000,
        ),
        WizardParam(
            name="budget",
            label="Final candidate budget",
            param_type="int",
            default=50,
            description="Number of top candidates after quality-diversity filtering. 20-100 typical.",
            min_value=5,
            max_value=500,
        ),
        WizardParam(
            name="protocol",
            label="Design protocol",
            param_type="str",
            default="protein-anything",
            description="protein-anything (minibinder), nanobody-anything (VHH), peptide-anything (linear peptide; cyclisation is not available), protein-small_molecule, antibody-anything.",
        ),
    ],
    "pxdesign": [
        WizardParam(
            name="num_designs",
            label="Number of designs",
            param_type="int",
            default=100,
            description="100 designs for a pilot run. Production campaigns typically use 5,000-20,000.",
            min_value=10,
            max_value=20000,
        ),
        WizardParam(
            name="mode",
            label="Filter mode",
            param_type="str",
            default="basic",
            description="basic (AF2-IG only, faster) or extended (AF2-IG + Protenix, requires MSA, more discriminating).",
        ),
        WizardParam(
            name="generator",
            label="Design generator",
            param_type="str",
            default="diffusion",
            description="diffusion (PXDesign-d, higher throughput) or hallucination (PXDesign-h, more diverse topologies).",
        ),
    ],
}
