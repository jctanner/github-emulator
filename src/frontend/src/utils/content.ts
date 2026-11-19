import {api} from "../api/client";
import type {components} from "../api/schema";

type Content = components["schemas"]["ContentResponse"];

export function decodeBase64Content(value: string | null | undefined): string {
  if (!value) return "";
  const bytes = Uint8Array.from(atob(value.replaceAll("\n", "")), (char) =>
    char.charCodeAt(0),
  );
  return new TextDecoder().decode(bytes);
}

/** Base64 of ``bytes``, in chunks so a large file does not overflow the call stack. */
export function encodeBase64Bytes(bytes: Uint8Array): string {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/**
 * A file from the contents or readme API with its content present. For a file
 * of 1 MiB or more the API, like GitHub's, answers "encoding": "none" and an
 * empty "content"; the bytes are then read from the same contents URL in the
 * raw media type and filled in as base64.
 */
export async function withFileContent(
  owner: string,
  repo: string,
  ref: string,
  file: Content,
): Promise<Content> {
  if (file.type !== "file" || file.encoding !== "none") return file;
  const {data, response} = await api.GET(
    "/api/v3/repos/{owner}/{repo}/contents/{path}",
    {
      params: {path: {owner, repo, path: file.path}, query: {ref}},
      headers: {Accept: "application/vnd.github.raw"},
      parseAs: "arrayBuffer",
    },
  );
  if (!response.ok || !data) {
    throw new Error("Could not load file.");
  }
  return {
    ...file,
    encoding: "base64",
    content: encodeBase64Bytes(new Uint8Array(data)),
  };
}
