"""Report generation for post-run design analysis.

Generates three export formats from a completed job's analysis data:
  - PDF  (fpdf2, Bindwave branding, text and tables only per D-16)
  - CSV  (pandas, all candidates with all score columns)
  - Markdown (mirrors PDF structure for documentation)

All formats include presigned PDB download links with 24-hour expiry (D-17).

Security (T-08-07, T-08-08):
  - pdb_key comes from the DB-backed candidate cache (never from raw user input)
  - Presigned URLs expire in 24 hours for PDB links in reports
  - Shortlist capped at 50 candidates (T-08-09)
"""

import json
import logging
from datetime import UTC, datetime

import pandas as pd
from config import settings
from fpdf import FPDF
from storage.client import generate_presigned_get_url, get_s3_client

from agent.analysis.cache import get_cached
from agent.analysis.ranking import compute_distribution_stats
from agent.analysis.tools import handle_flag_red_flags

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guidance text lookup for each tool (D-19: placeholder — Leo customizes)
# ---------------------------------------------------------------------------

_TOOL_GUIDANCE: dict[str, str] = {
    "bindcraft": (
        "Express top candidates in HEK293T or CHO cells via transient transfection. "
        "Purify by Ni-NTA affinity followed by size-exclusion chromatography (SEC). "
        "Validate binding affinity by SPR or BLI at 3 concentrations (1-point kinetics first, "
        "then full KD if binding is confirmed). "
        "Candidates with ipTM > 0.8 and ShapeComplementarity > 0.65 are priority hits for wet lab."
    ),
    "rfdiffusion": (
        "Express top scaffolds in E. coli BL21(DE3) as His-SUMO fusions. "
        "Assess solubility and thermostability by DSF before committing to large-scale expression. "
        "Validate structure by CD (secondary structure) and, if budget allows, low-resolution SAXS."
    ),
    "boltzgen": (
        "Validate designed binder structures by AF2-multimer refolding as an orthogonal check. "
        "Express in HEK293 or yeast display for functional screening. "
        "Prioritize candidates where Boltz2 complex pLDDT > 0.8 and interface pAE < 10."
    ),
    "rfantibody": (
        "Express VHH/nanobody candidates in E. coli or yeast display. "
        "Screen by yeast display FACS (2-round selection) before committing to E. coli expression. "
        "Validate by SPR for KD measurement."
    ),
    "pxdesign": (
        "Validate cyclic peptide candidates by AlphaFold2-multimer refolding. "
        "Synthesize top-ranked sequences by solid-phase peptide synthesis (SPPS). "
        "Test binding by fluorescence polarization (FP) or biolayer interferometry (BLI)."
    ),
}

_DEFAULT_GUIDANCE = (
    "Review the shortlisted candidates with your team and select top hits for wet lab validation. "
    "Consult your guidance profiles for tool-specific experimental protocols."
)


def _get_guidance(tool: str) -> str:
    """Return guidance text for a given tool type.

    Args:
        tool: Tool name (e.g. 'bindcraft', 'rfdiffusion').

    Returns:
        Guidance paragraph string.
    """
    return _TOOL_GUIDANCE.get(tool.lower(), _DEFAULT_GUIDANCE)


# ---------------------------------------------------------------------------
# Metric interpretation lines for PDF/Markdown
# ---------------------------------------------------------------------------

_METRIC_INTERPRETATION: dict[str, str] = {
    "ipTM": "Interface predicted TM-score (0-1). >0.7 indicates strong predicted binding.",
    "pLDDT": "Per-residue confidence (0-1). >0.8 indicates high backbone confidence.",
    "dG": "Binding free energy (Rosetta units). More negative = stronger predicted binding.",
    "dSASA": (
        "Buried solvent-accessible surface area on binding (Ų). "
        ">800 Ų indicates a substantial interface."
    ),
    "ShapeComplementarity": (
        "Geometric fit of the interface surfaces (0-1). "
        ">0.65 indicates good shape match."
    ),
    "Relaxed_Clashes": (
        "Residue clashes surviving Rosetta relaxation. "
        "0 is ideal; >2 suggests structural problems."
    ),
    "Surface_Hydrophobicity": (
        "Fraction of solvent-exposed hydrophobic surface. "
        "<0.4 minimises aggregation risk."
    ),
}


