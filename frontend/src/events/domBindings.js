import { bindCapabilityEvents } from "./capabilityBindings.js";
import { bindCommandEvents } from "./commandBindings.js";
import { bindComposerEvents } from "./composerBindings.js";
import { bindEvidenceEvents } from "./evidenceBindings.js";
import { bindEphemeralDelegatedEvents } from "./ephemeralDelegatedBindings.js";
import { bindLayoutEvents } from "./layoutBindings.js";
import { bindWorkspaceEvents } from "./workspaceBindings.js";

export function bindDomEvents(context) {
  bindEphemeralDelegatedEvents(context);
  bindCommandEvents(context);
  bindLayoutEvents(context);
  bindWorkspaceEvents(context);
  bindEvidenceEvents(context);
  bindCapabilityEvents(context);
  bindComposerEvents(context);
}
