import type { AgentEvent } from "@earendil-works/pi-agent-core";
import { toJsonValue } from "../json.ts";
import type { TraceEvent } from "../types.ts";

export class TraceCollector {
	private readonly events: TraceEvent[] = [];
	private sequence = 0;
	private readonly now: () => Date;

	constructor(now: () => Date = () => new Date()) {
		this.now = now;
	}

	record(type: string, payload: unknown): TraceEvent {
		const event: TraceEvent = {
			sequence: ++this.sequence,
			timestamp: this.now().toISOString(),
			type,
			payload: toJsonValue(payload),
		};
		this.events.push(event);
		return structuredClone(event);
	}

	recordAgentEvent(event: AgentEvent): void {
		if (event.type === "message_update") return;
		this.record(`agent.${event.type}`, event);
	}

	getEvents(): TraceEvent[] {
		return structuredClone(this.events);
	}
}
