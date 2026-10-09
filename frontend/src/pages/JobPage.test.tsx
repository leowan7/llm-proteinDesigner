import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { JobPage } from "./JobPage";
import { getJob } from "@/lib/jobs";

// Mock the jobs API module — avoids real HTTP calls and SSE connections.
vi.mock("@/lib/jobs", () => ({
  getJob: vi.fn().mockResolvedValue({
    id: "test-job-123",
    status: "complete",
    tool: "rfdiffusion",
    stage: null,
    completed_at: "2026-04-10T12:00:00Z",
    gpu_seconds: 1800,
    gpu_cost_usd: 0.45,
    error_category: null,
    job_spec: {},
    candidates: [],
    results: { candidate_count: 0, next_steps: null, zero_output: false },
  }),
  getJobList: vi.fn().mockResolvedValue([]),
  downloadAllDesignsUrl: vi.fn((id: string) => `/jobs/${id}/download`),
  subscribeToJobStatus: vi.fn().mockReturnValue(() => {}),
  cancelJob: vi.fn().mockResolvedValue(undefined),
}));

describe("JobPage (smoke test)", () => {
  function renderJobPage() {
    return render(
      <MemoryRouter initialEntries={["/jobs/test-job-123"]}>
        <Routes>
          <Route path="/jobs/:id" element={<JobPage />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("renders without crashing", () => {
    const { container } = renderJobPage();
    expect(container).toBeTruthy();
  });

  it("renders the loading state initially (before data resolves)", () => {
    const { container } = renderJobPage();
    // Component renders "Loading job..." before the promise resolves
    expect(container.textContent).toContain("Loading job");
  });

  it("shows a failed run's saved designs, labelled partial, with no Export Report", async () => {
    // A timed-out run that streamed two designs before it died: the API
    // lists them from Storage and sets `partial`, and they have no scores
    // because the run never reached the step that computes them.
    vi.mocked(getJob).mockResolvedValueOnce({
      id: "test-job-123",
      status: "failed",
      tool: "rfdiffusion",
      stage: "design",
      completed_at: null,
      started_at: null,
      gpu_seconds: 1200,
      gpu_cost_usd: null,
      error_category: "Timed out",
      job_spec: {},
      results: null,
      created_at: null,
      partial: true,
      candidates: [
        { rank: 1, pdb_key: "design_001.pdb", scores: {}, download_url: "https://signed/1" },
        { rank: 2, pdb_key: "design_002.pdb", scores: {}, download_url: "https://signed/2" },
      ],
    });

    const { container } = renderJobPage();

    await waitFor(() =>
      expect(container.textContent).toContain("Designs saved before the run stopped"),
    );
    expect(container.textContent).toContain("This is a partial set");
    expect(container.textContent).toContain("Download all designs");
    // No metrics exist for these, so offering a report over them would be
    // offering a report over nothing
    expect(container.textContent).not.toContain("Export Report");
  });

  it("offers a cancelled run's designs through one download button, not two", async () => {
    // RunSummaryCard renders its own "Download all designs" whenever the run
    // is complete or cancelled, so the partial-set button in the candidate
    // header would be a second copy of it on a cancelled run.
    vi.mocked(getJob).mockResolvedValueOnce({
      id: "test-job-123",
      status: "cancelled",
      tool: "rfdiffusion",
      stage: "design",
      completed_at: null,
      started_at: null,
      gpu_seconds: 600,
      gpu_cost_usd: 0.15,
      error_category: null,
      job_spec: {},
      results: null,
      created_at: null,
      partial: true,
      candidates: [
        { rank: 1, pdb_key: "design_001.pdb", scores: {}, download_url: "https://signed/1" },
      ],
    });

    const { findAllByRole } = renderJobPage();

    const buttons = await findAllByRole("button", { name: "Download all designs" });
    expect(buttons).toHaveLength(1);
  });
});
