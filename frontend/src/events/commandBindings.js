export function bindCommandEvents(context) {
  const {
    openCommandPalette,
    closeCommandPalette,
    setCommandQuery,
    filteredCommandItems,
    executeCommand,
  } = context;

  document.querySelector("[data-action='open-command-palette']")?.addEventListener("click", () => {
    openCommandPalette();
  });

  document.querySelectorAll("[data-action='close-command-palette']").forEach((element) => {
    element.addEventListener("click", (event) => {
      if (event.target === element || element.tagName === "BUTTON") {
        closeCommandPalette();
      }
    });
  });

  document.querySelector("#command-input")?.addEventListener("input", (event) => {
    setCommandQuery(event.target.value);
  });

  document.querySelector("#command-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeCommandPalette();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const first = filteredCommandItems()[0];
      if (first) executeCommand(first.id);
    }
  });

  document.querySelectorAll("[data-action='run-command']").forEach((button) => {
    button.addEventListener("click", async () => {
      await executeCommand(button.dataset.commandId);
    });
  });
}
