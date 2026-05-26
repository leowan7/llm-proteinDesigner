/**
 * ChatPage — full-width chat interface for the protein design agent.
 *
 * Layout:
 * - Rendered inside AuthenticatedLayout which provides sidebar + AppHeader
 * - Two-column on desktop: 60% chat (MessageList + ChatInput), 40% context panel
 * - Single-column on mobile: context panel accessible via "View summary" Sheet
 *
 * State:
 * - messages: running chat history (loaded from persistent session on mount)
 * - sessionId: active session UUID (read from URL params /chat/:sessionId)
 * - isProcessing: true while SSE stream is active
 * - statusText: most recent status event from SSE
 * - lastCard: most recent structured card (mirrored in context panel)
 * - warningsAcknowledged: user has acknowledged pre-flight warnings
 * - activeJobId: job ID after launch; drives SSE status subscription
 * - jobStatus: most recent JobStatusEvent received via SSE
 *
 * Session lifecycle:
 * - URL param /chat/:sessionId drives the active session
 * - Bare /chat URL: redirect to most recent session or create new one for brand new users
 * - No ephemeral Redis sessions — all sessions are PostgreSQL backed (Plan 06-01)
 * - Session list refresh triggered via useLayoutContext().refreshSessions after agent done
 *
 * Stripe Checkout return handling:
 * - ?setup=success → auto-retry launch on the ReviewCard
 * - ?setup=cancelled → show "payment required" alert on the ReviewCard
 */

import { useState, useEffect, useCallback, useRef, type MouseEvent as ReactMouseEvent, type RefObject } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { StructurePreviewCard } from "./StructurePreviewCard";
import { ReviewCard } from "./ReviewCard";
import { ValidationCard } from "./ValidationCard";
import { JobStatusCard } from "@/components/jobs/JobStatusCard";
import { JobCompletionCard } from "@/components/jobs/JobCompletionCard";
import {
  uploadPdbFile,
  sendMessage,
} from "@/lib/agent";
import type { ChatMessage, ChatCard, AgentEvent, ReviewData, ValidationCheck } from "@/lib/agent";
import {
  subscribeToJobStatus,
  cancelJob,
  getJob,
} from "@/lib/jobs";
import type { JobStatusEvent, JobData } from "@/lib/jobs";
import { listSessions, loadSession, createPersistentSession } from "@/lib/sessions";
import { useLayoutContext } from "@/components/layout/AuthenticatedLayout";

/** Generate a unique message ID */
function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Parse Stripe Checkout return query params from the current URL. */
function getSetupParam(): "success" | "cancelled" | null {
  const params = new URLSearchParams(window.location.search);
  const setup = params.get("setup");
  if (setup === "success") return "success";
  if (setup === "cancelled") return "cancelled";
  return null;
}

/** Remove ?setup= from the URL without a page reload. */
function clearSetupParam() {
  const url = new URL(window.location.href);
  url.searchParams.delete("setup");
  window.history.replaceState({}, "", url.toString());
}