# ---------------------------------------------------------------------------
# KendrewReport: fpdf2 subclass
# ---------------------------------------------------------------------------


class KendrewReport(FPDF):
    """FPDF subclass with Bindwave-branded header and footer.

    Uses only built-in Helvetica/Arial fonts — no external font files required.
    Renders text and tables only (no images) per D-16.
    """

    def __init__(self, job_id: str, tool: str):
        """Initialise the report with job context for footer rendering.

        Args:
            job_id: UUID string of the analysis job.
            tool: Tool used for this design run (e.g. 'bindcraft').
        """
        super().__init__()
        self._job_id = job_id
        self._tool = tool
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(left=20, top=20, right=20)

    def header(self):
        """Render Bindwave page header with report title."""
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(30, 30, 30)
        self.cell(0, 10, "Bindwave Design Analysis Report", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, f"Job: {self._job_id}  |  Tool: {self._tool}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def footer(self):
        """Render page number and generation note."""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Generated by Bindwave  |  Page {self.page_no()}  |  Download links expire in 24 hours.", align="C")

    def section_title(self, title: str) -> None:
        """Render a section heading.

        Args:
            title: Section title text.
        """
        self.ln(4)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(20, 20, 80)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(20, 20, 80)
        self.set_line_width(0.3)
        self.line(self.get_x(), self.get_y(), self.get_x() + 170, self.get_y())
        self.ln(2)
        self.set_text_color(30, 30, 30)

    @staticmethod
    def _sanitize(text: str) -> str:
        """Replace non-latin-1 characters with ASCII equivalents for Helvetica.

        fpdf2 built-in fonts use latin-1 encoding. This replaces common Unicode
        typographic characters that appear in analysis text strings.

        Args:
            text: Input string (may contain non-latin-1 characters).

        Returns:
            String safe for use with built-in Helvetica font.
        """
        return (
            text
            .replace("\u2014", "--")   # em dash
            .replace("\u2013", "-")    # en dash
            .replace("\u2018", "'")    # left single quote
            .replace("\u2019", "'")    # right single quote
            .replace("\u201c", '"')    # left double quote
            .replace("\u201d", '"')    # right double quote
            .replace("\u2026", "...")  # ellipsis
            .encode("latin-1", errors="replace")
            .decode("latin-1")
        )

    def body_text(self, text: str) -> None:
        """Render a body paragraph.

        Args:
            text: Paragraph content (Unicode ok; non-latin-1 chars are sanitized).
        """
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 6, self._sanitize(text))
        self.ln(2)


# ---------------------------------------------------------------------------
# generate_pdf_report
# ---------------------------------------------------------------------------


