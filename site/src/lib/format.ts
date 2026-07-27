export const num = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US");

export const date = (d: string | null | undefined) => {
  if (!d) return "—";
  const [y, m, dd] = d.split("-").map(Number);
  const dt = new Date(Date.UTC(y, (m ?? 1) - 1, dd ?? 1));
  return dt.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: d.length > 7 ? "numeric" : undefined,
    timeZone: "UTC",
  });
};

export const monthLabel = (m: string) => {
  const [y, mm] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mm - 1)).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    timeZone: "UTC",
  });
};

export const TYPE_LABEL: Record<string, string> = {
  closure: "Closure",
  mass_layoff: "Mass layoff",
  unknown: "Unspecified",
};

export const STATE_NAMES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia",
  FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana",
  ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan",
  MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
  NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey",
  NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota",
  OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
  RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota",
  TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia",
  WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

// Legal-form and unit abbreviations keep their own casing when a shouted
// name is settled down; anything else short enough is taken for an
// initialism ("BAE", "HMS") and left alone.
const LEGAL_FORMS: Record<string, string> = {
  INC: "Inc.", "INC.": "Inc.", CORP: "Corp.", "CORP.": "Corp.", CO: "Co.",
  "CO.": "Co.", LTD: "Ltd.", "LTD.": "Ltd.", COMPANY: "Company",
  INCORPORATED: "Incorporated", CORPORATION: "Corporation", HOLDINGS: "Holdings",
  GROUP: "Group", SERVICES: "Services", SYSTEMS: "Systems",
};
const KEEP_UPPER = new Set(["LLC", "LLP", "LP", "PLC", "PC", "USA", "US", "NA", "AG", "SA", "NV", "BV"]);

/** Settle a shouted name into title case: states file "MERVYN'S LLC" and
 *  the SEC records "TEXTRON INC", but neither is how the company writes
 *  itself. Mixed-case names are left exactly as filed. */
export function displayName(name: string | null | undefined): string {
  if (!name) return "";
  if (/[a-z]/.test(name)) return name; // already mixed case — trust it
  return name
    .split(/\s+/)
    .map((word) => {
      const bare = word.replace(/[^A-Za-z.]/g, "");
      if (KEEP_UPPER.has(bare)) return word;
      if (LEGAL_FORMS[word]) return LEGAL_FORMS[word];
      if (bare.length <= 3 && !word.includes(".")) return word; // initialism
      // Capitalise after a separator, and after an apostrophe only where
      // it follows a single letter: O'Brien, but Mervyn's.
      return word
        .toLowerCase()
        .replace(/(^|[\s("\-\/])([a-z])/g, (_, sep, c) => sep + c.toUpperCase())
        .replace(/(^[A-Za-z])(['\u2019])([a-z])/g, (_, a, q, c) => a + q + c.toUpperCase());
    })
    .join(" ");
}
