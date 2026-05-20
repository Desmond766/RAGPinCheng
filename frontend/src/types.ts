export type Source = {
  parent_id: string;
  doc_title: string;
  section_path: string;
  category: string;
  score: number;
  text: string;
  doc_type: string;
  start_time: string | null;
};

export type PrepData = {
  search_query: string;
  rewrite_applied: boolean;
  history_chars: number;
  budget: number;
  fresh_count: number;
  final_count: number;
  used_sources: Source[];
  no_source_fallback: boolean;
};

export type DoneData = {
  answer_text: string;
  timings: Record<string, number>;
  sources: Source[];
  history_chars: number;
  budget: number;
};

export type ChatEvent =
  | { type: "prep"; data: PrepData }
  | { type: "token"; data: { text: string } }
  | { type: "done"; data: DoneData }
  | { type: "error"; data: { message: string } };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  sources?: Source[];
  prep?: PrepData;
  done?: DoneData;
  streaming?: boolean;
  error?: string;
};

export type ApiConfig = {
  embed_model: string;
  reranker_model: string;
  rerank_enabled: boolean;
  llm_model: string;
  collection: string;
};

export type Health = {
  status: string;
  children: number;
  parents: number;
};
