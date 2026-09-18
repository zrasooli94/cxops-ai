"use client";

import { useEffect, useRef, useActionState } from "react";
import { ShieldCheck, Bot, BrainCircuit, Ticket, Workflow, Activity } from "lucide-react";
import { signIn } from "@/lib/auth/sign-in";
import {
  postSignInNavigation,
  type PostSignInResult,
} from "@/lib/auth/sign-in-result";

const initialState: PostSignInResult = { ok: false, error: "" };

function signInAction(
  prevState: PostSignInResult,
  formData: FormData,
): Promise<PostSignInResult> {
  return signIn(formData);
}

export function LoginForm() {
  const [state, formAction, isPending] = useActionState<
    PostSignInResult,
    FormData
  >(signInAction, initialState);

  const navigatedRef = useRef(false);

  useEffect(() => {
    const navigation = postSignInNavigation(state);
    if (!navigation.shouldNavigate) return;
    if (navigatedRef.current) return;
    navigatedRef.current = true;
    window.location.replace(navigation.destination);
  }, [state]);

  const error = !state.ok && state.error ? state.error : null;
  const navigating = postSignInNavigation(state).shouldNavigate;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 flex">
      <div className="hidden lg:flex lg:w-1/2 bg-gradient-to-br from-violet-600 via-violet-700 to-indigo-800 p-16 flex-col justify-between relative overflow-hidden">
        <div>
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 grid-cols-3 gap-[3px]">
              {Array.from({ length: 9 }).map((_, index) => (
                <span
                  key={index}
                  className={`rounded-full ${
                    index % 2 === 0
                      ? "bg-white"
                      : "bg-blue-200"
                  }`}
                />
              ))}
            </div>
            <div>
              <p className="text-[15px] font-semibold tracking-[-0.03em] text-white">
                CXOps AI
              </p>
            </div>
          </div>
        </div>

        <div className="max-w-md">
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-200 mb-4">
            AI-Powered Customer Service Operations
          </p>
          <h1 className="text-4xl font-light tracking-[-0.05em] text-white mb-6">
            Control Center
          </h1>
          <p className="text-lg leading-7 text-violet-100 mb-10">
            Sign in to access the CXOps AI platform — support tickets, RAG-assisted knowledge retrieval, AI agent decisions, risk controls, human approvals, execution audit trails, and operational observability.
          </p>

          <div className="grid gap-4 md:grid-cols-3">
            <FeatureCard icon={Ticket} title="Tickets" desc="Customer support workspace" />
            <FeatureCard icon={Bot} title="AI Agent" desc="LangGraph decision pipeline" />
            <FeatureCard icon={ShieldCheck} title="Approvals" desc="Human-in-the-loop safety" />
            <FeatureCard icon={BrainCircuit} title="Knowledge" desc="RAG-powered policy retrieval" />
            <FeatureCard icon={Workflow} title="Runs" desc="Persistent audit trail" />
            <FeatureCard icon={Activity} title="Observability" desc="Production telemetry" />
          </div>
        </div>

        <div className="text-xs text-violet-200/60">
          CXOps AI &copy; 2025 &mdash; Demonstration Platform
        </div>
      </div>

      <div className="w-full lg:w-1/2 flex items-center justify-center p-8 lg:p-16">
        <div className="w-full max-w-md">
          <div className="lg:hidden mb-8 px-8">
            <div className="flex items-center gap-3">
              <div className="grid h-9 w-9 grid-cols-3 gap-[3px]">
                {Array.from({ length: 9 }).map((_, index) => (
                  <span
                    key={index}
                    className={`rounded-full ${
                      index % 2 === 0
                        ? "bg-[#7357ff]"
                        : "bg-[#42a5ff]"
                    }`}
                  />
                ))}
              </div>
              <p className="text-[15px] font-semibold tracking-[-0.03em] text-slate-950">
                CXOps AI
              </p>
            </div>
          </div>

          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-600 mb-2">
            Control Center
          </p>
          <h1 className="text-3xl font-light tracking-[-0.04em] text-slate-950 mb-2">
            Welcome back
          </h1>
          <p className="text-slate-500 mb-8">
            Sign in to continue to the CXOps AI platform
          </p>

          <form action={formAction} className="space-y-6">
            {error && (
              <div className="rounded-xl border border-rose-200 bg-rose-50/80 p-4 text-sm text-rose-700">
                {error}
              </div>
            )}
            <div>
              <label htmlFor="email" className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400 block mb-2">
                Email
              </label>
              <input
                type="email"
                id="email"
                name="email"
                autoComplete="email"
                required
                placeholder="you@company.com"
                disabled={isPending || navigating}
                className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50 disabled:opacity-50"
              />
            </div>

            <div>
              <label htmlFor="password" className="text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400 block mb-2">
                Password
              </label>
              <input
                type="password"
                id="password"
                name="password"
                autoComplete="current-password"
                required
                placeholder="Enter your password"
                disabled={isPending || navigating}
                className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50 disabled:opacity-50"
              />
            </div>

            <button
              type="submit"
              disabled={isPending || navigating}
              className="w-full flex items-center justify-center gap-2 rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isPending ? (
                <>
                  <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Signing in...
                </>
              ) : navigating ? (
                "Redirecting..."
              ) : (
                <>
                  <ShieldCheck className="h-4 w-4" />
                  Sign in
                </>
              )}
            </button>
          </form>

          <p className="text-center text-xs text-slate-400">
            Demonstration platform &mdash; uses Nhost Auth with RS256 JWT
          </p>
        </div>
      </div>
    </div>
  );
}

function FeatureCard({ icon: Icon, title, desc }: { icon: React.ComponentType<{ className?: string }>; title: string; desc: string }) {
  return (
    <div className="rounded-2xl border border-white/20 bg-white/5 p-4 backdrop-blur-sm">
      <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/10 text-white">
        <Icon className="h-5 w-5" />
      </div>
      <p className="mt-3 font-medium text-white text-sm">{title}</p>
      <p className="mt-1 text-xs text-violet-100/70">{desc}</p>
    </div>
  );
}