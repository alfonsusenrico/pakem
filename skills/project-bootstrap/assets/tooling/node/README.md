# Node and TypeScript tooling

Default formatter, linter, and type-check settings for JavaScript and TypeScript projects
(Working Standard Profile, sections 7 and 10). The CI templates in the release-delivery skill (`assets/ci/`) call the
npm scripts below, so every project defines all of them.

## Adopt

1. Copy `.prettierrc.json` (an empty object: Prettier defaults are the standard),
   `.prettierignore`, and `eslint.config.mjs` to the project root, and `../.editorconfig`
   if the project has none.
2. Install project-local development dependencies (lockfile updated):

   ```sh
   npm install --save-dev prettier eslint @eslint/js typescript-eslint eslint-config-prettier typescript@~6.0
   ```

   `typescript-eslint` 8.71 supports TypeScript `>=4.8.4 <6.1.0` (checked 2026-10-07), so
   pin TypeScript 6.0 until its peer range includes 7.x.
3. Add the scripts and run each once before enabling CI.

## Scripts

```json
{
  "scripts": {
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "build": "tsc"
  }
}
```

`test` and `build` are project choices (for example `node --test`, `next build`, `vite build`);
the other four are the same everywhere. A script that does not apply must fail loudly or be
removed from the CI call, never succeed as a no-op.

## Next.js variant

Next.js 16 removed `next lint`; lint with the ESLint CLI and extend Next's flat configs instead
of `typescript-eslint` directly (`eslint-config-next/typescript` already includes its
recommended rules). Install `eslint eslint-config-next eslint-config-prettier` and use:

```js
// eslint.config.mjs
import prettier from "eslint-config-prettier/flat";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  prettier,
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);
```

Source: <https://nextjs.org/docs/app/api-reference/config/eslint> (Next.js 16.4, read
2026-10-07). Keep `"typecheck": "tsc --noEmit"` so type errors fail CI before `next build`.

## Plain TypeScript variant

`eslint.config.mjs` in this folder: `@eslint/js` recommended, `typescript-eslint`
recommended, then `eslint-config-prettier/flat` last so stylistic rules never fight Prettier.
Add type-aware rules (`tseslint.configs.recommendedTypeChecked` with `parserOptions.projectService`)
when the project wants them; they are slower and need a `tsconfig.json` covering every linted
file.
