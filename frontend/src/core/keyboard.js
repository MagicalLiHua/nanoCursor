export function shouldSubmitOnEnter(event, isComposing = false) {
  const nativeEvent = event?.nativeEvent || {};
  const keyCode = event?.keyCode ?? nativeEvent.keyCode;

  if (event?.key !== "Enter") return false;
  if (event?.shiftKey) return false;
  if (isComposing || event?.isComposing || nativeEvent.isComposing) return false;
  if (keyCode === 229) return false;
  return true;
}
