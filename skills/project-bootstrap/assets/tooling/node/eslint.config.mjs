// @ts-check
// ESLint flat config for TypeScript projects without Next.js (agent-framework
// assets/tooling/node). Next.js projects use the variant in README.md instead.
import eslint from "@eslint/js";
import prettier from "eslint-config-prettier/flat";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig(
  globalIgnores(["dist/", "build/", "coverage/"]),
  eslint.configs.recommended,
  tseslint.configs.recommended,
  // Last, so formatting rules that would fight Prettier stay off.
  prettier,
);
