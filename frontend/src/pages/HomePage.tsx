import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const CAPABILITIES = [
  {
    label: "BindCraft",
    metric: "E2E",
    metricLabel: "automated pipeline",
    detail:
      "End-to-end binder design through AF2 hallucination with integrated filtering. Produces ready-to-express sequences — no separate sequence design step required.",
  },
  {
    label: "RFdiffusion",
    metric: "5",
    metricLabel: "design modes",
    detail:
      "Backbone diffusion for minibinders, motif scaffolds, symmetric oligomers, partial diffusion, and fold-conditioned generation. Most experimentally validated tool in the field.",
  },
  {
    label: "BoltzGen",
    metric: "6",
    metricLabel: "design protocols",
    detail:
      "All-atom co-design with joint sequence-structure output. Native protocols for nanobodies, antibodies, cyclic peptides, small-molecule binders, and protein redesign.",
  },
  {
    label: "RFantibody",
    metric: "nM",
    metricLabel: "validated affinity",
    detail:
      "Purpose-built for antibody and nanobody CDR loop design. Cryo-EM validated at 1.45 \u00C5 backbone RMSD to design models.",
  },
  {
    label: "PXDesign",
    metric: "2",
    metricLabel: "predictor filtering",
    detail:
      "Diffusion-based design with multi-predictor confidence filtering (AF2-IG + Protenix). Dual validation catches false positives missed by single predictors.",
  },
];

const STEPS = [
  {
    num: "01",
    title: "Define your target",
    body: "Paste a PDB ID, upload a .pdb or .cif, or describe your protein in plain language. Kendrew resolves the structure and surfaces chain-level metadata for selection.",
  },
  {
    num: "02",
    title: "Describe your intent",
    body: "Tell Kendrew what you need and why. It asks targeted questions about your downstream application, then recommends the right computational tool with expert-tuned defaults.",
  },
  {
    num: "03",
    title: "Launch on cloud GPUs",
    body: "Jobs dispatch to A100-class hardware. Track progress via real-time streaming, then download filtered candidates ranked by confidence metrics.",
  },
];

