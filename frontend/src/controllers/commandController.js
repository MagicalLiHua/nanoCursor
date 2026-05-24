import { filterCommandItems } from "../ui/commands.js";
import { renderCommandPalette as renderCommandPaletteView } from "../ui/commandPalette.js";
import { executeCommand as executeCommandAction } from "../services/commandExecutor.js";

export function createCommandController({
  state,
  ensureUiState,
  render,
  getExecutionContext,
}) {
  function openCommandPalette(query = "") {
    const ui = ensureUiState();
    ui.commandPaletteOpen = true;
    ui.commandQuery = query;
    render();
    requestAnimationFrame(() => document.querySelector("#command-input")?.focus());
  }

  function closeCommandPalette() {
    const ui = ensureUiState();
    ui.commandPaletteOpen = false;
    ui.commandQuery = "";
    render();
  }

  function setCommandQuery(query) {
    ensureUiState().commandQuery = query;
    render();
  }

  function filteredCommandItems() {
    return filterCommandItems(ensureUiState().commandQuery);
  }

  function renderCommandPalette() {
    return renderCommandPaletteView({
      ui: ensureUiState(),
      commands: filteredCommandItems(),
    });
  }

  async function executeCommand(commandId) {
    await executeCommandAction(commandId, {
      state,
      ensureUiState,
      ...getExecutionContext(),
    });
  }

  return {
    closeCommandPalette,
    executeCommand,
    filteredCommandItems,
    openCommandPalette,
    renderCommandPalette,
    setCommandQuery,
  };
}
