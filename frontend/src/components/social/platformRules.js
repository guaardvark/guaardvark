// frontend/src/components/social/platformRules.js
// Plain-words readings of a provider's declared capabilities (backend
// services/connections/base.py), shared by Social Accounts, Social Posts and
// the publish composer. Nothing here knows a platform by name.
import { translate, uiLocale } from "../../i18n";

// A list in the interface language's own words ("a, b and c" in English).
const listOf = (words, type) => {
  const locale = uiLocale();
  if (locale && typeof Intl.ListFormat === "function") {
    return new Intl.ListFormat(locale, { type }).format(words);
  }
  if (type === "disjunction") return words.join(" or ");
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
};

export const describeWindow = (seconds) => {
  if (!seconds) return "";
  if (seconds % 86400 === 0) {
    return seconds === 86400
      ? translate("social.platformRules.window.day")
      : translate("social.platformRules.window.days", { count: seconds / 86400 });
  }
  if (seconds % 3600 === 0) {
    return seconds === 3600
      ? translate("social.platformRules.window.hour")
      : translate("social.platformRules.window.hours", { count: seconds / 3600 });
  }
  return translate("social.platformRules.window.minutes", { count: Math.max(1, Math.round(seconds / 60)) });
};

export const describeRateLimit = (limit) => {
  const sentence = translate("social.platformRules.rateLimit", {
    posts: limit.posts,
    window: describeWindow(limit.window_seconds),
  });
  return limit.platform_limit
    ? `${sentence} ${translate("social.platformRules.platformAllows", { limit: limit.platform_limit })}`
    : sentence;
};

export const describeDisclosure = (rule) => {
  if (!rule) return "";
  const how =
    rule.mechanism === "platform_flag"
      ? translate("social.platformRules.disclosureFlag", { flag: rule.flag })
      : translate("social.platformRules.disclosureLine", { text: rule.text });
  if (rule.required_when === "generated_media") {
    return `${translate("social.platformRules.requiredGeneratedMedia")} ${rule.rule} ${how}`.trim();
  }
  if (rule.required_when === "ai_assisted") {
    return `${translate("social.platformRules.requiredAiAssisted")} ${rule.rule} ${how}`.trim();
  }
  return `${translate("social.platformRules.noPlatformRule")} ${how}`;
};

export const describeScheduling = (capabilities) => {
  if (capabilities?.schedule_refusal) return capabilities.schedule_refusal;
  const native = capabilities?.native_scheduling;
  if (!native) {
    return translate("social.platformRules.scheduledByGuaardvark");
  }
  const visibilities = native.visibilities?.length ? listOf(native.visibilities, "disjunction") : "";
  const lead = native.min_lead_seconds ? describeWindow(native.min_lead_seconds) : "";
  const options = { field: native.field, visibilities, window: lead };
  const held = visibilities
    ? lead
      ? translate("social.platformRules.heldByPlatformVisibilityLead", options)
      : translate("social.platformRules.heldByPlatformVisibility", options)
    : lead
      ? translate("social.platformRules.heldByPlatformLead", options)
      : translate("social.platformRules.heldByPlatform", options);
  return `${held} ${translate("social.platformRules.otherwiseGuaardvark")}`;
};

export const describeDelete = (capabilities) => {
  if (!capabilities?.deletable) return translate("social.platformRules.notDeletable");
  const windowSeconds = capabilities.delete_window_seconds;
  return windowSeconds
    ? translate("social.platformRules.deleteWindow", { window: describeWindow(windowSeconds) })
    : translate("social.platformRules.deleteAlways");
};

const statusLabels = () => ({
  awaiting_approval: translate("social.platformRules.status.awaitingApproval"),
  scheduled: translate("social.platformRules.status.scheduled"),
  queued: translate("social.platformRules.status.queued"),
  processing: translate("social.platformRules.status.processing"),
  posted: translate("social.platformRules.status.posted"),
  failed: translate("social.platformRules.status.failed"),
  rejected: translate("social.platformRules.status.rejected"),
  cancelled: translate("social.platformRules.status.cancelled"),
  deleted: translate("social.platformRules.status.deleted"),
});

export const STATUS_TONE = {
  awaiting_approval: "warn",
  scheduled: "info",
  queued: "info",
  processing: "info",
  posted: "ok",
  failed: "error",
  rejected: "neutral",
  cancelled: "neutral",
  deleted: "neutral",
};

