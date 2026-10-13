import { SiteFooter, SiteHeader } from "@/components/marketing/site";

// Public marketing site (Phase 1Q). Shares one header and footer; every page
// in this group is a Server Component except the lead form.
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-white text-slate-950">
      <SiteHeader />
      <main id="main" className="flex-1">
        {children}
      </main>
      <SiteFooter />
    </div>
  );
}
