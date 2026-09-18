import { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireControlCenterAuth } from "@/lib/auth/control-center";
import { AuthorizationLoadError } from "@/lib/authorization/helpers";
import { getMyOrganizationsReadOnly } from "@/lib/tenant/backend-client";
import OrganizationSelectionForm from "./OrganizationSelectionForm";

export const metadata: Metadata = {
  title: "Select organization | CXOps AI",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function SelectOrganizationPage() {
  await requireControlCenterAuth();

  let memberships;
  try {
    memberships = await getMyOrganizationsReadOnly();
  } catch (error) {
    // Only an authentication failure routes through the recovery handler; any
    // other backend failure falls through to the normal error path (as before).
    if (
      error instanceof AuthorizationLoadError &&
      error.reason === "authentication"
    ) {
      redirect("/api/auth/session");
    }
    throw error;
  }

  if (memberships.length === 0) {
    redirect("/no-organization");
  }

  if (memberships.length === 1) {
    // Persisting the org cookie for the sole membership is a cookie WRITE and
    // cannot happen during Server Component rendering; the landing Route
    // Handler owns it and redirects on to /dashboard.
    redirect("/api/auth/landing");
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="mb-8 flex items-center justify-center gap-3">
          <div className="grid h-9 w-9 grid-cols-3 gap-[3px]">
            {Array.from({ length: 9 }).map((_, index) => (
              <span
                key={index}
                className={`rounded-full ${
                  index % 2 === 0 ? "bg-[#7357ff]" : "bg-[#42a5ff]"
                }`}
              />
            ))}
          </div>
          <p className="text-[15px] font-semibold tracking-[-0.03em] text-slate-950">
            CXOps AI
          </p>
        </div>

        <OrganizationSelectionForm memberships={memberships} />
      </div>
    </main>
  );
}
