// Typed client for src/api.py. The token is the only identity the server
// accepts; nothing here sends a customer id.


export type PersonaKey = "helena" | "richard";

export type ToolCall = {
  name: string;
  args: Record<string, unknown>;
  output: string | null;
};

export type Turn = {
  reply: string;
  tool_calls: ToolCall[];
  pending_approval: string | null;
  latency_ms: number;
  messages_left: number;
};

export type Session = {
  token: string;
  customer_name: string;
  expires_at: number;
  message_limit: number;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body?: unknown, token?: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the support server. Check your connection and try again.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "The request was rejected.";
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

export const api = {
  signIn: (persona: PersonaKey) => post<Session>("/api/session", { persona }),
  startConversation: (token: string) =>
    post<{ conversation_id: string }>("/api/conversations", undefined, token),
  send: (token: string, conversationId: string, message: string) =>
    post<Turn>(`/api/conversations/${conversationId}/messages`, { message }, token),
  decide: (token: string, conversationId: string, decision: "approve" | "reject") =>
    post<Turn>(`/api/conversations/${conversationId}/decision`, { decision }, token),
};
