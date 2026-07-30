import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";

interface BrowserSessionRequest {
  enabled: boolean;
  localAppData?: string;
  method?: string;
  url?: string;
  host?: string;
  origin?: string;
}

type BrowserSessionResponse =
  | { status: 200; body: { port: number; token: string } }
  | { status: 403 | 404 | 405 | 503 };

function loopbackHost(value: string | undefined): boolean {
  if (!value) return false;
  try {
    const hostname = new URL(`http://${value}`).hostname;
    return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "[::1]";
  } catch {
    return false;
  }
}

function validSession(value: unknown): value is { port: number; token: string } {
  if (!value || typeof value !== "object") return false;
  const session = value as { port?: unknown; token?: unknown };
  return Number.isInteger(session.port)
    && Number(session.port) > 0
    && Number(session.port) <= 65_535
    && typeof session.token === "string"
    && session.token.length > 0;
}

export async function browserSessionResponse(request: BrowserSessionRequest): Promise<BrowserSessionResponse> {
  if (!request.enabled) return { status: 404 };
  if (request.method !== "GET") return { status: 405 };
  if (!loopbackHost(request.host)) return { status: 403 };
  if (request.origin) {
    try {
      if (new URL(request.origin).host !== request.host) return { status: 403 };
    } catch {
      return { status: 403 };
    }
  }
  if (!request.localAppData) return { status: 503 };

  try {
    const value: unknown = JSON.parse(
      await readFile(join(request.localAppData, "Halo", "session.json"), "utf8"),
    );
    return validSession(value) ? { status: 200, body: value } : { status: 503 };
  } catch {
    return { status: 503 };
  }
}

function middleware(enabled: boolean) {
  return async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (req.url?.split("?", 1)[0] !== "/__halo/session") {
      next();
      return;
    }
    const result = await browserSessionResponse({
      enabled,
      localAppData: process.env.LOCALAPPDATA,
      method: req.method,
      url: req.url,
      host: req.headers.host,
      origin: req.headers.origin,
    });
    res.statusCode = result.status;
    res.setHeader("Cache-Control", "no-store");
    if (result.status === 200) {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(result.body));
    } else {
      res.end();
    }
  };
}

export function browserSessionPlugin(): Plugin {
  const enabled = process.env.HALO_BROWSER_DEV === "1";
  return {
    name: "halo-browser-session",
    configureServer(server) {
      server.middlewares.use(middleware(enabled));
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware(enabled));
    },
  };
}
