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

/** NAICS sector labels, mirroring warnlive/enrich/industry.py. A four-digit
 *  code has no title in any reference file we carry, but its sector does,
 *  and a sector beats printing a bare number beside an em-dash. */
export const SECTOR_LABELS: Record<string, string> = {
  "11": "Agriculture, forestry, fishing and hunting",
  "21": "Mining, quarrying, oil and gas",
  "22": "Utilities",
  "23": "Construction",
  "31-33": "Manufacturing",
  "42": "Wholesale trade",
  "44-45": "Retail trade",
  "48-49": "Transportation and warehousing",
  "51": "Information",
  "52": "Finance and insurance",
  "53": "Real estate, rental and leasing",
  "54": "Professional, scientific and technical services",
  "55": "Management of companies",
  "56": "Administrative, support and waste services",
  "61": "Educational services",
  "62": "Health care and social assistance",
  "71": "Arts, entertainment and recreation",
  "72": "Accommodation and food services",
  "81": "Other services",
  "92": "Public administration",
};

/** The sector a NAICS code belongs to, or null. Ranges ("31-33") are spelled
 *  as themselves; anything else is read from its first two digits. */
export function sectorLabel(naics: string | null | undefined): string | null {
  if (!naics) return null;
  if (SECTOR_LABELS[naics]) return SECTOR_LABELS[naics];
  const two = naics.slice(0, 2);
  for (const [sector, label] of Object.entries(SECTOR_LABELS)) {
    if (sector === two) return label;
    if (sector.includes("-")) {
      const [lo, hi] = sector.split("-").map(Number);
      const n = Number(two);
      if (!Number.isNaN(n) && n >= lo && n <= hi) return label;
    }
  }
  return null;
}

/** How an industry code was arrived at, in words. The bases are pipeline
 *  vocabulary; a reader needs to know whether a state published this or we
 *  inferred it. */
export const NAICS_BASIS_LABEL: Record<string, string> = {
  source: "as published by the state",
  "sector-name": "from the sector the state named",
  "sic-crosswalk": "from the state's SIC code",
  "sec-sic": "from the SEC's industry for this filer",
  ntee: "from the IRS activity code",
  "parent-sic": "from the corporate parent's SEC industry",
  adjudicated: "inferred from the employer's name",
  employer: "from this employer's other notices",
};

/** Whether a code describes the site that filed or the whole company. */
export const NAICS_LEVEL_LABEL: Record<string, string> = {
  establishment: "this establishment",
  enterprise: "the whole company",
};