export function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { refreshSessions } = useLayoutContext();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [lastCard, setLastCard] = useState<ChatCard | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);

  // Input value injected by example prompt clicks in GreetingCard or via URL query param
  const [injectedInputValue, setInjectedInputValue] = useState("");
  // Ref forwarded to the ChatInput's textarea for programmatic focus
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);

  // Consume ?prompt= query parameter from navigation (e.g. Export Report button on JobPage)
  useEffect(() => {
    const promptParam = searchParams.get("prompt");
    if (promptParam) {
      setInjectedInputValue(promptParam);
      // Clear the query param so it does not re-inject on re-render
      setSearchParams({}, { replace: true });
    }
  }, []);

  // Job tracking state after launch
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatusEvent | null>(null);
  const [completedJob, setCompletedJob] = useState<JobData | null>(null);

  // Stripe Checkout return state (resolved on mount, cleared after use)
  const [setupParam] = useState<"success" | "cancelled" | null>(() => {
    const param = getSetupParam();
    if (param) clearSetupParam();
    return param;
  });

  // Shared chain selection state — synced between chat card and context panel
  const [selectedChain, setSelectedChain] = useState<string | null>(null);

  // Resizable panel state (percentage for chat panel width)
  const [chatWidthPercent, setChatWidthPercent] = useState(60);
  const isDraggingRef = useRef(false);

  const handleDragStart = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    isDraggingRef.current = true;

    const onMouseMove = (ev: globalThis.MouseEvent) => {
      if (!isDraggingRef.current) return;
      const container = document.getElementById("chat-layout");
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const pct = ((ev.clientX - rect.left) / rect.width) * 100;
      // Clamp between 30% and 80%
      setChatWidthPercent(Math.min(80, Math.max(30, pct)));
    };

    const onMouseUp = () => {
      isDraggingRef.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  // Tracks the in-progress assistant message id during streaming
  const currentAssistantIdRef = useRef<string | null>(null);

  // Ref for the SSE unsubscribe function — cleaned up on unmount or new session
  const jobUnsubscribeRef = useRef<(() => void) | null>(null);

  // Accumulated context from tool results, used to assemble ReviewData across turns.
  const intentResultRef = useRef<{
    design_type: string;
    recommended_tool: string;
    rationale: string;
  } | null>(null);
  const structureResultRef = useRef<Record<string, unknown> | null>(null);
  const parametersResultRef = useRef<{
    tool: string;
    target_chain: string;
    hotspot_residues: number[];
    parameters: Record<string, unknown>;
    parameter_descriptions: Record<string, { label: string; description: string; default: unknown }>;
  } | null>(null);

  // Cleanup SSE subscription on unmount
  useEffect(() => {
    return () => {
      jobUnsubscribeRef.current?.();
    };
  }, []);

  /**
   * Handle bare /chat URL (no sessionId).
   *
   * - If user has existing sessions, navigate to the most recent one.
   * - If user has no sessions (brand new user), create a new persistent session
   *   and navigate to it.
   *
   * This runs only when sessionId is undefined (bare /chat route).
   */
  useEffect(() => {
    if (sessionId) return; // URL already has a session

    let cancelled = false;

    async function resolveSession() {
      try {
        const { sessions } = await listSessions(1);
        if (cancelled) return;

        if (sessions.length > 0) {
          // Navigate to most recent session
          navigate(`/chat/${sessions[0].id}`, { replace: true });
        } else {
          // Brand new user — create a persistent session
          const newSession = await createPersistentSession();
          if (cancelled) return;
          await refreshSessions();
          navigate(`/chat/${newSession.id}`, { replace: true });
        }
      } catch (err) {
        console.error("Failed to resolve session:", err);
      }
    }

    resolveSession();
    return () => {
      cancelled = true;
    };
  }, [sessionId, navigate, refreshSessions]);

  /**
   * Load session history when sessionId changes.
   * Reconstructs messages from the persistent session.
   */
  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;

    // Reset per-session state
    setMessages([]);
    setLastCard(null);
    setWarningsAcknowledged(false);
    setActiveJobId(null);
    setJobStatus(null);
    setCompletedJob(null);
    intentResultRef.current = null;
    structureResultRef.current = null;
    parametersResultRef.current = null;

    async function loadSessionData() {
      try {
        const sessionData = await loadSession(sessionId!);
        if (cancelled) return;

        const loadedMessages: ChatMessage[] = sessionData.messages
          .sort((a, b) => a.sort_order - b.sort_order)
          .map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            cards: m.cards as ChatCard[] | undefined,
          }));

        setMessages(loadedMessages);
      } catch (err) {
        console.error("Failed to load session:", err);
        setMessages([{
          id: newId(),
          role: "assistant",
          content: "Unable to load conversation history. Refresh the page to try again.",
        }]);
      }
    }

    loadSessionData();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /**
   * Called by ReviewCard after a job is successfully dispatched.
   * Stores the job ID, subscribes to SSE updates, and renders an inline
   * JobStatusCard in the chat thread.
   */
  const onJobLaunched = useCallback((jobId: string) => {
    setActiveJobId(jobId);
    setJobStatus(null);
    setCompletedJob(null);

    // Add an inline job status card message to the chat thread
    const statusMessageId = newId();
    setMessages((prev) => [
      ...prev,
      {
        id: statusMessageId,
        role: "assistant" as const,
        content: "",
        // The job status card is rendered by MessageList via the jobId field
      },
    ]);

    // Unsubscribe from any previous job SSE stream
    jobUnsubscribeRef.current?.();

    // Subscribe to real-time job status events
    const unsubscribe = subscribeToJobStatus(
      jobId,
      async (event) => {
        setJobStatus(event);

        // On terminal status: fetch full job data for completion/failure card
        if (event.status === "complete" || event.status === "failed" || event.status === "cancelled") {
          unsubscribe();
          jobUnsubscribeRef.current = null;

          try {
            const fullJob = await getJob(jobId);
            setCompletedJob(fullJob);
          } catch {
            // If fetch fails, use what we know from the SSE event
          }

          // Post-first-job completion one-time guidance message (D-23)
          if (event.status === "complete" && !localStorage.getItem("kendrew_first_job_shown")) {
            const firstJobMsg: ChatMessage = {
              id: newId(),
              role: "assistant",
              content:
                "Your first design job is complete. Find all past sessions in the sidebar and all job results under Jobs.",
            };
            setMessages((prev) => [...prev, firstJobMsg]);
            localStorage.setItem("kendrew_first_job_shown", "true");
          }
        }
      },
      (err) => {
        console.error("Job SSE error:", err);
      },
    );

    jobUnsubscribeRef.current = unsubscribe;
  }, []);

  /**
   * Process a single SSE event from the agent stream.
   * Updates the in-progress assistant message or appends cards/actions.
   */
  const handleEvent = useCallback((event: AgentEvent, assistantId: string) => {
    if (event.type === "status") {
      setStatusText(event.text);
    } else if (event.type === "text") {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantId
            ? { ...msg, content: msg.content + event.text }
            : msg,
        ),
      );
    } else if (event.type === "tool_result") {
      const card = buildCard(event.tool_name, event.result);
      if (card) {
        setLastCard(card);
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id !== assistantId) return msg;
            const existing = msg.cards ?? [];
            // Structure cards: replace any previous structure card (don't accumulate)
            if (card.type === "structure_preview") {
              const filtered = existing.filter((c) => c.type !== "structure_preview");
              return { ...msg, cards: [...filtered, card] };
            }
            return { ...msg, cards: [...existing, card] };
          }),
        );
      }

    } else if (event.type === "done") {
      setIsProcessing(false);
      setStatusText("");
      abortControllerRef.current = null;
      // Refresh sidebar session list so new/updated title appears
      refreshSessions().catch(() => {});
    } else if (event.type === "error") {
      setIsProcessing(false);
      setStatusText("");
      abortControllerRef.current = null;
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `Error: ${event.text}`,
        },
      ]);
    }
  }, [refreshSessions]);

  /**
   * Build a ChatCard from a tool_result SSE event.
   *
   * Side effects: updates intentResultRef, structureResultRef, and
   * parametersResultRef to accumulate data needed for the ReviewCard.
   */
  function buildCard(
    toolName: string,
    result: Record<string, unknown>,
  ): ChatCard | null {
    if (toolName === "resolve_structure") {
      // Only show a structure card if we got real PDB metadata (pdb_id + chains)
      if (result.pdb_id && result.chains) {
        structureResultRef.current = result;
        return { type: "structure_preview", data: result as unknown as ChatCard["data"] } as ChatCard;
      }
      // UniProt-only results (no PDB resolved yet) — store ref but don't render a card
      structureResultRef.current = result;
      return null;
    }

    if (toolName === "extract_interface") {
      // Interface extraction results don't need their own card — data feeds into parameters
      return null;
    }

    if (toolName === "classify_intent") {
      intentResultRef.current = {
        design_type: result.design_type as string,
        recommended_tool: result.recommended_tool as string,
        rationale: result.rationale as string,
      };
      return null;
    }

    if (toolName === "collect_parameters") {
      parametersResultRef.current = {
        tool: result.tool as string,
        target_chain: result.target_chain as string,
        hotspot_residues: (result.hotspot_residues as number[]) ?? [],
        parameters: (result.parameters as Record<string, unknown>) ?? {},
        parameter_descriptions: (result.parameter_descriptions as Record<
          string,
          { label: string; description: string; default: unknown }
        >) ?? {},
      };
      return null;
    }

    if (toolName === "validate_preflight") {
      const validationCard: ChatCard = {
        type: "validation",
        data: result as unknown as {
          validation_results: ValidationCheck[];
          can_proceed: boolean;
          has_warnings: boolean;
          summary: string;
        },
      };

      if (parametersResultRef.current) {
        const params = parametersResultRef.current;
        const intent = intentResultRef.current;
        const structure = structureResultRef.current;

        const reviewData: ReviewData = {
          design_goal: intent?.design_type
            ? `${intent.design_type.replace(/_/g, " ")} — ${params.tool}`
            : `Design job using ${params.tool}`,
          tool: params.tool,
          rationale: intent?.rationale ?? "",
          target_pdb_id:
            (structure?.pdb_id as string) ??
            (structure?.uniprot_accession as string) ??
            "unknown",
          target_chain: params.target_chain,
          hotspot_residues: params.hotspot_residues,
          parameters: { ...params.parameters, job_id: result.job_id as string },
          parameter_descriptions: params.parameter_descriptions,
          estimated_cost_usd: 0, // Replaced by live getCostEstimate call in ReviewCard
          validation_results: (result.validation_results as ReviewData["validation_results"]) ?? [],
          can_proceed: (result.can_proceed as boolean) ?? false,
          has_warnings: (result.has_warnings as boolean) ?? false,
        };

        setTimeout(() => {
          setLastCard({ type: "review", data: reviewData });
          setMessages((prev) => {
            const aid = currentAssistantIdRef.current;
            if (!aid) return prev;
            return prev.map((msg) =>
              msg.id === aid
                ? { ...msg, cards: [...(msg.cards ?? []), { type: "review", data: reviewData }] }
                : msg,
            );
          });
        }, 0);
      }

      return validationCard;
    }

    return null;
  }

  const handleSend = useCallback(
    async (text: string, file?: File) => {
      if (!sessionId) return;
      if (isProcessing) return;

      setIsProcessing(true);
      setWarningsAcknowledged(false);

      let messageText = text;

      if (file) {
        try {
          const uploadResult = await uploadPdbFile(file);
          const fileContext = `[Uploaded file: ${file.name} → ${uploadResult.normalized_path}]`;
          messageText = messageText ? `${fileContext}\n\n${messageText}` : fileContext;
        } catch (err) {
          setMessages((prev) => [
            ...prev,
            {
              id: newId(),
              role: "assistant",
              content: `Failed to upload ${file.name}: ${err instanceof Error ? err.message : "Unknown error"}`,
            },
          ]);
          setIsProcessing(false);
          return;
        }
      }

      const userMessage: ChatMessage = {
        id: newId(),
        role: "user",
        content: text || `[Uploaded ${file?.name ?? "file"}]`,
      };

      const assistantId = newId();
      currentAssistantIdRef.current = assistantId;
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);

      try {
        const controller = new AbortController();
        abortControllerRef.current = controller;
        await sendMessage(sessionId, messageText, (event) =>
          handleEvent(event, assistantId),
          controller.signal,
        );
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // User clicked stop — just end processing
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId && !msg.content
                ? { ...msg, content: "Stopped." }
                : msg,
            ),
          );
          setIsProcessing(false);
          abortControllerRef.current = null;
          return;
        }
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? {
                  ...msg,
                  content: `Connection error: ${err instanceof Error ? err.message : "Unknown error"}`,
                }
              : msg,
          ),
        );
        setIsProcessing(false);
        setStatusText("");
      }
    },
    [sessionId, isProcessing, handleEvent],
  );

  /** Shared chain selection handler — used by both chat and context panel cards */
  const handleChainSelected = useCallback(
    (chainId: string) => {
      setSelectedChain(chainId);
      if (structureResultRef.current) {
        structureResultRef.current.selected_chain = chainId;
      }
      const chains = (lastCard?.data as Record<string, unknown> | undefined)?.chains as Array<{id: string; name: string}> | undefined;
      const chain = chains?.find((c) => c.id === chainId);
      const chainLabel = chain ? `${chainId} (${chain.name})` : chainId;
      handleSend(`I want to target chain ${chainLabel}`);
    },
    [lastCard, handleSend],
  );

  /** Render the context panel content (most recent structured card) */
  function renderContextPanel() {
    if (!lastCard) {
      return (
        <div className="flex items-center justify-center h-full">
          <p className="text-sm text-muted-foreground text-center px-4">
            Structured cards will appear here as you work through your design.
          </p>
        </div>
      );
    }

    if (lastCard.type === "structure_preview") {
      return (
        <StructurePreviewCard
          data={lastCard.data}
          selectedChainOverride={selectedChain ?? undefined}
          onUseDifferent={() => handleSend("I want to use a different structure")}
          onChainSelected={handleChainSelected}
        />
      );
    }
    if (lastCard.type === "validation") {
      return (
        <ValidationCard
          data={lastCard.data}
          onAcknowledge={() => setWarningsAcknowledged(true)}
          acknowledged={warningsAcknowledged}
        />
      );
    }
    if (lastCard.type === "review") {
      // After job launch, show summary without action buttons
      if (activeJobId) {
        const rd = lastCard.data as ReviewData;
        return (
          <div className="rounded-xl border border-border/50 bg-card p-4 space-y-3 font-body">
            <span className="font-display text-xs font-semibold text-muted-foreground uppercase tracking-[0.15em]">
              Job Review
            </span>
            <div>
              <p className="text-sm text-muted-foreground">Design goal</p>
              <p className="font-display text-base text-foreground">{rd.design_goal}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Tool</p>
              <p className="text-base text-foreground font-semibold">{rd.tool}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Target</p>
              <p className="text-base font-mono text-foreground">{rd.target_pdb_id} / chain {rd.target_chain}</p>
            </div>
            <p className="text-sm text-emerald-400 font-semibold">Job launched successfully.</p>
          </div>
        );
      }
      return (
        <ReviewCard
          data={lastCard.data}
          onJobLaunched={onJobLaunched}
          onEdit={() => handleSend("I want to edit the parameters")}
          disabled={isProcessing}
          warningsAcknowledged={warningsAcknowledged}
          autoRetryAfterSetup={setupParam === "success"}
          setupCancelled={setupParam === "cancelled"}
        />
      );
    }
    return null;
  }

  /**
   * Render the inline job tracking section shown in chat after a job is launched.
   * Shows a JobStatusCard while running, then a JobCompletionCard on completion.
   */
  function renderInlineJobTracking() {
    if (!activeJobId) return null;

    // If job has reached a terminal state
    if (completedJob) {
      if (completedJob.status === "complete") {
        return (
          <div className="px-4 py-2">
            <JobCompletionCard
              jobId={activeJobId}
              candidateCount={completedJob.results?.candidate_count ?? 0}
              gpuSeconds={completedJob.gpu_seconds}
              gpuCostUsd={completedJob.gpu_cost_usd}
            />
          </div>
        );
      }
      if (completedJob.status === "failed") {
        return (
          <div className="px-4 py-2">
            <p className="text-sm text-muted-foreground">
              Job failed
              {completedJob.error_category ? ` — ${completedJob.error_category}` : ""}.
              View details on the{" "}
              <a href={`/jobs/${activeJobId}`} className="underline text-foreground">
                job page
              </a>
              .
            </p>
          </div>
        );
      }
      if (completedJob.status === "cancelled") {
        return (
          <div className="px-4 py-2">
            <p className="text-sm text-muted-foreground">
              Job cancelled. View details on the{" "}
              <a href={`/jobs/${activeJobId}`} className="underline text-foreground">
                job page
              </a>
              .
            </p>
          </div>
        );
      }
    }

    // While job is running or queued — show JobStatusCard
    if (jobStatus) {
      const currentStatus = jobStatus.status as "queued" | "running" | "complete" | "failed" | "cancelled";
      // Extract tool from the review card data if available
      const tool = lastCard?.type === "review" ? lastCard.data.tool : "rfdiffusion";

      return (
        <div className="px-4 py-2">
          <JobStatusCard
            jobId={activeJobId}
            status={currentStatus}
            stage={jobStatus.stage}
            tool={tool}
            onCancel={async () => {
              await cancelJob(activeJobId);
            }}
          />
        </div>
      );
    }

    // Brief gap between launch and first SSE event
    return (
      <div className="px-4 py-2">
        <p className="text-sm text-muted-foreground">Job queued...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Main content area */}
      <div id="chat-layout" className="flex flex-1 overflow-hidden">
        {/* Left column: chat thread (resizable on desktop, full on mobile) */}
        <div
          className="flex flex-col flex-1 overflow-hidden surface-chat"
          style={{ flexBasis: `${chatWidthPercent}%`, maxWidth: `${chatWidthPercent}%` }}
        >
          <MessageList
            messages={messages}
            isProcessing={isProcessing}
            statusText={statusText}
            warningsAcknowledged={warningsAcknowledged}
            onJobLaunched={onJobLaunched}
            onEditParams={() => handleSend("I want to edit the parameters")}
            onAcknowledgeWarnings={() => setWarningsAcknowledged(true)}
            onUseDifferentStructure={() => handleSend("I want to use a different structure")}
            onChainSelected={handleChainSelected}
            selectedChain={selectedChain ?? undefined}
            onPromptClick={(prompt) => {
              setInjectedInputValue(prompt);
              chatInputRef.current?.focus();
            }}
          />
          {/* Inline job tracking section — appears below messages after launch */}
          {renderInlineJobTracking()}
          <ChatInput
            onSend={handleSend}
            isProcessing={isProcessing}
            injectedValue={injectedInputValue}
            onInjectedValueConsumed={() => setInjectedInputValue("")}
            textareaRef={chatInputRef as RefObject<HTMLTextAreaElement | null>}
            onStop={() => {
              abortControllerRef.current?.abort();
              abortControllerRef.current = null;
              setIsProcessing(false);
              setStatusText("");
            }}
          />
        </div>

        {/* Mobile: context panel sheet trigger */}
        <div className="md:hidden absolute top-2 right-2">
          <Sheet>
            <SheetTrigger
              render={
                <Button variant="ghost" size="sm">
                  View summary
                </Button>
              }
            />
            <SheetContent side="bottom" className="h-[60vh]">
              <SheetHeader>
                <SheetTitle>Current Summary</SheetTitle>
              </SheetHeader>
              <div className="overflow-y-auto mt-4 px-1">{renderContextPanel()}</div>
            </SheetContent>
          </Sheet>
        </div>

        {/* Drag handle for resizing panels */}
        <div
          onMouseDown={handleDragStart}
          className="hidden md:flex items-center justify-center w-1.5 cursor-col-resize hover:bg-primary/20 active:bg-primary/30 transition-colors shrink-0"
        >
          <div className="w-0.5 h-8 rounded-full bg-border" />
        </div>

        {/* Right column: context panel (resizable on desktop, hidden on mobile) */}
        <div
          className="hidden md:flex md:flex-col overflow-y-auto p-4 surface-context"
          style={{ flexBasis: `${100 - chatWidthPercent}%`, maxWidth: `${100 - chatWidthPercent}%` }}
        >
          <p className="font-display text-xs font-semibold text-muted-foreground uppercase tracking-[0.15em] mb-3">
            Current context
          </p>
          {renderContextPanel()}
        </div>
      </div>
    </div>
  );
}
