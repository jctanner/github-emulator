export function decodeBase64Content(value: string | null | undefined): string {
  if (!value) return "";
  const bytes = Uint8Array.from(atob(value.replaceAll("\n", "")), (char) =>
    char.charCodeAt(0),
  );
  return new TextDecoder().decode(bytes);
}
