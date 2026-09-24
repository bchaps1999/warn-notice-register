// CSS variables let SVG fills and legends follow theme changes immediately.
export const RAMP = Array.from({ length: 6 }, (_, i) => `var(--map-ramp-${i + 1})`);
