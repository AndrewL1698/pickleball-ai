/**
 * Guard against the hand-written types in lib/types.ts drifting from the API.
 *
 * Types are written by hand because the contract is small; the cost of that
 * choice is that nothing stops the backend changing underneath. This script
 * pays it back: it reads the OpenAPI schema the running API serves and
 * compares the field names and enum values against what lib/types.ts declares.
 *
 *   npm run check:contract                 # against http://localhost:8000
 *   API=http://127.0.0.1:8123 npm run check:contract
 *
 * It needs the API running, so it is not part of `npm test`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const base = (process.env.API ?? "http://localhost:8000").replace(/\/+$/, "");

/** Field names declared for one interface in lib/types.ts. */
function declaredFields(source, interfaceName) {
  const match = source.match(
    new RegExp(`export interface ${interfaceName}[^{]*\\{([\\s\\S]*?)\\n\\}`),
  );
  if (!match) throw new Error(`lib/types.ts has no interface ${interfaceName}`);
  return new Set(
    [...match[1].matchAll(/^\s*(\w+)\??:/gm)].map((line) => line[1]),
  );
}

/** Values declared for one `as const` array in lib/types.ts. */
function declaredValues(source, constName) {
  const match = source.match(new RegExp(`${constName} = \\[([\\s\\S]*?)\\] as const`));
  if (!match) throw new Error(`lib/types.ts has no ${constName}`);
  return new Set([...match[1].matchAll(/"([^"]+)"/g)].map((value) => value[1]));
}

function compare(label, expected, actual, problems) {
  const missing = [...expected].filter((name) => !actual.has(name));
  const extra = [...actual].filter((name) => !expected.has(name));
  if (missing.length) problems.push(`${label}: the API has ${missing.join(", ")}, the types do not`);
  if (extra.length) problems.push(`${label}: the types have ${extra.join(", ")}, the API does not`);
}

const source = readFileSync(join(here, "..", "lib", "types.ts"), "utf8");

let schema;
try {
  const response = await fetch(`${base}/openapi.json`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  schema = await response.json();
} catch (cause) {
  console.error(`Could not read ${base}/openapi.json (${cause.message}).`);
  console.error("Start the API first:  uv run pbapi");
  process.exit(2);
}

const components = schema.components.schemas;
const problems = [];

for (const [apiName, typeName] of [
  ["JobRead", "Job"],
  ["VideoRead", "Video"],
  ["MatchSummary", "MatchSummary"],
  ["MatchList", "MatchList"],
]) {
  compare(
    typeName,
    new Set(Object.keys(components[apiName].properties)),
    declaredFields(source, typeName),
    problems,
  );
}

// MatchDetail extends MatchSummary, so its own declaration lists only the extra keys.
compare(
  "MatchDetail",
  new Set(Object.keys(components.MatchDetail.properties)),
  new Set([...declaredFields(source, "MatchSummary"), ...declaredFields(source, "MatchDetail")]),
  problems,
);

// The primary collection. A route the client calls that the API does not
// serve is drift too, and the one a field comparison cannot see.
for (const path of ["/api/matches", "/api/matches/{match_id}", "/api/jobs/{job_id}"]) {
  if (!(path in schema.paths)) problems.push(`the API does not serve ${path}`);
}

compare("JobStatus", new Set(components.JobStatus.enum), declaredValues(source, "JOB_STATUSES"), problems);
compare("JobStage", new Set(components.JobStage.enum), declaredValues(source, "JOB_STAGES"), problems);
compare(
  "MatchStatus",
  new Set(components.MatchStatus.enum),
  declaredValues(source, "MATCH_STATUSES"),
  problems,
);

if (problems.length > 0) {
  console.error("lib/types.ts has drifted from the API:\n");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log(`lib/types.ts matches the API schema at ${base}.`);
