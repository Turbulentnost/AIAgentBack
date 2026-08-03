import type { HealthResponse, ModelHealthStatus } from "@/types/eskd";

export function getVlmStatus(health?: HealthResponse): ModelHealthStatus | undefined {
  return health?.vlm ?? health?.model;
}

export function isVlmReady(health?: HealthResponse): boolean {
  const vlm = getVlmStatus(health);
  return Boolean(vlm?.reachable && vlm?.model_loaded);
}

export function isVlmLoading(health?: HealthResponse): boolean {
  const vlm = getVlmStatus(health);
  return Boolean(vlm?.reachable && !vlm?.model_loaded);
}