def generate_pdf_report(
    job_id: str,
    tool: str,
    shortlist: list[dict],
    all_candidates: list[dict],
    red_flags: list[dict],
    stats: dict[str, dict],
    job_spec: dict,
    guidance_text: str,
) -> bytes:
    """Generate a Bindwave-branded PDF analysis report.

    Contains: title page metadata, original parameters, results summary,
    shortlisted candidates table, red flags, metric interpretation, next steps,
    and PDB download links (24-hour presigned URLs).

    Security:
      - Shortlist is capped at 50 candidates (T-08-09 DoS mitigation).
      - PDB keys come from DB-backed cache, not user input (T-08-08).

    Args:
        job_id: UUID of the analysis job.
        tool: Design tool used (e.g. 'bindcraft').
        shortlist: Ordered list of shortlisted candidate dicts (rank, pdb_key, scores).
        all_candidates: Full candidate list for summary statistics.
        red_flags: List of red-flag dicts from handle_flag_red_flags.
        stats: Distribution stats dict from compute_distribution_stats.
        job_spec: Original job parameters dict (tool, target_pdb_path, parameters, etc.).
        guidance_text: Experimental next-step guidance paragraph.

    Returns:
        PDF file as bytes.

    Raises:
        ValueError: If shortlist exceeds 50 candidates.
    """
    if len(shortlist) > 50:
        raise ValueError(
            f"Shortlist has {len(shortlist)} candidates. "
            "PDF reports are capped at 50 candidates to prevent DoS. "
            "Trim your shortlist and try again."
        )

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    pdf = KendrewReport(job_id=job_id, tool=tool)
    pdf.add_page()

    # --- Section: metadata ---
    pdf.section_title("Report Details")
    pdf.body_text(f"Date: {now}")
    pdf.body_text(f"Job ID: {job_id}")
    pdf.body_text(f"Tool: {tool}")

    # --- Section: original design parameters ---
    pdf.section_title("Original Design Parameters")
    params = job_spec.get("parameters", {})
    pdf.body_text(f"Target PDB: {job_spec.get('target_pdb_path', 'N/A')}")
    pdf.body_text(f"Target Chain: {job_spec.get('target_chain', 'N/A')}")
    hotspot = job_spec.get("hotspot_residues", [])
    pdf.body_text(f"Hotspot Residues: {', '.join(str(r) for r in hotspot) if hotspot else 'None specified'}")
    pdf.body_text(f"Number of Designs Requested: {params.get('num_designs', 'N/A')}")
    for k, v in params.items():
        if k != "num_designs":
            pdf.body_text(f"  {k}: {v}")

    # --- Section: results summary ---
    pdf.section_title("Results Summary")
    pdf.body_text(f"Total candidates returned: {len(all_candidates)}")
    pdf.body_text(f"Shortlisted for report: {len(shortlist)}")
    if stats:
        for metric, s in list(stats.items())[:4]:
            pdf.body_text(
                f"{metric}: mean {s['mean']:.3f}, "
                f"range [{s['min']:.3f}-{s['max']:.3f}], "
                f"p95 {s['p95']:.3f}"
            )

    # --- Section: shortlisted candidates table ---
    if shortlist:
        pdf.section_title("Shortlisted Candidates")
        # Pick top 5 score keys that exist across all shortlisted candidates
        all_score_keys = set()
        for c in shortlist:
            all_score_keys.update(c.get("scores", {}).keys())
        top_score_keys = sorted(all_score_keys)[:5]

        # Build table: Rank + top score columns
        headers = ["Rank"] + top_score_keys
        num_cols = len(headers)
        col_width = 170.0 / num_cols  # 170mm usable width

        with pdf.table(col_widths=tuple(int(col_width) for _ in headers)) as table:
            # Header row
            header_row = table.row()
            for h in headers:
                header_row.cell(h)
            # Data rows
            for candidate in shortlist:
                data_row = table.row()
                data_row.cell(str(candidate.get("rank", "")))
                for key in top_score_keys:
                    val = candidate.get("scores", {}).get(key, "")
                    if isinstance(val, float):
                        data_row.cell(f"{val:.3f}")
                    else:
                        data_row.cell(str(val))

    # --- Section: red flags ---
    pdf.section_title("Red Flags")
    if red_flags:
        for entry in red_flags:
            metrics_str = ", ".join(f"{k}: {v}" for k, v in entry.get("metrics", {}).items())
            pdf.body_text(f"Rank {entry.get('rank', '?')}: {entry.get('flag', '')} ({metrics_str})")
    else:
        pdf.body_text("No red flags detected.")

    # --- Section: metric interpretation ---
    pdf.section_title("Metric Interpretation")
    for metric, interp in _METRIC_INTERPRETATION.items():
        pdf.body_text(f"{metric}: {interp}")

    # --- Section: recommended next steps ---
    pdf.section_title("Recommended Next Steps")
    pdf.body_text(guidance_text)

    # --- Section: PDB download links ---
    pdf.section_title("PDB Download Links (24-hour expiry)")
    for candidate in shortlist:
        pdb_key = candidate.get("pdb_key", "")
        rank = candidate.get("rank", "?")
        if pdb_key:
            try:
                url = generate_presigned_get_url(pdb_key, expires_in=86400)
                pdf.body_text(f"Rank {rank}: {url}")
            except Exception as exc:
                logger.warning("Failed to generate presigned URL for %s: %s", pdb_key, exc)
                pdf.body_text(f"Rank {rank}: (URL unavailable — {exc})")
        else:
            pdf.body_text(f"Rank {rank}: (No PDB key available)")

    # fpdf2 output() returns bytearray; cast to bytes for consistency
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# generate_csv_export
# ---------------------------------------------------------------------------


