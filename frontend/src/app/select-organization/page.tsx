import { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireControlCenterAuth } from "@/lib/auth/control-center";
import { getMyOrganizations } from "@/lib/tenant/backend-client";
import { selectOrganization } from "@/lib/tenant/actions";
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
  const memberships = await getMyOrganizations();

  if (memberships.length === 0) {
    redirect("/no-organization");
  }

  if (memberships.length === 1) {
    // Auto-select the sole membership and proceed to the dashboard.
    await selectOrganization(memberships[0].id);
    redirect("/dashboard");
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
