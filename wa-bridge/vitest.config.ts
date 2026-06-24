import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.spec.ts"],
    environment: "node",
    globals: false,
    reporters: ["default"],
    testTimeout: 10_000,
    hookTimeout: 10_000,
  },
});
