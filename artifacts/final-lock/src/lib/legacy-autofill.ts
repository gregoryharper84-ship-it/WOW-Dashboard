export function legacyAutofillUrl(input: { player: string; sport: string; prop: string; line: string; side: string }) {
  if (!input.player.trim() || !input.sport.trim() || !input.prop.trim() || !input.line.trim() || !Number.isFinite(Number(input.line))) {
    throw new Error("Enter Player / Market plus Sport and Exact line in the form before using the legacy L10 lookup.");
  }
  const side = input.side.toUpperCase();
  if (!["OVER", "UNDER", "MORE", "LESS"].includes(side)) throw new Error("Choose an explicit Over or Under side.");
  const params = new URLSearchParams({ player: input.player.trim(), sport: input.sport.trim(), prop: input.prop.trim(), line: input.line, direction: ["UNDER", "LESS"].includes(side) ? "LESS" : "MORE" });
  return `/api/wow/l10/v2?${params}`;
}
