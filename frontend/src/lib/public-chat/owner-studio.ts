export type StudioConversationMetric = { status: string; needs_response: boolean };
export type StudioHandoffMetric = { status: string };
export type StudioMessage = { direction: string; body: string };
export type StudioKnowledgeSource = { title: string; content?: string };
export type StudioEvidence = {
  question: string;
  coverage: "missing" | "ambiguous" | "related";
  items: unknown[];
};

export async function refreshSelectedConversation<T>(
  targetConversationId: number,
  load: (conversationId: number) => Promise<T>,
  currentConversationId: () => number | null,
  apply: (messages: T, conversationId: number) => void,
) {
  const messages = await load(targetConversationId);
  if (currentConversationId() !== targetConversationId) return false;
  apply(messages, targetConversationId);
  return true;
}

const OWNER_STOP_WORDS = new Set([
  "a", "an", "and", "are", "can", "do", "for", "how", "i", "is", "it", "my", "of", "on", "or", "the", "to", "what", "when", "where", "you",
  "آیا", "از", "است", "با", "برای", "به", "در", "را", "و", "چه", "کجا", "کی", "من",
]);

function ownerTokens(value: string) {
  return new Set(value
    .normalize("NFKC")
    .replaceAll("ي", "ی")
    .replaceAll("ك", "ک")
    .replaceAll("\u200c", " ")
    .toLocaleLowerCase()
    .match(/[\p{L}\p{N}]+/gu)
    ?.filter((token) => token.length > 1 && !OWNER_STOP_WORDS.has(token)) ?? []);
}

export function buildKnowledgeGapReview(
  messages: StudioMessage[],
  sources: StudioKnowledgeSource[],
  hasHandoff: boolean,
) {
  if (!hasHandoff) return null;
  const handoffReplyIndex = messages.findIndex((message) =>
    message.direction === "outbound" && message.body.includes("needs to review this request"));
  if (handoffReplyIndex < 0) return null;
  const candidateMessages = messages.slice(0, handoffReplyIndex);
  const question = [...candidateMessages].reverse().find((message) => message.direction === "inbound")?.body.trim();
  if (!question) return null;
  const questionTokens = ownerTokens(question);
  const relevantSources = sources.map((source) => {
    const sourceTokens = ownerTokens(`${source.title} ${source.content ?? ""}`);
    return { ...source, overlap: [...questionTokens].filter((token) => sourceTokens.has(token)).length };
  }).filter((source) => source.overlap > 0)
    .sort((left, right) => right.overlap - left.overlap || left.title.localeCompare(right.title))
    .slice(0, 3);
  return { question, relevantSources };
}

export function selectCurrentEvidence<T extends StudioEvidence>(
  question: string | undefined,
  evidence: T | null,
) {
  return question && evidence?.question === question ? evidence : null;
}

export function evidenceCoverageLabel(
  coverage: StudioEvidence["coverage"],
  language: string,
) {
  const persian = language === "fa";
  if (coverage === "missing") return persian ? "پوشش واژگانی یافت نشد" : "No lexical coverage";
  if (coverage === "ambiguous") return persian ? "چند هم‌پوشانی واژگانی" : "Multiple lexical matches";
  return persian ? "هم‌پوشانی واژگانی یافت شد" : "Lexical match found";
}

export function summarizeOwnerStudio(
  conversations: StudioConversationMetric[],
  handoffs: StudioHandoffMetric[],
) {
  return {
    conversations: conversations.length,
    awaitingReply: conversations.filter((item) => item.needs_response).length,
    activeHandoffs: handoffs.filter((item) => item.status !== "closed").length,
    resolved: conversations.filter((item) => ["closed", "resolved", "solved"].includes(item.status)).length,
  };
}
