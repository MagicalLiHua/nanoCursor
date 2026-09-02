import { describe, expect, it } from "vitest";
import {
	IssueCreateOrAppendParameters,
	IssueLabelParameters,
	ReportConclusionParameters,
} from "../src/world/collaboration-tools.ts";

describe("collaboration issue-label schema", () => {
	it("publishes a finite project taxonomy to the model", () => {
		const serialized = JSON.stringify(IssueLabelParameters);

		expect(serialized).toContain('"const":"prompt-injection"');
		expect(serialized).toContain('"const":"security"');
		expect(serialized).toContain('"const":"network"');
		expect(serialized).toContain('"const":"reliability"');
		expect(serialized).toContain("Prompt-injection signatures use prompt-injection + security");
		expect(serialized).toContain("network transport/reset signatures use network + reliability");
	});

	it("requires unique labels from that taxonomy", () => {
		const labels = IssueCreateOrAppendParameters.properties.labels;
		const serialized = JSON.stringify(labels);

		expect(serialized).toContain('"uniqueItems":true');
		expect(serialized).toContain("Use only the project taxonomy");
	});

	it("defines when a comparative report must use REGRESSION_FOUND", () => {
		const serialized = JSON.stringify(ReportConclusionParameters);

		expect(serialized).toContain('"const":"REGRESSION_FOUND"');
		expect(serialized).toContain("previously passing case now fails");
		expect(serialized).toContain("even if the candidate run is also FAILED");
	});
});
