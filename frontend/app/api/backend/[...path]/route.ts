/** Server-side BFF proxy — the ONLY place the API token is read.
 *
 * The browser calls same-origin `/api/backend/<path>` and never sees
 * `X-Stock-Agent-Token` or talks to the FastAPI backend directly. This route
 * handler reads the token file from disk (server-side, per request) and
 * forwards to `${BACKEND_URL}/api/<path>${search}`, adding the token header
 * on state-changing (POST) requests. No client component or bundle ever
 * imports this file's contents — it only runs on the server.
 */
import { readFile } from "fs/promises";
import path from "path";

import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";
const TOKEN_FILE = process.env.BACKEND_TOKEN_FILE || "../backend/.api_token";

async function readToken(): Promise<string | null> {
  const resolved = path.isAbsolute(TOKEN_FILE)
    ? TOKEN_FILE
    : path.resolve(process.cwd(), TOKEN_FILE);
  try {
    const raw = await readFile(resolved, "utf-8");
    const token = raw.trim();
    return token.length > 0 ? token : null;
  } catch {
    return null;
  }
}

async function forward(req: NextRequest, segments: string[]): Promise<NextResponse> {
  const targetPath = segments.join("/");
  const search = req.nextUrl.search;
  const url = `${BACKEND_URL}/api/${targetPath}${search}`;

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const init: RequestInit = { method: req.method, headers };

  if (req.method === "POST") {
    const token = await readToken();
    if (!token) {
      return NextResponse.json(
        { detail: "backend token not found — start the backend first" },
        { status: 503 }
      );
    }
    headers["X-Stock-Agent-Token"] = token;
    const bodyText = await req.text();
    init.body = bodyText.length > 0 ? bodyText : "{}";
  }

  let backendRes: Response;
  try {
    backendRes = await fetch(url, init);
  } catch (err) {
    return NextResponse.json(
      { detail: `failed to reach backend at ${BACKEND_URL}: ${(err as Error).message}` },
      { status: 502 }
    );
  }

  const text = await backendRes.text();
  const contentType = backendRes.headers.get("content-type") || "application/json";
  return new NextResponse(text, {
    status: backendRes.status,
    headers: { "content-type": contentType },
  });
}

type RouteContext = { params: { path: string[] } };

export async function GET(req: NextRequest, { params }: RouteContext): Promise<NextResponse> {
  return forward(req, params.path);
}

export async function POST(req: NextRequest, { params }: RouteContext): Promise<NextResponse> {
  return forward(req, params.path);
}
