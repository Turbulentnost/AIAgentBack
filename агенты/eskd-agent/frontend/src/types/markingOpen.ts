export type MarkingOpenIntent =
  | { type: "document"; documentId: string }
  | { type: "checkRun"; checkRunId: string; filename: string };