export const statusLabel = (record, platformLabel) => {
  if (record.status === "scheduled" && record.scheduled_natively) {
    return translate("social.platformRules.heldBy", { platform: platformLabel || record.platform });
  }
  return statusLabels()[record.status] || record.status;
};

/**
 * The strictest (smallest positive) value of a numeric capability across the
 * targets, or null when none declares one.
 */
export const strictestLimit = (specs, key) => {
  const limits = (specs || [])
    .map((s) => s?.capabilities?.[key])
    .filter((n) => typeof n === "number" && n > 0);
  return limits.length ? Math.min(...limits) : null;
};

/** The visibilities every target that declares any accepts. */
export const sharedVisibilities = (specs) => {
  const sets = (specs || [])
    .map((s) => s?.capabilities?.visibilities || [])
    .filter((v) => v.length);
  if (!sets.length) return [];
  return sets.reduce((acc, cur) => acc.filter((v) => cur.includes(v)));
};

/**
 * The visibility a post to this provider is sent with when the person picks
 * none: its declared default. The composer shows it selected and sends it.
 */
export const defaultVisibility = (spec) => spec?.capabilities?.default_visibility || "";

/**
 * Whether this connection may use the visibilities its provider holds back
 * (capabilities.public_opt_in), as the backend reports it on the connection.
 * A state that is missing counts as off, like on the backend.
 */
export const publicOptInOn = (connection) => connection?.public_opt_in?.on === true;

/**
 * The visibilities a post to this connection can take. One its provider holds
 * back until the connection's switch is on is offered disabled, with the
 * provider's reason, which also says where the switch is.
 * @returns {Array<{ value: string, disabled: boolean, reason: string }>}
 */
export const visibilityChoices = (spec, connection) => {
  const optIn = spec?.capabilities?.public_opt_in;
  const held = optIn && !publicOptInOn(connection) ? optIn.visibilities || [] : [];
  return (spec?.capabilities?.visibilities || []).map((value) => {
    const off = held.includes(value);
    return { value, disabled: off, reason: off ? optIn.refusal || "" : "" };
  });
};


/**
 * One line for the targets that offer a single visibility, which get no
 * select, e.g. "Public on Bluesky and Discord: the only visibility they offer."
 * @param {Array<{ label: string, value: string }>} targets
 */
export const fixedVisibilityNote = (targets) => {
  const byValue = new Map();
  for (const target of targets || []) {
    byValue.set(target.value, [...(byValue.get(target.value) || []), target.label]);
  }
  return [...byValue.entries()]
    .map(([value, labels]) => {
      const named = value ? `${value[0].toUpperCase()}${value.slice(1)}` : value;
      const options = { visibility: named, targets: listOf(labels, "conjunction") };
      return labels.length === 1
        ? translate("social.platformRules.fixedVisibilityOne", options)
        : translate("social.platformRules.fixedVisibilityMany", options);
    })
    .join(" ");
};

/**
 * The per-post fields the selected targets declare, merged by name: one input
 * serves every target that asks for it, required when any of them requires it.
 * @returns {Array<object & { for: string[] }>}
 */
export const mergePostFields = (specs) => {
  const byName = new Map();
  for (const spec of specs || []) {
    for (const field of spec?.post_fields || []) {
      const entry = byName.get(field.name) || { ...field, required: false, for: [] };
      entry.required = entry.required || Boolean(field.required);
      entry.for.push(spec.label || spec.provider);
      byName.set(field.name, entry);
    }
  }
  return [...byName.values()];
};

/**
 * Whether Delete on platform applies to this record now.
 * @returns {{ show: boolean, ok: boolean, reason: string }}
 */
export const deleteOnPlatform = (record, capabilities, now = Date.now()) => {
  const heldByPlatform = record.status === "scheduled" && record.scheduled_natively;
  if (!capabilities?.deletable || !record.remote_id || !(record.status === "posted" || heldByPlatform)) {
    return { show: false, ok: false, reason: "" };
  }
  const windowSeconds = capabilities.delete_window_seconds;
  if (windowSeconds && record.posted_at && !heldByPlatform) {
    const age = (now - new Date(record.posted_at).getTime()) / 1000;
    if (age > windowSeconds) {
      return {
        show: true,
        ok: false,
        reason: translate("social.platformRules.deleteWindowClosed", { window: describeWindow(windowSeconds) }),
      };
    }
  }
  return { show: true, ok: true, reason: "" };
};
