"use client";

import {
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Mail,
  Phone,
  RefreshCw,
  Search,
  TriangleAlert,
  Users,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useState,
} from "react";

import { useAuthorization } from "@/lib/authorization/context";
import {
  authorizationFeedback,
  planClientAuthorizationResponse,
} from "@/lib/authorization/helpers";
import { deriveCustomerExperience } from "@/lib/customer/customer";

const PAGE_SIZE = 20;

type Customer = {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  organization_id: number;
  external_id: string | null;
  created_at: string;
};

type CustomerListResponse = {
  items: Customer[];
  total: number;
  offset: number;
  limit: number;
};

const EMPTY_LIST: CustomerListResponse = {
  items: [],
  total: 0,
  offset: 0,
  limit: PAGE_SIZE,
};

export default function CustomersPage() {
  const { can, refresh } = useAuthorization();
  const { canWrite } = deriveCustomerExperience(can);

  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<CustomerListResponse>(EMPTY_LIST);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search);
      setOffset(0);
    }, 300);

    return () => {
      window.clearTimeout(timer);
    };
  }, [search]);

  const loadCustomers = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const params = new URLSearchParams();
      const trimmed = query.trim();
      if (trimmed) {
        params.set("search", trimmed);
      }
      params.set("offset", String(offset));
      params.set("limit", String(PAGE_SIZE));

      const response = await fetch(
        `/api/backend/customers?${params.toString()}`,
        { cache: "no-store" },
      );

      const body = await response
        .json()
        .catch(() => null);

      if (!response.ok) {
        const plan = planClientAuthorizationResponse(
          response.status,
          body?.detail,
        );
        const feedback = authorizationFeedback(plan);

        if (feedback) {
          setError(feedback);
          refresh();
          return;
        }

        throw new Error(
          body?.detail ?? `Customer API returned ${response.status}`,
        );
      }

      setData(body as CustomerListResponse);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load customers.",
      );
    } finally {
      setLoading(false);
    }
  }, [query, offset, refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadCustomers();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadCustomers]);

  const totalPages = Math.max(
    1,
    Math.ceil(data.total / PAGE_SIZE),
  );
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const hasPrevious = offset > 0;
  const hasNext = offset + PAGE_SIZE < data.total;

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Customers
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Customer 360 workspace
              </p>
            </div>

            <div className="flex items-center gap-3">
              {!canWrite && (
                <span className="hidden rounded-full border border-slate-200 bg-white/70 px-3 py-1.5 text-[11px] text-slate-500 sm:inline-flex">
                  Read-only access
                </span>
              )}

              <button
                type="button"
                onClick={() => void loadCustomers()}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh customers"
              >
                <RefreshCw
                  className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                  Customer Operations
                </p>

                <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
                  Customer 360
                </h1>

                <p className="mt-3 max-w-xl text-sm leading-7 text-slate-500">
                  Search the customer directory, then open a
                  profile to review service context, linked
                  tickets and unified activity.
                </p>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-4 py-2 text-[11px] text-slate-500 shadow-sm">
                <Users className="h-3.5 w-3.5 text-violet-500" />
                {data.total} customer
                {data.total === 1 ? "" : "s"}
              </div>
            </div>
          </section>

          {error && (
            <div className="mb-7 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          <section className="app-panel mb-6 overflow-hidden rounded-[22px]">
            <div className="border-b border-slate-200/70 p-4">
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />

                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search by name, email, phone, or external id..."
                  className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] py-3 pl-10 pr-4 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                />
              </div>
            </div>

            {loading && data.items.length === 0 ? (
              <div className="flex h-52 items-center justify-center">
                <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
              </div>
            ) : data.items.length === 0 ? (
              <div className="p-12 text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-50">
                  <Search className="h-5 w-5 text-slate-400" />
                </div>

                <p className="mt-4 text-sm font-medium text-slate-700">
                  No customers found
                </p>

                <p className="mt-1 text-xs text-slate-400">
                  Try another search term.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-slate-200/60">
                {data.items.map((customer) => (
                  <li key={customer.id}>
                    <Link
                      href={`/customers/${customer.id}`}
                      className="group flex items-center justify-between gap-4 px-5 py-4 transition hover:bg-slate-50/80"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
                            #{customer.id}
                          </span>

                          {customer.external_id && (
                            <span className="text-[10px] text-blue-500">
                              {customer.external_id}
                            </span>
                          )}
                        </div>

                        <p className="mt-1.5 truncate text-sm font-medium text-slate-900">
                          {customer.name}
                        </p>

                        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                          <span className="inline-flex items-center gap-1.5">
                            <Mail className="h-3.5 w-3.5" />
                            {customer.email}
                          </span>

                          {customer.phone && (
                            <span className="inline-flex items-center gap-1.5">
                              <Phone className="h-3.5 w-3.5" />
                              {customer.phone}
                            </span>
                          )}
                        </div>
                      </div>

                      <ChevronRight className="h-4 w-4 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex items-center justify-between border-t border-slate-200/70 px-5 py-3.5">
              <p className="text-[11px] text-slate-400">
                Page {page} of {totalPages}
              </p>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() =>
                    setOffset((current) =>
                      Math.max(0, current - PAGE_SIZE),
                    )
                  }
                  disabled={!hasPrevious || loading}
                  className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-40"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </button>

                <button
                  type="button"
                  onClick={() =>
                    setOffset((current) => current + PAGE_SIZE)
                  }
                  disabled={!hasNext || loading}
                  className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-40"
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
