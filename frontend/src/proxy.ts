import { NextRequest, NextResponse } from "next/server";
import { neonStagingEnabled } from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";

/** SDK session refresh owns cookie writes before the Server Component renders. */
export async function proxy(request: NextRequest) {
  if (!neonStagingEnabled(process.env)) return NextResponse.next();
  return stagingAuth().middleware({ loginUrl: "/staging/login" })(request);
}

export const config = { matcher: ["/staging"] };
