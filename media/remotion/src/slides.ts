export type ArchitectureNode = {
  id: string;
  label: string;
  detail: string;
  x: number;
  y: number;
  tone: "signal" | "core" | "memory" | "artifact" | "guardrail" | "tool";
};

export type ArchitectureLink = {
  from: string;
  to: string;
  label: string;
};

export type ArchitectureSlide = {
  title: string;
  subtitle: string;
  focus: string;
  nodes: ArchitectureNode[];
  links: ArchitectureLink[];
  checkpoints: string[];
};

export const architectureSlides: ArchitectureSlide[] = [
  {
    title: "Entrada confiable",
    subtitle: "Telegram y la API local reciben mensajes; la autoridad del tenant nunca viene del texto.",
    focus: "Channels",
    nodes: [
      {
        id: "user",
        label: "Usuario",
        detail: "Texto, voz o comando",
        x: 0,
        y: 240,
        tone: "signal",
      },
      {
        id: "gateway",
        label: "Channel Gateway",
        detail: "Telegram webhook y API local",
        x: 330,
        y: 150,
        tone: "guardrail",
      },
      {
        id: "normalizer",
        label: "Normalizer",
        detail: "Mensaje canonico y media",
        x: 660,
        y: 290,
        tone: "tool",
      },
      {
        id: "principal",
        label: "Principal",
        detail: "tenant_id confiable",
        x: 990,
        y: 150,
        tone: "core",
      },
    ],
    links: [
      { from: "user", to: "gateway", label: "envia" },
      { from: "gateway", to: "normalizer", label: "normaliza" },
      { from: "normalizer", to: "principal", label: "vincula" },
    ],
    checkpoints: [
      "Webhook secreto y allowlist",
      "Audio se transcribe antes de rutear",
      "Tenant viene del runtime, no del prompt",
    ],
  },
  {
    title: "Workflow L2",
    subtitle: "El camino principal es deterministico; el LLM solo ayuda cuando el lenguaje lo necesita.",
    focus: "Runtime",
    nodes: [
      {
        id: "router",
        label: "Command Router",
        detail: "Comandos y fallback natural",
        x: 0,
        y: 100,
        tone: "core",
      },
      {
        id: "llm",
        label: "LLMProvider",
        detail: "Clasifica y extrae JSON",
        x: 420,
        y: 310,
        tone: "signal",
      },
      {
        id: "approval",
        label: "Approval Gate",
        detail: "P3/P5 antes de escribir",
        x: 760,
        y: 100,
        tone: "guardrail",
      },
      {
        id: "tools",
        label: "Tool Ports",
        detail: "Calendar, scheduler, TTS",
        x: 990,
        y: 400,
        tone: "tool",
      },
    ],
    links: [
      { from: "router", to: "llm", label: "fallback" },
      { from: "llm", to: "approval", label: "propone" },
      { from: "approval", to: "tools", label: "autoriza" },
      { from: "tools", to: "router", label: "resultado" },
    ],
    checkpoints: [
      "Schemas Pydantic estrictos",
      "Prompts versionados fuera del codigo",
      "Side effects idempotentes",
    ],
  },
  {
    title: "Durabilidad local",
    subtitle: "Estado, eventos y trazas sobreviven al reinicio cuando se activa Postgres.",
    focus: "Persistence",
    nodes: [
      {
        id: "workflow",
        label: "Workflow State",
        detail: "Replay y aprobaciones",
        x: 0,
        y: 120,
        tone: "core",
      },
      {
        id: "events",
        label: "Events + Outbox",
        detail: "CloudEvents y leases",
        x: 430,
        y: 260,
        tone: "guardrail",
      },
      {
        id: "memory",
        label: "Memory",
        detail: "Hechos confirmados",
        x: 820,
        y: 100,
        tone: "memory",
      },
      {
        id: "postgres",
        label: "Postgres",
        detail: "assistant_* JSONB",
        x: 820,
        y: 420,
        tone: "memory",
      },
    ],
    links: [
      { from: "workflow", to: "events", label: "emite" },
      { from: "events", to: "postgres", label: "persiste" },
      { from: "workflow", to: "memory", label: "consulta" },
      { from: "memory", to: "postgres", label: "guarda" },
    ],
    checkpoints: [
      "Memory por tenant y usuario",
      "Outbox reprocesable",
      "Modo memory para pruebas locales",
    ],
  },
  {
    title: "Operabilidad visible",
    subtitle: "El dashboard local muestra agenda, recordatorios, errores, trazas y estado durable.",
    focus: "Ops",
    nodes: [
      {
        id: "worker",
        label: "Workers",
        detail: "Due reminders y outbox",
        x: 0,
        y: 230,
        tone: "tool",
      },
      {
        id: "notify",
        label: "Notifications",
        detail: "Telegram text y audio",
        x: 360,
        y: 130,
        tone: "core",
      },
      {
        id: "admin",
        label: "Admin",
        detail: "Agenda, errores, trazas",
        x: 710,
        y: 320,
        tone: "guardrail",
      },
      {
        id: "artifacts",
        label: "Public Artifacts",
        detail: "README, Draw.io, video",
        x: 990,
        y: 150,
        tone: "artifact",
      },
    ],
    links: [
      { from: "worker", to: "notify", label: "despacha" },
      { from: "notify", to: "admin", label: "audita" },
      { from: "admin", to: "artifacts", label: "documenta" },
    ],
    checkpoints: [
      "Admin loopback-only y token opcional",
      "Errores agrupados por categoria y run",
      "Artefactos sin secretos",
    ],
  },
];
