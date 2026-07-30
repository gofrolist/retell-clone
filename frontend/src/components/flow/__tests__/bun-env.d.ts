// bun's test runner and `import.meta.dir` are not in the DOM/esnext libs the
// app compiles against, so `tsc --noEmit` cannot see them without this.
// Scoped to the test directory on purpose: a global `types` array in
// tsconfig.json would disable auto-inclusion of @types/node and @types/react,
// which Next needs.
/// <reference types="bun" />
