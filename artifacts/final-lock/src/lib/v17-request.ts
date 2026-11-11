export interface PickIdentity {
  row_key: string;
  event_id: string;
  event_start_time: string;
  sport: string;
  player: string;
  stat_type: string;
  line: number;
  direction: "MORE" | "LESS";
  source_type: "NORMALIZED";
  platform?: string;
}

export function canonicalRequest(requestId: string, input: Omit<PickIdentity, "source_type">) {
  if (!requestId.trim() || !input.row_key.trim()) throw new Error("Request ID and row key are required for receipt recovery.");
  for (const field of ["event_id", "event_start_time", "sport", "player", "stat_type"] as const) {
    if (!input[field].trim()) throw new Error(`${field} is required. Use the exact event and market identity.`);
  }
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(input.event_start_time) || !Number.isFinite(Date.parse(input.event_start_time))) {
    throw new Error("Event start must be a valid ISO timestamp with a timezone, such as 2026-11-07T20:00:00Z.");
  }
  if (!Number.isFinite(input.line)) throw new Error("Line must be a finite number.");
  if (!["MORE", "LESS"].includes(input.direction)) throw new Error("Choose an explicit MORE or LESS direction.");
  return { request_id: requestId, response_mode: "COMPACT" as const, rows: [{ ...input, source_type: "NORMALIZED" as const }] };
}

export function receiptRecovery(request: ReturnType<typeof canonicalRequest>) {
  return {
    request_id: request.request_id,
    rows: request.rows.map(({ row_key, event_id, sport, player, stat_type, line, direction }) => ({ row_key, event_id, sport, player, stat_type, line, direction })),
  };
}

export function publicationText(row: Record<string, unknown>): string {
  if (row.probability_publishable === true) return "Publishable: true";
  if (row.probability_publishable === false) return "Publishable: false";
  return "Publication status not provided";
}

export function scoreCompletionUnknown(receipt: Record<string, unknown>): boolean {
  return receipt.completion_unknown === true;
}
