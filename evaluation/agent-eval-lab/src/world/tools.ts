import type { AgentTool, AgentToolResult } from "@earendil-works/pi-agent-core";
import { Type } from "@earendil-works/pi-ai";
import { toJsonValue } from "../json.ts";
import type { JsonValue } from "../types.ts";
import type { WorldSandbox } from "./sandbox.ts";

const WeatherParameters = Type.Object({
	location: Type.String({ minLength: 1 }),
	date: Type.String({ minLength: 10 }),
});
const ContactsParameters = Type.Object({ query: Type.String({ minLength: 1 }) });
const CalendarListParameters = Type.Object({
	start: Type.String({ minLength: 1 }),
	end: Type.String({ minLength: 1 }),
});
const CalendarCreateParameters = Type.Object({
	title: Type.String({ minLength: 1 }),
	start: Type.String({ minLength: 1 }),
	end: Type.String({ minLength: 1 }),
	attendee_emails: Type.Optional(Type.Array(Type.String())),
	location: Type.Optional(Type.String()),
});
const NoteCreateParameters = Type.Object({
	title: Type.String({ minLength: 1 }),
	body: Type.String({ minLength: 1 }),
});
const NotificationParameters = Type.Object({
	recipient: Type.String({ minLength: 1 }),
	message: Type.String({ minLength: 1 }),
});

function textResult(details: JsonValue): AgentToolResult<JsonValue> {
	return { content: [{ type: "text", text: JSON.stringify(details) }], details };
}

function beforeExecute(
	sandbox: WorldSandbox,
	toolCallId: string,
	toolName: string,
	args: unknown,
	signal?: AbortSignal,
): AgentToolResult<JsonValue> | undefined {
	signal?.throwIfAborted();
	const invocation = sandbox.nextInvocation(toolCallId, toolName, toJsonValue(args));
	const fault = sandbox.applyFault(invocation);
	if (fault?.mode === "empty_result") return textResult(null);
	if (fault?.mode === "malformed_result") {
		return { content: [{ type: "text", text: "{malformed-result" }], details: "{malformed-result" };
	}
	return undefined;
}

export function createWorldTools(sandbox: WorldSandbox): AgentTool[] {
	const weather: AgentTool<typeof WeatherParameters, JsonValue> = {
		name: "weather_get",
		label: "Get weather",
		description: "Read the deterministic weather forecast for a location and date.",
		parameters: WeatherParameters,
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "weather_get", params, signal);
			if (injected) return injected;
			return textResult(toJsonValue(sandbox.getWeather(params.location, params.date) ?? null));
		},
	};
	const contacts: AgentTool<typeof ContactsParameters, JsonValue> = {
		name: "contacts_search",
		label: "Search contacts",
		description: "Search contacts by name, email, or tag.",
		parameters: ContactsParameters,
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "contacts_search", params, signal);
			if (injected) return injected;
			return textResult(toJsonValue(sandbox.findContacts(params.query)));
		},
	};
	const calendarList: AgentTool<typeof CalendarListParameters, JsonValue> = {
		name: "calendar_list",
		label: "List calendar",
		description: "List calendar events overlapping a time interval.",
		parameters: CalendarListParameters,
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "calendar_list", params, signal);
			if (injected) return injected;
			return textResult(toJsonValue(sandbox.listCalendar(params.start, params.end)));
		},
	};
	const calendarCreate: AgentTool<typeof CalendarCreateParameters, JsonValue> = {
		name: "calendar_create",
		label: "Create calendar event",
		description: "Create a calendar event in the isolated task world.",
		parameters: CalendarCreateParameters,
		executionMode: "sequential",
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "calendar_create", params, signal);
			if (injected) return injected;
			return textResult(
				toJsonValue(
					sandbox.createCalendarEvent({
						title: params.title,
						start: params.start,
						end: params.end,
						attendeeEmails: params.attendee_emails ?? [],
						...(params.location ? { location: params.location } : {}),
					}),
				),
			);
		},
	};
	const noteCreate: AgentTool<typeof NoteCreateParameters, JsonValue> = {
		name: "notes_create",
		label: "Create note",
		description: "Create a note in the isolated task world.",
		parameters: NoteCreateParameters,
		executionMode: "sequential",
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "notes_create", params, signal);
			if (injected) return injected;
			return textResult(toJsonValue(sandbox.createNote(params.title, params.body)));
		},
	};
	const notification: AgentTool<typeof NotificationParameters, JsonValue> = {
		name: "notification_send",
		label: "Send notification",
		description: "Send a notification in the isolated task world.",
		parameters: NotificationParameters,
		executionMode: "sequential",
		async execute(toolCallId, params, signal) {
			const injected = beforeExecute(sandbox, toolCallId, "notification_send", params, signal);
			if (injected) return injected;
			return textResult(toJsonValue(sandbox.sendNotification(params.recipient, params.message)));
		},
	};
	return [weather, contacts, calendarList, calendarCreate, noteCreate, notification];
}
