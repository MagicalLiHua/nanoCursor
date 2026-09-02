import type { JsonValue } from "./types.ts";

export function toJsonValue(value: unknown): JsonValue {
	if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
		return value;
	}
	if (typeof value === "undefined") return null;
	if (value instanceof Date) return value.toISOString();
	if (Array.isArray(value)) return value.map((item) => toJsonValue(item));
	if (typeof value === "object") {
		const record: { [key: string]: JsonValue } = {};
		for (const [key, item] of Object.entries(value)) {
			if (typeof item !== "undefined") record[key] = toJsonValue(item);
		}
		return record;
	}
	return String(value);
}
