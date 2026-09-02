import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import type { EvalResult } from "../types.ts";
import { renderMarkdownReport } from "./report.ts";

export async function writeResultArtifacts(results: EvalResult[], outputPath: string): Promise<string> {
	const resolved = resolve(outputPath);
	await mkdir(dirname(resolved), { recursive: true });
	await writeFile(resolved, `${JSON.stringify(results, null, 2)}\n`, "utf8");
	await writeFile(resolved.replace(/\.json$/i, ".md"), renderMarkdownReport(results), "utf8");
	return resolved;
}

export async function readResults(path: string): Promise<EvalResult[]> {
	const parsed: unknown = JSON.parse(await readFile(resolve(path), "utf8"));
	if (!Array.isArray(parsed)) throw new Error("Result artifact must contain a JSON array.");
	return parsed as EvalResult[];
}