def generate_csv_export(candidates: list[dict]) -> str:
    """Export all candidates as a CSV string.

    Columns: rank, pdb_key, then all score keys sorted alphabetically.
    Includes all candidates (not just the shortlist) for full data access.

    Args:
        candidates: Full list of candidate dicts with rank, pdb_key, scores.

    Returns:
        CSV string with header row and one data row per candidate.
    """
    if not candidates:
        return "rank,pdb_key\n"

    # Flatten each candidate to a flat dict
    rows = []
    for c in candidates:
        row = {
            "rank": c.get("rank", ""),
            "pdb_key": c.get("pdb_key", ""),
        }
        for score_key, score_val in sorted(c.get("scores", {}).items()):
            row[score_key] = score_val
        rows.append(row)

    df = pd.DataFrame(rows)

    # Ensure rank and pdb_key are always first
    other_cols = [col for col in sorted(df.columns) if col not in ("rank", "pdb_key")]
    ordered_cols = ["rank", "pdb_key"] + other_cols
    existing_cols = [col for col in ordered_cols if col in df.columns]
    df = df[existing_cols]

    return df.to_csv(index=False)


# ---------------------------------------------------------------------------
# generate_markdown_report
# ---------------------------------------------------------------------------


def generate_markdown_report(
    job_id: str,
    tool: str,
    shortlist: list[dict],
    all_candidates: list[dict],
    red_flags: list[dict],
    stats: dict[str, dict],
    job_spec: dict,
    guidance_text: str,
) -> str:
    """Generate a Markdown analysis report.

    Same content structure as the PDF but in Markdown format, suitable for
    documentation, Notion import, or plain-text archiving.

    Args:
        job_id: UUID of the analysis job.
        tool: Design tool used (e.g. 'bindcraft').
        shortlist: Shortlisted candidate dicts.
        all_candidates: Full candidate list for summary statistics.
        red_flags: Red-flag dicts from handle_flag_red_flags.
        stats: Distribution stats dict.
        job_spec: Original job parameters dict.
        guidance_text: Experimental next-step guidance paragraph.

    Returns:
        Markdown string.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append("# Bindwave Design Analysis Report")
    lines.append("")
    lines.append(f"**Date:** {now}  ")
    lines.append(f"**Job ID:** `{job_id}`  ")
    lines.append(f"**Tool:** {tool}  ")
    lines.append("")

    # --- Original design parameters ---
    lines.append("## Original Design Parameters")
    lines.append("")
    params = job_spec.get("parameters", {})
    lines.append(f"- **Target PDB:** {job_spec.get('target_pdb_path', 'N/A')}")
    lines.append(f"- **Target Chain:** {job_spec.get('target_chain', 'N/A')}")
    hotspot = job_spec.get("hotspot_residues", [])
    lines.append(f"- **Hotspot Residues:** {', '.join(str(r) for r in hotspot) if hotspot else 'None specified'}")
    lines.append(f"- **Number of Designs Requested:** {params.get('num_designs', 'N/A')}")
    for k, v in params.items():
        if k != "num_designs":
            lines.append(f"  - {k}: {v}")
    lines.append("")

    # --- Results summary ---
    lines.append("## Results Summary")
    lines.append("")
    lines.append(f"- **Total candidates returned:** {len(all_candidates)}")
    lines.append(f"- **Shortlisted for report:** {len(shortlist)}")
    if stats:
        lines.append("")
        lines.append("**Distribution statistics (key metrics):**")
        lines.append("")
        for metric, s in list(stats.items())[:4]:
            lines.append(
                f"- {metric}: mean {s['mean']:.3f}, "
                f"range [{s['min']:.3f}-{s['max']:.3f}], "
                f"p95 {s['p95']:.3f}"
            )
    lines.append("")

    # --- Shortlisted candidates table ---
    lines.append("## Shortlisted Candidates")
    lines.append("")
    if shortlist:
        all_score_keys = set()
        for c in shortlist:
            all_score_keys.update(c.get("scores", {}).keys())
        top_score_keys = sorted(all_score_keys)[:5]

        header_row = "| Rank | " + " | ".join(top_score_keys) + " |"
        sep_row = "|------|" + "".join("------|" for _ in top_score_keys)
        lines.append(header_row)
        lines.append(sep_row)

        for candidate in shortlist:
            cells = [str(candidate.get("rank", ""))]
            for key in top_score_keys:
                val = candidate.get("scores", {}).get(key, "")
                if isinstance(val, float):
                    cells.append(f"{val:.3f}")
                else:
                    cells.append(str(val))
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("No candidates in shortlist.")
    lines.append("")

    # --- Red flags ---
    lines.append("## Red Flags")
    lines.append("")
    if red_flags:
        for entry in red_flags:
            metrics_str = ", ".join(f"{k}: {v}" for k, v in entry.get("metrics", {}).items())
            lines.append(
                f"- **Rank {entry.get('rank', '?')}:** {entry.get('flag', '')} "
                f"({metrics_str})"
            )
    else:
        lines.append("No red flags detected.")
    lines.append("")

    # --- Metric interpretation ---
    lines.append("## Metric Interpretation")
    lines.append("")
    for metric, interp in _METRIC_INTERPRETATION.items():
        lines.append(f"- **{metric}:** {interp}")
    lines.append("")

    # --- Next steps ---
    lines.append("## Next Steps")
    lines.append("")
    lines.append(guidance_text)
    lines.append("")

    # --- PDB download links ---
    lines.append("## PDB Downloads")
    lines.append("")
    lines.append("*Download links expire in 24 hours.*")
    lines.append("")
    for candidate in shortlist:
        pdb_key = candidate.get("pdb_key", "")
        rank = candidate.get("rank", "?")
        if pdb_key:
            try:
                url = generate_presigned_get_url(pdb_key, expires_in=86400)
                lines.append(f"- [Rank {rank} PDB]({url})")
            except Exception as exc:
                logger.warning("Failed to generate presigned URL for %s: %s", pdb_key, exc)
                lines.append(f"- Rank {rank}: (URL unavailable)")
        else:
            lines.append(f"- Rank {rank}: (No PDB key available)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# handle_generate_report
# ---------------------------------------------------------------------------


async def handle_generate_report(tool_input: dict, user_id: str) -> str:
    """Generate and upload PDF, CSV, and Markdown reports for a completed job.

    Requires candidates to be loaded in cache via load_job_results first.
    Fetches job_spec from DB to include original design parameters in report.
    Uploads all three report files to MinIO under users/{user_id}/reports/{job_id}/.
    Returns presigned 1-hour download URLs for all three files.

    Security:
      - pdb_key values come from the ownership-checked cache (T-08-08)
      - Presigned URLs scoped to specific object keys (T-08-07)
      - Shortlist capped at 50 candidates (T-08-09)

    Args:
        tool_input: Must contain 'job_id'. Optional 'shortlist_ranks' (list[int]).
        user_id: Authenticated user ID for ownership check.

    Returns:
        JSON string with status, pdf_url, csv_url, markdown_url, shortlist_count,
        and a human-readable message.
    """
    job_id = tool_input.get("job_id")
    if not job_id:
        return json.dumps({"status": "error", "message": "job_id is required."})

    # Require candidates to be cached (load_job_results must be called first)
    candidates = get_cached(job_id)
    if candidates is None:
        return json.dumps({
            "status": "error",
            "message": (
                f"Job {job_id} is not loaded. "
                "Call load_job_results first, then call generate_report."
            ),
        })

    # Fetch job_spec from DB for original parameters (ownership already verified via cache)
    try:
        from db.connection import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT tool, job_spec FROM public.jobs WHERE id = $1 AND user_id = $2",
                job_id,
                user_id,
            )
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Failed to fetch job details: {exc}",
        })

    if job_row is None:
        return json.dumps({
            "status": "error",
            "message": f"Job {job_id} not found or you do not have access to it.",
        })

    tool = job_row["tool"]
    job_spec_raw = job_row["job_spec"]
    job_spec: dict = (
        json.loads(job_spec_raw) if isinstance(job_spec_raw, str) else dict(job_spec_raw)
    )

    # Determine shortlist
    shortlist_ranks: list[int] | None = tool_input.get("shortlist_ranks")
    if shortlist_ranks:
        shortlist = [c for c in candidates if c.get("rank") in shortlist_ranks]
    else:
        # Default: top 10 by rank (candidates already sorted by rank from DB)
        shortlist = candidates[:10]

    if len(shortlist) > 50:
        shortlist = shortlist[:50]
        logger.warning("Shortlist trimmed to 50 candidates for job %s (T-08-09)", job_id)

    # Gather analysis data
    red_flags_json = await handle_flag_red_flags({"job_id": job_id}, user_id=user_id)
    red_flags_data = json.loads(red_flags_json)
    red_flags = red_flags_data.get("red_flags", []) if red_flags_data.get("status") == "success" else []

    stats = compute_distribution_stats(candidates)
    guidance_text = _get_guidance(tool)

    # Generate all 3 formats
    try:
        pdf_bytes = generate_pdf_report(
            job_id=job_id,
            tool=tool,
            shortlist=shortlist,
            all_candidates=candidates,
            red_flags=red_flags,
            stats=stats,
            job_spec=job_spec,
            guidance_text=guidance_text,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"PDF generation failed: {exc}"})

    csv_str = generate_csv_export(candidates)
    md_str = generate_markdown_report(
        job_id=job_id,
        tool=tool,
        shortlist=shortlist,
        all_candidates=candidates,
        red_flags=red_flags,
        stats=stats,
        job_spec=job_spec,
        guidance_text=guidance_text,
    )

    # Upload to MinIO
    pdf_key = f"users/{user_id}/reports/{job_id}/report.pdf"
    csv_key = f"users/{user_id}/reports/{job_id}/report.csv"
    md_key = f"users/{user_id}/reports/{job_id}/report.md"

    try:
        s3 = get_s3_client()
        bucket = settings.s3_bucket_name

        s3.put_object(Bucket=bucket, Key=pdf_key, Body=pdf_bytes, ContentType="application/pdf")
        s3.put_object(Bucket=bucket, Key=csv_key, Body=csv_str.encode("utf-8"), ContentType="text/csv")
        s3.put_object(Bucket=bucket, Key=md_key, Body=md_str.encode("utf-8"), ContentType="text/markdown")
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Failed to upload report files: {exc}",
        })

    # Generate presigned 1-hour GET URLs for the report files themselves
    try:
        pdf_url = generate_presigned_get_url(pdf_key, expires_in=3600)
        csv_url = generate_presigned_get_url(csv_key, expires_in=3600)
        md_url = generate_presigned_get_url(md_key, expires_in=3600)
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Failed to generate download URLs: {exc}",
        })

    return json.dumps({
        "status": "success",
        "pdf_url": pdf_url,
        "csv_url": csv_url,
        "markdown_url": md_url,
        "shortlist_count": len(shortlist),
        "message": (
            f"Report generated with {len(shortlist)} shortlisted candidates. "
            "Download links expire in 1 hour."
        ),
    })
