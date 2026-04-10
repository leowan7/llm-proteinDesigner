import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { JobPage } from "./JobPage";

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
});
