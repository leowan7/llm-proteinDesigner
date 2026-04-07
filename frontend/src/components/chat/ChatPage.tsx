/**
 * ChatPage — full-width chat interface for the protein design agent.
 *
 * Layout:
 * - Two-column on desktop: 60% chat (MessageList + ChatInput), 40% context panel
 * - Single-column on mobile: context panel accessible via "View summary" Sheet
 * - Full-width, does NOT use AuthLayout (chat needs the full viewport)
 *
 * State:
 * - messages: running chat history
 * - sessionId: active agent session (created on mount)
 * - isProcessing: true while SSE stream is active
 * - statusText: most recent status event from SSE
 * - lastCard: most recent structured card (mirrored in context panel)
 * - warningsAcknowledged: user has acknowledged pre-flight warnings
 * - activeJobId: job ID after launch; drives SSE status subscription
 * - jobStatus: most recent JobStatusEvent received via SSE
 *
 * Session lifecycle:
 * - createSession on mount
 * - deleteSession + createSession on "New Session"
 * - No persistence between page refreshes (session is ephemeral)
 *
 * Stripe Checkout return handling:
 * - ?setup=success → auto-retry launch on the ReviewCard
 * - ?setup=cancelled → show "payment required" alert on the ReviewCard
 */

import { useState, useEffect, useCallback, useRef, type RefObject } from "react";
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
  createSession,
  deleteSession,
  uploadPdbFile,
  sendMessage,
} from "@/lib/agent";
import type { ChatMessage, ChatCard, AgentEvent, ActionButton, ReviewData } from "@/lib/agent";
import {
  subscribeToJobStatus,
  cancelJob,
  getJob,
} from "@/lib/jobs";
import type { JobStatusEvent, JobData } from "@/lib/jobs";

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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [lastCard, setLastCard] = useState<ChatCard | null>(null);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [showNewSessionConfirm, setShowNewSessionConfirm] = useState(false);

  // Input value injected by example prompt clicks in GreetingCard
  const [injectedInputValue, setInjectedInputValue] = useState("");
  // Ref forwarded to the ChatInput's textarea for programmatic focus
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);

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

  /** Initialize a new session on mount */
  useEffect(() => {
    let cancelled = false;
    createSession()
      .then((id) => {
        if (!cancelled) setSessionId(id);
      })
      .catch((err) => {
        console.error("Failed to create session:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, cards: [...(msg.cards ?? []), card] }
              : msg,
          ),
        );
      }

      const actions = buildActions(event.tool_name, event.result);
      if (actions.length > 0) {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, actions: [...(msg.actions ?? []), ...actions] }
              : msg,
          ),
        );
      }
    } else if (event.type === "done") {
      setIsProcessing(false);
      setStatusText("");
    } else if (event.type === "error") {
      setIsProcessing(false);
      setStatusText("");
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `Error: ${event.text}`,
        },
      ]);
    }
  }, []);

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
      structureResultRef.current = result;
      return { type: "structure_preview", data: result } as ChatCard;
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
        data: result as ChatCard["data"] & object,
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
          parameters: params.parameters,
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

  function buildActions(
    toolName: string,
    result: Record<string, unknown>,
  ): ActionButton[] {
    if (toolName === "classify_intent") {
      const tool = (result.recommended_tool as string) ?? "the recommended tool";
      const toolLabel = tool.charAt(0).toUpperCase() + tool.slice(1);
      return [
        { label: `Yes, use ${toolLabel}`, value: `Yes, use ${toolLabel}` },
        { label: "Let me change something", value: "Let me change something" },
      ];
    }
    return [];
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
        await sendMessage(sessionId, messageText, (event) =>
          handleEvent(event, assistantId),
        );
      } catch (err) {
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

  const handleAction = useCallback(
    (value: string) => {
      handleSend(value);
    },
    [handleSend],
  );

  const handleNewSession = useCallback(async () => {
    // Unsubscribe from any active job SSE stream
    jobUnsubscribeRef.current?.();
    jobUnsubscribeRef.current = null;

    if (sessionId) {
      await deleteSession(sessionId).catch(() => {});
    }
    const newSessionId = await createSession().catch(() => null);
    if (newSessionId) {
      setSessionId(newSessionId);
    }
    setMessages([]);
    setLastCard(null);
    setWarningsAcknowledged(false);
    setIsProcessing(false);
    setStatusText("");
    setShowNewSessionConfirm(false);
    setActiveJobId(null);
    setJobStatus(null);
    setCompletedJob(null);

    intentResultRef.current = null;
    structureResultRef.current = null;
    parametersResultRef.current = null;
  }, [sessionId]);

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
          onUseDifferent={() => handleSend("I want to use a different structure")}
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
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2.5">
          <img src="/logo.svg" alt="Kendrew.AI" className="size-7" />
          <h1 className="text-xl font-semibold text-foreground">Kendrew<span className="text-primary">.AI</span></h1>
        </div>
        <div className="flex items-center gap-2">
          {/* Mobile: context panel sheet trigger */}
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" className="md:hidden">
                View summary
              </Button>
            </SheetTrigger>
            <SheetContent side="bottom" className="h-[60vh]">
              <SheetHeader>
                <SheetTitle>Current Summary</SheetTitle>
              </SheetHeader>
              <div className="overflow-y-auto mt-4 px-1">{renderContextPanel()}</div>
            </SheetContent>
          </Sheet>

          {/* New Session button with inline confirmation */}
          {!showNewSessionConfirm ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowNewSessionConfirm(true)}
            >
              New Session
            </Button>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                Start new session? Your current conversation will be cleared.
              </span>
              <Button variant="default" size="sm" onClick={handleNewSession}>
                Confirm
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowNewSessionConfirm(false)}
              >
                Cancel
              </Button>
            </div>
          )}
        </div>
      </header>

      {/* Main content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: chat thread (60% on desktop, full on mobile) */}
        <div className="flex flex-col flex-1 md:w-3/5 md:max-w-[60%] overflow-hidden">
          <MessageList
            messages={messages}
            isProcessing={isProcessing}
            statusText={statusText}
            warningsAcknowledged={warningsAcknowledged}
            onAction={handleAction}
            onJobLaunched={onJobLaunched}
            onEditParams={() => handleSend("I want to edit the parameters")}
            onAcknowledgeWarnings={() => setWarningsAcknowledged(true)}
            onUseDifferentStructure={() => handleSend("I want to use a different structure")}
            onPromptClick={(prompt) => {
              // Auto-fill the chat input with the selected example prompt (D-21)
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
          />
        </div>

        {/* Right column: context panel (40% on desktop, hidden on mobile) */}
        <div className="hidden md:flex md:flex-col md:w-2/5 border-l border-border overflow-y-auto p-4">
          <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            Current context
          </p>
          {renderContextPanel()}
        </div>
      </div>
    </div>
  );
}
