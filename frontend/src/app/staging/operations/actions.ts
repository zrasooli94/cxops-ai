"use server";

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import {
  stagingIdentity,
} from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";
import { stagingServerActionOriginAllowed } from "@/lib/staging/server-action-security";
import { runSyntheticTicketAction } from "@/lib/staging/ticket-action-handler";

/** Server-only form action. Never accept an arbitrary tenant, URL or bearer token. */
export async function performSyntheticTicketAction(form: FormData): Promise<void> {
  const requestHeaders = await headers();
  const originAllowed = stagingServerActionOriginAllowed(
    requestHeaders.get("origin"), requestHeaders.get("host"),
  );
  const outcome = originAllowed ? await runSyntheticTicketAction({
    env: process.env,
    form,
    readIdentity: allowed => stagingIdentity(stagingAuth(), allowed),
    fetcher: fetch,
  }) : "blocked";
  if (outcome === "completed") revalidatePath("/staging/operations");
  redirect(outcome === "completed" ? "/staging/operations" : `/staging/operations?workflow=${outcome}`);
}
