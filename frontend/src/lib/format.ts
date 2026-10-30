// Display helpers. Numbers are formatted, never invented: every value comes from an event or an API payload.

const MODEL_NAMES: Record<string, string> = {
  "muse-spark-1.3": "Muse Spark 1.3",
  "muse-spark-1.3-contributor": "Muse Spark 1.3 (contributor)",
  "muse-wonder-1.0": "Muse Wonder 1.0",
  "meta-models/muse-spark-open-1026": "Muse Spark Open",
  "sam-3.1": "SAM 3.1",
  "classical (scikit-image)": "classical watershed (scikit-image)",
};

export const modelName = (id?: string | null) => (id ? MODEL_NAMES[id] ?? id : "unknown");

// Who serves the Scientist + Microscopist. The PI reviewer (and SAM) always run on the Meta Model API.
export const PROVIDER_LABEL: Record<string, string> = { meta: "Meta API", baseten: "Baseten" };
export const providerOf = (ids?: Record<string, string> | null) => ids?.provider ?? "meta"; // older runs: Meta only
export const isOpenWeights = (ids?: Record<string, string> | null) => providerOf(ids) !== "meta";
export const openWeightsLabel = (ids?: Record<string, string> | null) =>
  `Open weights · ${modelName(ids?.scientist)} via ${PROVIDER_LABEL[providerOf(ids)] ?? providerOf(ids)}`;

export const TOOL_NAMES: Record<string, string> = {
  list_available_experiments: "List available experiments",
  acquire_well: "Acquire well",
  describe_phenotype: "Describe phenotype",
  compare_to_reference_atlas: "Compare to reference atlas",
  dose_response: "Dose response",
  update_hypotheses: "Update hypotheses",
  submit_conclusion: "Submit conclusion",
  lookup_compound: "Look up compound",
  search_literature: "Search literature",
};

export const toolName = (name: string) => TOOL_NAMES[name] ?? name;

export const FEATURE_NAMES: Record<string, string> = {
  nuc_area: "Nucleus area",
  nuc_dna_integrated: "DNA content (integrated)",
  nuc_dna_mean: "DNA intensity (mean)",
  nuc_eccentricity: "Nucleus eccentricity",
  nuc_solidity: "Nucleus solidity",
  nuc_form_factor: "Nucleus form factor",
  nuc_major_axis: "Nucleus length",
  cell_area: "Cell area",
  cell_nuc_area_ratio: "Cell / nucleus area",
  cell_roundness: "Cell roundness",
  cell_eccentricity: "Cell eccentricity",
  cell_solidity: "Cell solidity",
  cell_major_axis: "Cell length",
  tub_mean: "Tubulin intensity",
  tub_std: "Tubulin variability",
  tub_glcm_contrast: "Tubulin texture",
  act_mean: "Actin intensity",
  act_std: "Actin variability",
  act_glcm_contrast: "Actin texture",
  act_tub_ratio: "Actin / tubulin",
  cell_count: "Cell count",
  frac_mitotic_like: "Mitotic-like fraction",
  frac_large_nucleus: "Large-nucleus fraction",
  frac_multinucleate_proxy: "Clumped-nuclei fraction",
};

export const featureName = (f: string) => FEATURE_NAMES[f] ?? f;

export const usd = (x?: number | null) => (x == null ? "—" : `$${x.toFixed(2)}`);

export const signed = (x: number, digits = 1) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;

export const prob = (p: number) => p.toFixed(2);

export function uM(x: number): string {
  if (x >= 1) return `${+x.toPrecision(3)} µM`;
  if (x >= 0.001) return `${+(x * 1000).toPrecision(3)} nM`;
  return `${+(x * 1e6).toPrecision(3)} pM`;
}

export function dateTime(iso?: string | null): string {
  if (!iso) return "unknown date";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function dateOnly(iso?: string | null): string {
  if (!iso) return "unknown date";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export const shortId = (id: string) => (id.length > 10 ? `${id.slice(0, 8)}…` : id);

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
