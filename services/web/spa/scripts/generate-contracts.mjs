// Turns the contracts JSON Schema into TypeScript types.
//
//   node scripts/generate-contracts.mjs           write src/api/contracts.gen.ts
//   node scripts/generate-contracts.mjs --check   exit 1 if that file is out of date
//
// The schema itself is written by the contracts package:
//   uv run python -m contracts.export_schema services/web/spa/src/api/contracts.schema.json
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";
import * as prettier from "prettier";

const schemaPath = fileURLToPath(
  new URL("../src/api/contracts.schema.json", import.meta.url),
);
const typesPath = fileURLToPath(
  new URL("../src/api/contracts.gen.ts", import.meta.url),
);

const BANNER = `/* Generated from contracts.schema.json by scripts/generate-contracts.mjs.
 * Do not edit: change packages/contracts, then run \`npm run contracts:generate\`. */`;

// pydantic titles every field. Left in, each title becomes its own type alias
// (`CaseId1`, `CaseId2`, ...), so titles go and fields keep their plain types.
function withoutTitles(node) {
  if (Array.isArray(node)) return node.map(withoutTitles);
  if (node === null || typeof node !== "object") return node;
  const result = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "title") continue;
    if (["properties", "patternProperties", "$defs"].includes(key)) {
      // Maps from a name to a schema: the names are kept as they are.
      result[key] = Object.fromEntries(
        Object.entries(value).map(([name, schema]) => [
          name,
          withoutTitles(schema),
        ]),
      );
    } else if (["enum", "const", "default", "required"].includes(key)) {
      result[key] = value;
    } else {
      result[key] = withoutTitles(value);
    }
  }
  return result;
}

async function render() {
  const source = JSON.parse(await readFile(schemaPath, "utf8"));
  const schema = { ...withoutTitles(source), title: source.title };
  // The root lists every definition as a property, so each one becomes an
  // exported type and \`Contracts\` maps a model name to its type.
  const root = {
    ...schema,
    type: "object",
    additionalProperties: false,
    required: Object.keys(schema.$defs),
    properties: Object.fromEntries(
      Object.keys(schema.$defs).map((name) => [
        name,
        { $ref: `#/$defs/${name}` },
      ]),
    ),
  };
  const types = await compile(root, schema.title, {
    bannerComment: BANNER,
    additionalProperties: false,
    format: false,
  });
  const options = await prettier.resolveConfig(typesPath);
  return prettier.format(types, { ...options, filepath: typesPath });
}

const rendered = await render();
if (process.argv.includes("--check")) {
  const current = await readFile(typesPath, "utf8").catch(() => null);
  if (current !== rendered) {
    console.error(
      "src/api/contracts.gen.ts is out of date with contracts.schema.json. Run `npm run contracts:generate`.",
    );
    process.exit(1);
  }
} else {
  await writeFile(typesPath, rendered, "utf8");
}
