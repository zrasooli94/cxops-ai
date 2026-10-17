import type { StagingTicket } from "@/lib/staging/operations-contract";
import { performSyntheticTicketAction } from "./actions";

/** Server component: controls appear only with a current, backend-verified
 * staging AGENT membership. The backend independently re-checks every action.
 */
export default function StagingTicketActions({
  tickets, currentSubject, enabled,
}: {
  tickets: readonly StagingTicket[];
  currentSubject: string;
  enabled: boolean;
}) {
  if (!enabled) return null;
  return <section aria-label="Synthetic ticket actions" className="mt-7 rounded-2xl border border-violet-200 bg-white p-6 shadow-sm">
    <p className="text-xs font-bold uppercase tracking-widest text-violet-700">Agent workflow · Synthetic records only</p>
    <h2 className="mt-2 text-lg font-semibold text-slate-900">Assign and resolve test tickets</h2>
    <p className="mt-2 text-sm text-slate-600">These actions update only the fictional Alpha staging database. No message, webhook, AI request or notification is sent.</p>
    <div className="mt-5 space-y-3">
      {tickets.map(ticket => <div key={ticket.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-100 p-4">
        <div>
          <p className="text-sm font-medium text-slate-900">{ticket.subject}</p>
          <p className="mt-1 text-xs text-slate-500">{ticket.external_id} · {ticket.status} · {ticket.assigned_subject === currentSubject ? "Assigned to you" : ticket.assigned_subject ? "Assigned to another tester" : "Unassigned"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {["new", "open", "pending"].includes(ticket.status) && ticket.assigned_subject === null ? (
            <form action={performSyntheticTicketAction}>
              <input type="hidden" name="ticket_id" value={ticket.id} />
              <input type="hidden" name="operation" value="claim" />
              <input type="hidden" name="expected_version" value={ticket.updated_at} />
              <button className="rounded-lg bg-violet-700 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-800" type="submit">Claim ticket</button>
            </form>
          ) : ["new", "open", "pending"].includes(ticket.status) && ticket.assigned_subject === currentSubject ? (
            <>
              <form action={performSyntheticTicketAction}>
                <input type="hidden" name="ticket_id" value={ticket.id} />
                <input type="hidden" name="operation" value="resolve" />
                <input type="hidden" name="expected_version" value={ticket.updated_at} />
                <button className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800" type="submit">Resolve</button>
              </form>
              <form action={performSyntheticTicketAction}>
                <input type="hidden" name="ticket_id" value={ticket.id} />
                <input type="hidden" name="operation" value="release" />
                <input type="hidden" name="expected_version" value={ticket.updated_at} />
                <button className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50" type="submit">Release</button>
              </form>
            </>
          ) : <span className="text-xs font-medium text-slate-500">{ticket.status === "solved" || ticket.status === "closed" ? "Resolved" : "Not available"}</span>}
        </div>
      </div>)}
    </div>
  </section>;
}
