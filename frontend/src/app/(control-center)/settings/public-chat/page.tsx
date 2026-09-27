"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  KeyRound,
  LoaderCircle,
  Settings2,
  ShieldCheck,
  ToggleLeft,
  ToggleRight,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";
import {
  fetchBusinessIntegrations,
  fetchPublicChatSettings,
  PublicChatApiError,
  rotateWidgetKey,
  setBusinessIntegration,
  updatePublicChatSettings,
} from "@/lib/tenant-config/client";
import type {
  BusinessIntegrationState,
  PublicChatSettings,
} from "@/lib/tenant-config/types";

function originsToText(origins: string[]): string {
  return origins.join("\n");
}

function textToOrigins(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

function cardClass() {
  return "rounded-2xl border border-slate-200/70 bg-white/80 p-6 shadow-sm backdrop-blur-2xl";
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      {hint ? (
        <span className="mt-0.5 block text-xs text-slate-500">{hint}</span>
      ) : null}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

const inputClass =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-slate-400 focus:ring-2 focus:ring-slate-200";

export default function SettingsPublicChatPage() {
  const { can } = useAuthorization();
  const canManage = can(CAPABILITIES.INTEGRATION_MANAGE);

  const [settings, setSettings] = useState<PublicChatSettings | null>(null);
  const [integrations, setIntegrations] = useState<BusinessIntegrationState[]>(
    [],
  );

  const [displayName, setDisplayName] = useState("");
  const [welcomeMessage, setWelcomeMessage] = useState("");
  const [originsText, setOriginsText] = useState("");
  const [themeToken, setThemeToken] = useState("");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [issuedKey, setIssuedKey] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextSettings, nextIntegrations] = await Promise.all([
        fetchPublicChatSettings(),
        fetchBusinessIntegrations(),
      ]);
      setSettings(nextSettings);
      setDisplayName(nextSettings.displayName);
      setWelcomeMessage(nextSettings.welcomeMessage);
      setOriginsText(originsToText(nextSettings.allowedOrigins));
      setThemeToken(nextSettings.themeToken ?? "");
      setIntegrations(nextIntegrations);
    } catch (cause) {
      setError(
        cause instanceof PublicChatApiError
          ? cause.message
          : "Could not load tenant configuration.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!canManage) return;
    // The initial load is a fetch, not derived state: the effect subscribes to
    // an external system and writes the result, which is the sanctioned use.
    /* eslint-disable react-hooks/set-state-in-effect */
    void load();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [canManage, load]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await updatePublicChatSettings({
        displayName,
        welcomeMessage,
        allowedOrigins: textToOrigins(originsText),
        // An empty field means "remove the stored token", not "leave it alone":
        // the API treats null as an explicit clear, while undefined would be
        // dropped by JSON.stringify and silently keep the old value.
        themeToken: themeToken.trim() === "" ? null : themeToken.trim(),
      });
      setSettings(next);
      setNotice("Saved. The widget picks these values up on its next load.");
    } catch (cause) {
      setError(
        cause instanceof PublicChatApiError
          ? cause.message
          : "Could not save the configuration.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled() {
    if (!settings) return;
    const next = !settings.enabled;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const updated = await updatePublicChatSettings({ enabled: next });
      setSettings(updated);
      setNotice(
        next
          ? "Widget enabled. New sessions are accepted again."
          : "Widget disabled. New sessions are refused; existing history is kept.",
      );
    } catch (cause) {
      setError(
        cause instanceof PublicChatApiError
          ? cause.message
          : "Could not change the widget state.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function rotate() {
    setRotating(true);
    setError("");
    setNotice("");
    setIssuedKey("");
    try {
      const rotation = await rotateWidgetKey();
      // Shown exactly once. It is never written to storage or a cookie.
      setIssuedKey(rotation.publicWidgetKey);
      setNotice(rotation.warning);
      setSettings((current) =>
        current ? { ...current, hasWidgetKey: true } : current,
      );
    } catch (cause) {
      setError(
        cause instanceof PublicChatApiError
          ? cause.message
          : "Could not rotate the widget key.",
      );
    } finally {
      setRotating(false);
    }
  }

  async function toggleIntegration(
    provider: string,
    enabled: boolean,
  ) {
    setError("");
    setNotice("");
    try {
      const result = await setBusinessIntegration(provider, enabled);
      setIntegrations((current) =>
        current.map((entry) =>
          entry.provider === result.provider
            ? { ...entry, enabled: result.enabled }
            : entry,
        ),
      );
    } catch (cause) {
      setError(
        cause instanceof PublicChatApiError
          ? cause.message
          : "Could not change the integration.",
      );
      await load();
    }
  }

  async function copyKey() {
    try {
      await navigator.clipboard.writeText(issuedKey);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy to the clipboard. Select the key manually.");
    }
  }

  if (!canManage) {
    return (
      <div className="p-8">
        <div className={cardClass()}>
          <div className="flex items-center gap-2 text-slate-900">
            <ShieldCheck className="h-5 w-5" />
            <h1 className="text-lg font-semibold">Settings</h1>
          </div>
          <p className="mt-3 text-sm text-slate-600">
            You do not have permission to change tenant configuration. Ask an
            administrator for the integration.manage capability.
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-slate-600">
        <LoaderCircle className="h-4 w-4 animate-spin" />
        Loading configuration…
      </div>
    );
  }

  return (
    <div className="space-y-6 p-8">
      <div className={cardClass()}>
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-slate-900">
              <Settings2 className="h-5 w-5" />
              <h1 className="text-lg font-semibold">Public chat settings</h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Branding, the exact-origin allowlist, and the widget kill switch
              for this tenant.
            </p>
          </div>
          {settings ? (
            <span
              className={
                settings.enabled
                  ? "inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700"
                  : "inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700"
              }
            >
              {settings.enabled ? (
                <ToggleRight className="h-4 w-4" />
              ) : (
                <ToggleLeft className="h-4 w-4" />
              )}
              {settings.enabled ? "Enabled" : "Disabled"}
            </span>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
      {notice && !error ? (
        <div className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{notice}</span>
        </div>
      ) : null}

      {issuedKey ? (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3">
          <p className="text-sm font-medium text-amber-900">
            New widget key — copy it now, it is not shown again
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-lg bg-white px-3 py-2 text-xs text-slate-900">
              {issuedKey}
            </code>
            <button
              type="button"
              onClick={copyKey}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-900 hover:bg-amber-100"
            >
              <Copy className="h-3.5 w-3.5" />
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="mt-2 text-xs text-amber-800">
            Anyone holding this key can open the widget. It is not a
            substitute for the origin allowlist.
          </p>
        </div>
      ) : null}

      <form className={cardClass()} onSubmit={save}>
        <h2 className="text-sm font-semibold text-slate-900">Branding</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Field
            label="Display name"
            hint="Shown in the widget header."
          >
            <input
              className={inputClass}
              value={displayName}
              maxLength={100}
              onChange={(event) => setDisplayName(event.target.value)}
              required
            />
          </Field>
          <Field
            label="Theme token"
            hint="Optional branding token, not a secret."
          >
            <input
              className={inputClass}
              value={themeToken}
              maxLength={100}
              onChange={(event) => setThemeToken(event.target.value)}
              placeholder="e.g. brand-teal"
            />
          </Field>
        </div>
        <div className="mt-4">
          <Field
            label="Welcome message"
            hint="First message a customer sees."
          >
            <textarea
              className={`${inputClass} min-h-20`}
              value={welcomeMessage}
              maxLength={500}
              onChange={(event) => setWelcomeMessage(event.target.value)}
            />
          </Field>
        </div>
        <div className="mt-4">
          <Field
            label="Allowed origins"
            hint="Exact origins only, one per line. No wildcards, paths, or trailing slashes. This is the boundary that stops another site embedding your widget."
          >
            <textarea
              className={`${inputClass} min-h-24 font-mono text-xs`}
              value={originsText}
              onChange={(event) => setOriginsText(event.target.value)}
              placeholder="https://www.example.com"
            />
          </Field>
        </div>

        <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
          <p className="text-xs font-medium text-slate-700">
            Abuse limits are set by the tenant manifest, not here
          </p>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-600 sm:grid-cols-3">
            <div>
              <dt className="text-slate-500">Max message length</dt>
              <dd className="font-medium text-slate-800">
                {settings?.maxMessageLength ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Messages / minute</dt>
              <dd className="font-medium text-slate-800">
                {settings?.maxMessagesPerMinute ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Session TTL (hours)</dt>
              <dd className="font-medium text-slate-800">
                {settings?.sessionTtlHours ?? "—"}
              </dd>
            </div>
          </dl>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : null}
            Save changes
          </button>
          <button
            type="button"
            onClick={toggleEnabled}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-800 disabled:opacity-60"
          >
            {settings?.enabled ? (
              <ToggleRight className="h-4 w-4" />
            ) : (
              <ToggleLeft className="h-4 w-4" />
            )}
            {settings?.enabled ? "Disable widget" : "Enable widget"}
          </button>
        </div>
      </form>

      <div className={cardClass()}>
        <div className="flex items-center gap-2 text-slate-900">
          <KeyRound className="h-5 w-5" />
          <h2 className="text-sm font-semibold">Widget key</h2>
        </div>
        <p className="mt-1 text-sm text-slate-600">{settings?.widgetKeyStatus}</p>
        <p className="mt-2 text-xs text-slate-500">
          Rotating issues a new key and immediately invalidates the previous
          one. Any embed still using the old key stops working at once.
        </p>
        <button
          type="button"
          onClick={rotate}
          disabled={rotating}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-800 disabled:opacity-60"
        >
          {rotating ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : null}
          Rotate key
        </button>
      </div>

      <div className={cardClass()}>
        <h2 className="text-sm font-semibold text-slate-900">
          Business integrations
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          A disabled provider exposes none of its tools to the agent. Tools
          already queued keep their stored tenant.
        </p>
        <ul className="mt-4 space-y-3">
          {integrations.length === 0 ? (
            <li className="text-sm text-slate-500">
              No business integrations are registered for this build.
            </li>
          ) : null}
          {integrations.map((integration) => (
            <li
              key={integration.provider}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3"
            >
              <div>
                <p className="text-sm font-medium text-slate-900">
                  {integration.provider}
                </p>
                <p className="text-xs text-slate-500">
                  {integration.providerMode ?? "unknown mode"}
                  {integration.toolNames.length > 0
                    ? ` · ${integration.toolNames.length} tool(s)`
                    : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() =>
                  void toggleIntegration(
                    integration.provider,
                    !integration.enabled,
                  )
                }
                className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
              >
                {integration.enabled ? (
                  <>
                    <ToggleRight className="h-4 w-4" />
                    Disable
                  </>
                ) : (
                  <>
                    <ToggleLeft className="h-4 w-4" />
                    Enable
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
