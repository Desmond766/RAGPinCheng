export type Source = {
  parent_id: string;
  doc_title: string;
  section_path: string;
  category: string;
  score: number;
  rrf_score: number;
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

export type ChatStage = "retrieving" | "generating" | "streaming" | "done";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  query?: string;
  sources?: Source[];
  prep?: PrepData;
  done?: DoneData;
  streaming?: boolean;
  stage?: ChatStage;
  error?: string;
};

export type AuthUser = {
  id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  csrf_token: string;
};

export type Conversation = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  turn_index: number;
};

export type ConversationState = {
  id: string;
  title: string;
  user_id: number;
  created_at: number;
  updated_at: number;
  turn_index: number;
  messages: {
    id?: number;
    role: "user" | "assistant" | "system";
    content: string;
    sources_for_ui?: Source[] | null;
    created_at?: number;
  }[];
};

export type AdminUser = {
  id: number;
  employee_id: string;
  real_name: string;
  role: "user" | "admin";
  is_active: boolean;
  created_at: number;
  last_login_at: number | null;
  conversation_count: number;
};

export type AdminConversation = {
  id: string;
  title: string;
  user_id: number;
  employee_id: string;
  real_name: string;
  created_at: number;
  updated_at: number;
  turn_index: number;
};

export type AdminStats = {
  users_total: number;
  users_active: number;
  conversations_total: number;
  conversations_7d: number;
  messages_total: number;
  messages_7d: number;
};

export type AdminFeedbackEntry = {
  ts?: string | null;
  kind?: string | null;
  rating?: string | null;
  note?: string | null;
  parent_id?: string | null;
  doc_title?: string | null;
  section_path?: string | null;
  start_time?: string | null;
  category?: string | null;
  query?: string | null;
  answer_text?: string | null;
  session_id?: string | null;
  conversation_id?: string | null;
  turn_index?: number | null;
  message_id?: string | null;
};

export type FeedbackPayload = {
  conversation_id?: string | null;
  turn_index?: number | null;
  message_id?: string | null;
  kind: "answer" | "citation";
  rating?: "up" | "down";
  note?: string;
  parent_id?: string;
  doc_title?: string;
  section_path?: string;
  start_time?: string | null;
  category?: string;
  query?: string;
  answer_text?: string;
};

export type ApiConfig = {
  embed_model: string;
  reranker_model: string;
  rerank_enabled: boolean;
  llm_model: string;
  llm_rewrite_model: string;
  collection: string;
};

export type Health = {
  status: string;
  children: number;
  parents: number;
};

export type LlmModelHealth = {
  model: string;
  role: "generation" | "rewrite" | string;
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
};

export type LlmHealth = {
  ok: boolean;
  key_present: boolean;
  key_masked: string;
  base_url: string;
  checked_at: number;
  cached: boolean;
  models: LlmModelHealth[];
};
