import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/api/eskd";
import type { ModelHealthStatus } from "@/types/eskd";
import styles from "./ModelStatusIndicator.module.css";

type ModelState = "online" | "loading" | "offline" | "idle";

function resolveVlmState(status?: ModelHealthStatus, errored?: boolean): ModelState {
  if (errored || status?.reachable === false) return "offline";
  if (status?.reachable && status.model_loaded) return "online";
  if (status?.reachable) return "loading";
  return "offline";
}

function resolveLlmState(status?: ModelHealthStatus): ModelState {
  if (!status?.required && !status?.configured) return "idle";
  if (status.reachable === false) return "offline";
  if (status.reachable) return "online";
  return "offline";
}

function stateLabel(state: ModelState): string {
  if (state === "online") return "online";
  if (state === "loading") return "загрузка";
  if (state === "idle") return "не нужен";
  return "offline";
}

function formatPing(status?: ModelHealthStatus): string {
  if (status?.reachable && typeof status.ping_ms === "number") {
    return `${status.ping_ms % 1 === 0 ? status.ping_ms : status.ping_ms.toFixed(1)} ms`;
  }
  return "—";
}

function modelSlug(model?: string): string | undefined {
  if (!model) return undefined;
  return model.split("/").pop() ?? model;
}

function shortModelName(model?: string): string | undefined {
  const slug = modelSlug(model);
  if (!slug) return undefined;
  return slug.length > 20 ? `${slug.slice(0, 17)}…` : slug;
}

function displayModelName(model?: string, compact?: boolean): string | undefined {
  const slug = modelSlug(model);
  if (!slug) return undefined;
  return compact ? shortModelName(slug) : slug;
}

function formatTargetHost(status?: ModelHealthStatus): string | undefined {
  if (status?.target) return status.target;
  if (!status?.base_url) return undefined;
  try {
    const url = new URL(status.base_url);
    return url.port ? `${url.hostname}:${url.port}` : url.hostname;
  } catch {
    return status.base_url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  }
}

function isInternalHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  return (
    normalized === "host.docker.internal" ||
    normalized === "localhost" ||
    normalized === "127.0.0.1" ||
    normalized.endsWith(".docker.internal")
  );
}

function isInternalTarget(target?: string): boolean {
  if (!target) return false;
  const host = target.split(":")[0]?.trim() ?? target;
  return isInternalHost(host);
}

function formatVlmBackendLabel(backend?: string): string {
  const normalized = (backend ?? "").trim().toLowerCase();
  if (normalized === "openrouter") return "OpenRouter";
  if (normalized === "lmstudio") return "lmstudio";
  if (normalized === "local" || normalized === "gemma") return "локально";
  return backend?.trim() || "облако";
}

function isLocalVlmInference(vlm?: ModelHealthStatus): boolean {
  if (vlm?.location === "remote") return false;
  if (vlm?.location === "local") return true;
  const backend = (vlm?.backend ?? "").toLowerCase();
  return backend === "local" || backend === "gemma";
}

function llmLocationLabel(llm?: ModelHealthStatus): string {
  if (llm?.location === "lan") return "локальная сеть";
  if (llm?.location === "local") return "локально";
  if (llm?.backend === "openrouter") return "OpenRouter";
  return "удалённо";
}

function buildVlmTitle(vlm?: ModelHealthStatus, errored?: boolean): string | undefined {
  if (errored) return "Не удалось получить статус VLM";
  const backend = (vlm?.backend ?? "").trim().toLowerCase();
  const inference = vlm?.inference_target;
  const userInference = inference && !isInternalTarget(inference) ? inference : undefined;
  const local = isLocalVlmInference(vlm);
  const lines = local
    ? [
        "VLM (локально) — Gemma на этом же компьютере",
        "Ping: задержка бэкенда до локальной модели",
      ]
    : backend === "lmstudio"
      ? [
          "VLM (lmstudio) — vision через LM Studio",
          ...(userInference ? [`LM Studio: ${userInference}`] : []),
          "Ping: задержка бэкенда до VLM",
        ]
      : backend === "openrouter"
        ? [
            "VLM (OpenRouter) — inference в облаке",
            "Ping: задержка бэкенда до VLM-шлюза",
          ]
        : [
            `VLM (${formatVlmBackendLabel(vlm?.backend)})`,
            ...(userInference ? [`Inference: ${userInference}`] : []),
            "Ping: задержка бэкенда до VLM",
          ];
  if (vlm?.model) lines.push(`Модель: ${vlm.model}`);
  if (vlm?.error) lines.push(`Ошибка: ${vlm.error}`);
  return lines.join("\n");
}

function buildLlmTitle(llm?: ModelHealthStatus): string | undefined {
  if (!llm) {
    return "LLM — модель для второй стадии пайплайна";
  }
  const target = formatTargetHost(llm) ?? "—";
  const loc = llmLocationLabel(llm);
  const pingHint =
    llm.location === "lan"
      ? "Ping: задержка бэкенда до LM Studio в локальной сети"
      : llm.location === "local"
        ? "Ping: задержка бэкенда до локального LLM (LM Studio)"
        : "Ping: задержка бэкенда до облачного LLM (OpenRouter)";
  const lines = [`LLM (${loc}) — ${target}`, pingHint];
  if (llm.model) lines.push(`Модель: ${llm.model}`);
  if (llm.backend) lines.push(`Бэкенд: ${llm.backend}`);
  if (llm.error) lines.push(`Ошибка: ${llm.error}`);
  return lines.join("\n");
}

function vlmKindLabel(): string {
  return "VLM";
}

function llmKindLabel(): string {
  return "LLM";
}

function StatusChip({
  kindLabel,
  modelName,
  compact = false,
  state,
  ping,
  title
}: {
  kindLabel: string;
  modelName?: string;
  compact?: boolean;
  state: ModelState;
  ping: string;
  title?: string;
}) {
  const labelModel = displayModelName(modelName, compact);
  return (
    <div className={styles.chip} title={title}>
      <span className={`${styles.dot} ${styles[state]}`} aria-hidden="true" />
      <span className={styles.label}>
        {kindLabel}
        {labelModel ? `: ${labelModel}` : ""} · {stateLabel(state)}
      </span>
      <span className={styles.ping}>{ping}</span>
    </div>
  );
}

export default function ModelStatusIndicator({
  compact = false,
  stacked = false,
}: {
  compact?: boolean;
  stacked?: boolean;
}) {
  const health = useQuery({
    queryKey: ["eskd-model-health"],
    queryFn: fetchHealth,
    refetchInterval: 20_000,
    retry: 1
  });

  const vlm = health.data?.vlm ?? health.data?.model;
  const llm = health.data?.llm;
  const vlmState = resolveVlmState(vlm, health.isError);
  const llmState = resolveLlmState(llm);
  return (
    <div
      className={`${styles.root}${compact ? ` ${styles.compact}` : ""}${stacked ? ` ${styles.stacked}` : ""}`}
      aria-live="polite"
    >
      <StatusChip
        kindLabel={vlmKindLabel()}
        modelName={vlm?.model}
        compact={compact}
        state={vlmState}
        ping={formatPing(vlm)}
        title={buildVlmTitle(vlm, health.isError)}
      />
      <StatusChip
        kindLabel={llmKindLabel()}
        modelName={llm?.model}
        compact={compact}
        state={llmState}
        ping={formatPing(llm)}
        title={buildLlmTitle(llm)}
      />
    </div>
  );
}
