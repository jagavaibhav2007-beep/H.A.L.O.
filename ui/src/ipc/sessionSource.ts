import { invoke, isTauri } from "@tauri-apps/api/core";

export interface Session {
  port: number;
  token: string;
}

export class SessionDiscoveryError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
    this.name = "SessionDiscoveryError";
  }
}

function validSession(value: unknown): value is Session {
  if (!value || typeof value !== "object") return false;
  const session = value as Partial<Session>;
  return Number.isInteger(session.port)
    && session.port! > 0
    && session.port! <= 65_535
    && typeof session.token === "string"
    && session.token.length > 0;
}

export async function readSession(): Promise<Session> {
  if (isTauri()) return invoke<Session>("read_session");

  const response = await fetch("/__halo/session", { cache: "no-store" });
  if (!response.ok) {
    throw new SessionDiscoveryError(
      response.status === 404
        ? "Browser connection is disabled. Start Halo with ./dev.ps1 -Browser."
        : "Halo's Brain is not ready yet.",
      response.status !== 404,
    );
  }
  const session: unknown = await response.json();
  if (!validSession(session)) {
    throw new SessionDiscoveryError("Halo returned an invalid browser session.", true);
  }
  return session;
}