export function HomePage() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 40);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-screen font-body text-foreground" style={{ background: "oklch(0.13 0.006 260)" }}>
      {/* Ambient vertical lines */}
      <div className="helix-line" style={{ left: "12%" }} />
      <div className="helix-line" style={{ left: "88%", opacity: 0.5 }} />

      {/* ── Floating Nav ── */}
      <nav
        className={`sticky top-0 z-50 flex items-center justify-between px-8 md:px-16 py-5 transition-all duration-300 ${
          scrolled
            ? "border-b border-border/30 backdrop-blur-xl"
            : "border-b border-transparent"
        }`}
        style={{
          background: scrolled ? "oklch(0.115 0.008 265 / 85%)" : "transparent",
        }}
      >
        <div className="flex items-center gap-3">
          <div className="size-9 rounded-md bg-primary flex items-center justify-center text-primary-foreground font-display font-semibold text-base">
            K
          </div>
          <span className="font-display text-xl tracking-tight">
            Kendrew<span className="text-primary">.AI</span>
          </span>
        </div>
        <div className="flex items-center gap-7">
          <Link to="/docs" className="text-base text-muted-foreground hover:text-foreground transition-colors hidden md:block">
            Docs
          </Link>
          <Link to="/resources" className="text-base text-muted-foreground hover:text-foreground transition-colors hidden md:block">
            Resources
          </Link>
          <Link to="/login" className="text-base text-muted-foreground hover:text-foreground transition-colors">
            Sign in
          </Link>
          <Link to="/signup">
            <Button className="rounded-full px-6 h-10 text-sm">
              Get started
            </Button>
          </Link>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section
        className="relative overflow-hidden"
        style={{
          background: "oklch(0.115 0.008 265)",
          minHeight: "min(92vh, 800px)",
        }}
      >
        {/* Layered background: gradient mesh */}
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: [
              "radial-gradient(ellipse 70% 60% at 65% 40%, oklch(0.32 0.12 277 / 80%) 0%, transparent 70%)",
              "radial-gradient(ellipse 50% 50% at 10% 80%, oklch(0.25 0.06 260 / 55%) 0%, transparent 60%)",
              "radial-gradient(circle at 85% 15%, oklch(0.28 0.08 290 / 50%) 0%, transparent 40%)",
            ].join(", "),
          }}
        />

        {/* Molecular orbit rings — decorative, right side */}
        <div className="absolute inset-0 hidden md:block">
          {/* Outermost orbit */}
          <div
            className="hero-orbit"
            style={{
              width: "620px", height: "620px",
              top: "50%", left: "72%",
              animation: "orbit-spin 80s linear infinite",
              borderColor: "oklch(0.8 0 0 / 10%)",
            }}
          >
            <div className="hero-orbit-dot" style={{ top: "15%", left: "95%", transform: "translate(-50%, -50%)", width: "8px", height: "8px" }} />
          </div>
          {/* Second orbit */}
          <div
            className="hero-orbit"
            style={{
              width: "460px", height: "460px",
              top: "50%", left: "72%",
              animation: "orbit-spin-reverse 55s linear infinite",
            }}
          >
            <div className="hero-orbit-dot" style={{ top: "0%", left: "50%", transform: "translate(-50%, -50%)", width: "10px", height: "10px" }} />
          </div>
          {/* Third orbit */}
          <div
            className="hero-orbit"
            style={{
              width: "300px", height: "300px",
              top: "50%", left: "72%",
              animation: "orbit-spin 40s linear infinite",
              borderColor: "oklch(0.75 0 0 / 12%)",
            }}
          >
            <div className="hero-orbit-dot" style={{ top: "50%", left: "100%", transform: "translate(-50%, -50%)", width: "8px", height: "8px", opacity: 0.9 }} />
          </div>
          {/* Innermost orbit */}
          <div
            className="hero-orbit"
            style={{
              width: "160px", height: "160px",
              top: "50%", left: "72%",
              borderColor: "oklch(0.7 0 0 / 8%)",
            }}
          />
          {/* Center node */}
          <div
            className="absolute rounded-full"
            style={{
              width: "14px", height: "14px",
              top: "50%", left: "72%",
              transform: "translate(-50%, -50%)",
              background: "oklch(0.95 0 0)",
              boxShadow: "0 0 30px oklch(0.9 0 0 / 50%), 0 0 80px oklch(0.9 0 0 / 20%)",
              animation: "pulse-soft 4s ease-in-out infinite",
            }}
          />
        </div>

        {/* Bottom edge gradient fade into next section */}
        <div
          className="absolute bottom-0 left-0 right-0 h-32"
          style={{ background: "linear-gradient(to bottom, transparent, oklch(0.15 0.005 260))" }}
        />

        {/* Content */}
        <div className="relative z-10 max-w-5xl mx-auto px-8 md:px-16 pt-28 pb-36 md:pt-40 md:pb-48 flex flex-col justify-center">
          <p
            className="animate-fade-in font-body text-base tracking-[0.3em] uppercase text-primary font-semibold mb-10"
            style={{ animationDelay: "0.1s" }}
          >
            Computational protein design
          </p>
          <h1
            className="animate-fade-in-up font-display text-[2.75rem] sm:text-[3.5rem] md:text-[5rem] leading-[1.05] tracking-tight max-w-2xl"
            style={{ animationDelay: "0.25s" }}
          >
            From target structure{" "}
            <br className="hidden sm:block" />
            to <span className="hero-gradient-text">expressible</span>{" "}
            <br className="hidden sm:block" />
            sequences.
          </h1>
          <p
            className="animate-fade-in-up mt-8 text-lg md:text-xl text-muted-foreground max-w-xl leading-relaxed"
            style={{ animationDelay: "0.5s" }}
          >
            Upload a PDB, describe your design goal, and launch jobs on
            five state-of-the-art generative models. Kendrew handles tool
            selection, configuration, and result analysis.
          </p>
          <div
            className="animate-fade-in-up mt-14 flex items-center gap-5"
            style={{ animationDelay: "0.7s" }}
          >
            <Link to="/signup">
              <Button size="lg" className="rounded-full px-10 gap-2 text-base h-12 shadow-lg shadow-primary/20">
                Get started <ArrowRight className="size-4" />
              </Button>
            </Link>
            <Link
              to="/login"
              className="text-base text-muted-foreground hover:text-foreground transition-colors underline underline-offset-4 decoration-border"
            >
              Sign in
            </Link>
          </div>
        </div>
      </section>

      {/* ── Capabilities ── */}
      <section className="px-8 md:px-16 py-16" style={{ background: "oklch(0.15 0.005 260)" }}>
        <div className="max-w-5xl mx-auto">
        <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4 mb-12">
          <h2 className="font-display text-3xl md:text-4xl tracking-tight">
            Five engines, one conversation.
          </h2>
          <p className="text-base text-muted-foreground max-w-sm">
            Kendrew selects the right model for your design goal and
            handles configuration automatically.
          </p>
        </div>

        <div className="space-y-px rounded-xl overflow-hidden">
          {CAPABILITIES.map((c) => (
            <div
              key={c.label}
              className="group flex flex-col md:flex-row md:items-baseline gap-4 md:gap-8 px-6 py-6 transition-colors hover:bg-[oklch(0.16_0.005_260)]"
              style={{ background: "oklch(0.145 0.005 260)" }}
            >
              <div className="flex items-baseline justify-between md:w-60 shrink-0">
                <span className="text-base tracking-[0.12em] uppercase text-muted-foreground">
                  {c.label}
                </span>
                <span className="md:hidden text-right">
                  <span className="font-display text-xl text-foreground">{c.metric}</span>
                  <span className="text-sm text-muted-foreground ml-1">{c.metricLabel}</span>
                </span>
              </div>
              <p className="text-base text-muted-foreground leading-relaxed flex-1 group-hover:text-foreground/70 transition-colors">
                {c.detail}
              </p>
              <span className="hidden md:block text-right shrink-0">
                <span className="font-display text-3xl text-foreground">{c.metric}</span>
                <span className="text-sm text-muted-foreground ml-1.5">{c.metricLabel}</span>
              </span>
            </div>
          ))}
        </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="px-8 md:px-16 py-16" style={{ background: "oklch(0.13 0.006 260)" }}>
        <div className="max-w-5xl mx-auto">
        <h2 className="font-display text-3xl md:text-4xl tracking-tight mb-12">
          How it works
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {STEPS.map((s) => (
            <div
              key={s.num}
              className="rounded-xl border border-border/40 p-6 flex flex-col"
              style={{ background: "oklch(0.155 0.005 260)" }}
            >
              <span className="font-display text-3xl text-primary mb-4 select-none">
                {s.num}
              </span>
              <h3 className="font-display text-xl mb-3">{s.title}</h3>
              <p className="text-base text-muted-foreground leading-relaxed flex-1">
                {s.body}
              </p>
            </div>
          ))}
        </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="relative overflow-hidden" style={{ background: "oklch(0.11 0.006 260)" }}>
        <div className="hero-glow" style={{ bottom: "-300px", right: "-100px", background: "oklch(0.62 0.25 277)" }} />
        <div className="relative z-10 max-w-5xl mx-auto px-8 md:px-16 py-16 flex flex-col md:flex-row md:items-center md:justify-between gap-8">
          <div>
            <h2 className="font-display text-3xl md:text-4xl tracking-tight mb-3">
              Start designing today.
            </h2>
            <p className="text-muted-foreground text-base max-w-md leading-relaxed">
              Create an account and launch your first protein design job
              in minutes. Pay only for the GPU time you use.
            </p>
          </div>
          <Link to="/signup" className="shrink-0">
            <Button size="lg" className="rounded-full px-10 gap-2 text-base h-12">
              Create account <ArrowRight className="size-4" />
            </Button>
          </Link>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="px-8 md:px-16 py-8 border-t border-border/20" style={{ background: "oklch(0.10 0.006 260)" }}>
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4 text-sm text-muted-foreground/60">
          <span className="font-display">Kendrew.AI &copy; {new Date().getFullYear()}</span>
          <div className="flex items-center gap-6">
            <Link to="/terms" className="hover:text-foreground transition-colors">Terms of Service</Link>
            <Link to="/privacy" className="hover:text-foreground transition-colors">Privacy Policy</Link>
            <Link to="/docs" className="hover:text-foreground transition-colors">Documentation</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
