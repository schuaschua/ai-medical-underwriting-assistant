import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "coverage"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: { globals: globals.browser },
    rules: {
      // coding-style.md rule 15: only the API client module calls the server.
      "no-restricted-globals": [
        "error",
        {
          name: "fetch",
          message: "Call the server through src/api/client.ts.",
        },
      ],
      // security.md rule 22: user and model text is never rendered as HTML.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Never render text as HTML (security.md rule 22).",
        },
      ],
    },
  },
  {
    files: ["src/api/client.ts"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
]);
