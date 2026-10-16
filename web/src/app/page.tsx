"use client";

import { useState } from "react";
import { AccountPicker } from "@/components/AccountPicker";
import { Chat } from "@/components/Chat";
import { api, type PersonaKey, type Session } from "@/lib/api";
import { persona } from "@/lib/personas";

type Active = { key: PersonaKey; session: Session; conversationId: string };

export default function Home() {
  const [active, setActive] = useState<Active | null>(null);
  const [signingIn, setSigningIn] = useState<PersonaKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function signIn(key: PersonaKey) {
    setSigningIn(key);
    setError(null);
    try {
      const session = await api.signIn(key);
      const { conversation_id } = await api.startConversation(session.token);
      setActive({ key, session, conversationId: conversation_id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
    } finally {
      setSigningIn(null);
    }
  }

  if (!active) {
    return <AccountPicker busy={signingIn} error={error} onPick={signIn} />;
  }
  return (
    <Chat
      key={active.conversationId}
      persona={persona(active.key)}
      session={active.session}
      conversationId={active.conversationId}
      onSwitch={() => setActive(null)}
      onExpired={() => {
        setActive(null);
        setError("Your session expired. Pick a customer to start a new one.");
      }}
    />
  );
}
