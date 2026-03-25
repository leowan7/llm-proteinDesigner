"""Abstract base class for tool-specific pipeline implementations.

Each design tool (RFdiffusion, BindCraft, etc.) has a concrete pipeline that
translates a generic JobSpec into tool-native configuration and parses tool
output back into standardized CandidateResult objects.
"""

from abc import ABC, abstractmethod

from jobs.models import CandidateResult


class ToolPipeline(ABC):
    """Abstract base defining the per-tool config generation and result parsing contract.

    Subclasses must implement:
    - generate_config: Translate JobSpec dict into tool-native configuration.
    - parse_results: Normalize RunPod handler output into CandidateResult list.
    - execution_timeout_ms: Per-tool RunPod execution timeout in milliseconds.
    """

    @abstractmethod
    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Translate a JobSpec dict into tool-native configuration.

        The returned dict is passed to the RunPod handler, which uses it to
        configure and run the tool inside the Docker container.

        Args:
            job_spec: Deserialized JobSpec dict from the database.
            target_local_path: Local filesystem path where the target PDB will
                be available inside the RunPod container.

        Returns:
            Tool-specific configuration dict (CLI args, JSON settings, YAML spec, etc.).
        """

    @abstractmethod
    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Normalize RunPod handler output into a list of CandidateResult objects.

        Args:
            output: The 'output' field from the RunPod webhook payload or
                status response. Structure is tool-dependent.

        Returns:
            List of CandidateResult objects sorted by rank (1-indexed).
        """

    @property
    @abstractmethod
    def execution_timeout_ms(self) -> int:
        """Per-tool RunPod execution timeout in milliseconds.

        RunPod terminates the job if it exceeds this duration. Each tool has
        different expected runtimes (e.g. RFdiffusion ~30 min, BindCraft ~4 hr).
        """

    @property
    def presigned_url_expiry_seconds(self) -> int:
        """Expiry for presigned S3 URLs passed to the RunPod container.

        Defaults to 1.5x the execution timeout (converted to seconds), with
        a minimum of 7200 seconds (2 hours). Override in subclass if the tool
        requires a longer window (e.g. BindCraft).

        Returns:
            Expiry duration in seconds.
        """
        computed = int(self.execution_timeout_ms * 1.5 / 1000)
        return max(7200, computed)
