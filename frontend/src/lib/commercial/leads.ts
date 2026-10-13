// Early-access / demo-request form (Phase 1Q).
//
// Client-side validation mirrors the backend contract (app/schemas/commercial.py)
// so visitors get immediate, specific feedback. The backend re-validates every
// field; nothing here is a security boundary.

export const LEAD_INDUSTRIES = [
  { value: "automotive", label: "Automotive sales & services" },
  { value: "local_services", label: "Local & home services" },
  { value: "retail_ecommerce", label: "Retail & e-commerce" },
  { value: "professional_services", label: "Professional services" },
  { value: "healthcare_wellness", label: "Health & wellness" },
  { value: "hospitality_travel", label: "Hospitality & travel" },
  { value: "education", label: "Education & training" },
  { value: "real_estate", label: "Real estate & property" },
  { value: "technology_saas", label: "Technology & software" },
  { value: "other", label: "Something else" },
] as const;

export type LeadIndustry = (typeof LEAD_INDUSTRIES)[number]["value"];

export interface LeadFormInput {
  companyName: string;
  contactName: string;
  email: string;
  website: string;
  industry: string;
  inquiry: string;
  consent: boolean;
  /** Honeypot. Hidden from people; must stay empty. */
  companyFax: string;
}

export type LeadField = Exclude<keyof LeadFormInput, "companyFax">;

export interface LeadValidation {
  ok: boolean;
  errors: Partial<Record<LeadField, string>>;
}

export const LEAD_LIMITS = {
  companyName: 200,
  contactName: 200,
  email: 320,
  website: 255,
  inquiry: 2000,
} as const;

// Deliberately simple: one "@", a dot in the domain, no whitespace. The server
// performs full RFC validation.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const INDUSTRY_VALUES: ReadonlySet<string> = new Set(
  LEAD_INDUSTRIES.map((item) => item.value),
);

/** Accepts "example.com" or "https://example.com/..."; returns null if invalid. */
export function normalizeWebsite(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  const candidate = trimmed.includes("://") ? trimmed : `https://${trimmed}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return null;
  }
  if (url.username || url.password || !url.hostname.includes(".")) {
    return null;
  }
  return `${url.protocol}//${url.host}`;
}

export function validateLeadForm(input: LeadFormInput): LeadValidation {
  const errors: LeadValidation["errors"] = {};
  const company = input.companyName.trim();
  const contact = input.contactName.trim();
  const email = input.email.trim();

  if (!company) {
    errors.companyName = "Enter your business name.";
  } else if (company.length > LEAD_LIMITS.companyName) {
    errors.companyName = `Use at most ${LEAD_LIMITS.companyName} characters.`;
  }
  if (!contact) {
    errors.contactName = "Enter your name.";
  } else if (contact.length > LEAD_LIMITS.contactName) {
    errors.contactName = `Use at most ${LEAD_LIMITS.contactName} characters.`;
  }
  if (!email) {
    errors.email = "Enter your work email.";
  } else if (email.length > LEAD_LIMITS.email || !EMAIL_PATTERN.test(email)) {
    errors.email = "Enter a valid email address, for example name@company.com.";
  }
  if (input.website.trim()) {
    const website = normalizeWebsite(input.website);
    if (website === null || input.website.trim().length > LEAD_LIMITS.website) {
      errors.website = "Enter a website address such as yourbusiness.com.";
    }
  }
  if (!INDUSTRY_VALUES.has(input.industry)) {
    errors.industry = "Choose the option closest to your business.";
  }
  if (input.inquiry.length > LEAD_LIMITS.inquiry) {
    errors.inquiry = `Keep this under ${LEAD_LIMITS.inquiry} characters.`;
  }
  if (!input.consent) {
    errors.consent = "Please confirm we may contact you about this request.";
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

/** Wire payload for POST /public/leads (snake_case, honeypot included). */
export function toLeadPayload(input: LeadFormInput): Record<string, unknown> {
  const website = normalizeWebsite(input.website);
  return {
    company_name: input.companyName.trim(),
    contact_name: input.contactName.trim(),
    email: input.email.trim(),
    website: website ? website : null,
    industry: input.industry,
    inquiry: input.inquiry.trim() ? input.inquiry.trim() : null,
    consent: input.consent,
    company_fax: input.companyFax ? input.companyFax : null,
  };
}

export type LeadSubmitOutcome =
  | { kind: "received"; message: string }
  | { kind: "closed" }
  | { kind: "rate_limited" }
  | { kind: "invalid"; message: string; fieldErrors: Partial<Record<LeadField, string>> }
  | { kind: "error" };

const WIRE_FIELDS: Record<string, LeadField> = {
  company_name: "companyName",
  contact_name: "contactName",
  email: "email",
  website: "website",
  industry: "industry",
  inquiry: "inquiry",
  consent: "consent",
};

const FIELD_MESSAGES: Record<LeadField, string> = {
  companyName: "Check your business name.",
  contactName: "Check your name.",
  email: "This email address cannot receive mail. Use your work email address.",
  website: "Enter a website address such as yourbusiness.com.",
  industry: "Choose the option closest to your business.",
  inquiry: "Shorten your message and try again.",
  consent: "Please confirm we may contact you about this request.",
};

/** Map FastAPI 422 details (loc: ["body", "<field>"]) onto form fields. */
export function fieldErrorsFromDetail(detail: unknown): Partial<Record<LeadField, string>> {
  const errors: Partial<Record<LeadField, string>> = {};
  if (!Array.isArray(detail)) {
    return errors;
  }
  for (const item of detail) {
    const loc = item && typeof item === "object" ? (item as { loc?: unknown }).loc : undefined;
    const wire = Array.isArray(loc) ? loc[loc.length - 1] : undefined;
    const field = typeof wire === "string" ? WIRE_FIELDS[wire] : undefined;
    if (field && !errors[field]) {
      errors[field] = FIELD_MESSAGES[field];
    }
  }
  return errors;
}

/** Map the backend response onto what the visitor should see. */
export async function interpretLeadResponse(
  response: Response,
): Promise<LeadSubmitOutcome> {
  if (response.status === 202) {
    try {
      const body = (await response.json()) as { message?: unknown };
      return {
        kind: "received",
        message:
          typeof body.message === "string"
            ? body.message
            : "Thanks - your request has been received.",
      };
    } catch {
      return { kind: "received", message: "Thanks - your request has been received." };
    }
  }
  if (response.status === 503) {
    return { kind: "closed" };
  }
  if (response.status === 429) {
    return { kind: "rate_limited" };
  }
  if (response.status === 422 || response.status === 400) {
    let fieldErrors: Partial<Record<LeadField, string>> = {};
    try {
      fieldErrors = fieldErrorsFromDetail(((await response.json()) as { detail?: unknown }).detail);
    } catch {
      fieldErrors = {};
    }
    return {
      kind: "invalid",
      message:
        Object.keys(fieldErrors).length > 0
          ? "Some details could not be accepted. Check the highlighted fields and try again."
          : "Some details could not be accepted. Please review the form and try again.",
      fieldErrors,
    };
  }
  return { kind: "error" };
}
