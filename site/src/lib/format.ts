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
