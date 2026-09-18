/**
 * Route-segment loading UI for the control center.
 *
 * Per the Next.js 16 loading convention this wraps page segments (not the
 * layout in this same folder), providing an instant, non-interactive skeleton
 * while server rendering resolves.
 */
export default function Loading() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <div className="mx-auto max-w-[1450px] px-6 pt-[112px] pb-16 lg:px-10">
          <div className="animate-pulse space-y-8">
            <div className="space-y-3">
              <div className="h-3 w-40 rounded-full bg-slate-200" />
              <div className="h-9 w-72 rounded-2xl bg-slate-200" />
              <div className="h-4 w-96 max-w-full rounded-full bg-slate-100" />
            </div>

            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, index) => (
                <div
                  key={index}
                  className="rounded-2xl border border-slate-200 bg-white p-6"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-11 w-11 rounded-2xl bg-slate-100" />
                    <div className="space-y-2">
                      <div className="h-3.5 w-28 rounded-full bg-slate-200" />
                      <div className="h-3 w-36 rounded-full bg-slate-100" />
                    </div>
                  </div>
                  <div className="mt-5 h-3.5 w-32 rounded-full bg-slate-100" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
