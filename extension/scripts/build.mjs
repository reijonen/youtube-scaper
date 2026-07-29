import * as esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

const entryPoints = ["capture", "collector", "service-worker"];

await esbuild.build({
  entryPoints: entryPoints.map((name) => path.join(root, "src", `${name}.ts`)),
  outdir: root,
  outbase: path.join(root, "src"),
  bundle: true,
  format: "iife",
  target: "chrome120",
  platform: "browser",
  logLevel: "info",
});
